"""Phase 4 tests: dispatch scheduler wiring and due-device dispatcher."""

from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

from bson import ObjectId

from utils.utc import utc_now


class TestDispatcherIntervalHelper(unittest.TestCase):
    def test_default_and_clamps(self):
        from services.settings_service import get_monitor_dispatcher_interval_seconds

        with patch("services.settings_service.os.getenv", return_value="5"):
            self.assertEqual(get_monitor_dispatcher_interval_seconds(), 5)

        with patch("services.settings_service.os.getenv", return_value="0"):
            self.assertEqual(get_monitor_dispatcher_interval_seconds(), 1)

        with patch("services.settings_service.os.getenv", return_value="99"):
            self.assertEqual(get_monitor_dispatcher_interval_seconds(), 15)

        with patch("services.settings_service.os.getenv", return_value="nope"):
            self.assertEqual(get_monitor_dispatcher_interval_seconds(), 5)


class TestRegisterDeviceMonitorJob(unittest.TestCase):
    def test_legacy_mode_schedules_monitor_all_devices_with_ping_interval(self):
        import scheduler as sched

        mock_sched = MagicMock()
        mock_sched.running = False

        with (
            patch.object(sched, "scheduler", mock_sched),
            patch.object(sched, "get_monitor_runtime_mode", return_value="legacy"),
            patch.object(sched, "get_settings", return_value={"pingInterval": 30}),
            patch.object(sched, "_start_nmap_job"),
            patch.object(sched, "_start_interface_job"),
            patch.object(sched, "_start_interface_stats_job"),
            patch.object(sched, "_start_recovery_job"),
            patch.object(sched, "_start_retention_job"),
            patch.object(sched, "_register_isp_monitor_job"),
        ):
            sched.start_scheduler()

        device_calls = [
            c
            for c in mock_sched.add_job.call_args_list
            if c.kwargs.get("id") == sched.JOB_ID
            or (c.kwargs.get("id") is None and False)
        ]
        # start_scheduler → _register_device_monitor_job → add_job
        self.assertTrue(mock_sched.add_job.called)
        first = mock_sched.add_job.call_args_list[0]
        self.assertIs(first.kwargs["func"], sched.monitor_all_devices)
        self.assertEqual(first.kwargs["seconds"], 30)
        self.assertEqual(first.kwargs["max_instances"], 1)
        self.assertTrue(first.kwargs["coalesce"])
        self.assertEqual(first.kwargs["id"], sched.JOB_ID)

    def test_dispatch_mode_schedules_dispatcher_not_ping_interval(self):
        import scheduler as sched

        mock_sched = MagicMock()
        mock_sched.running = False

        with (
            patch.object(sched, "scheduler", mock_sched),
            patch.object(sched, "get_monitor_runtime_mode", return_value="dispatch"),
            patch.object(sched, "get_settings", return_value={"pingInterval": 30}),
            patch.object(
                sched, "get_monitor_dispatcher_interval_seconds", return_value=5
            ),
            patch("services.monitor_runtime.start_monitor_runtime") as start_rt,
            patch.object(sched, "_start_nmap_job"),
            patch.object(sched, "_start_interface_job"),
            patch.object(sched, "_start_interface_stats_job"),
            patch.object(sched, "_start_recovery_job"),
            patch.object(sched, "_start_retention_job"),
            patch.object(sched, "_register_isp_monitor_job"),
        ):
            sched.start_scheduler()

        start_rt.assert_called()
        first = mock_sched.add_job.call_args_list[0]
        self.assertIs(first.kwargs["func"], sched.dispatch_monitor_due_devices)
        self.assertEqual(first.kwargs["seconds"], 5)
        self.assertNotEqual(first.kwargs["seconds"], 30)
        self.assertEqual(first.kwargs["max_instances"], 1)
        self.assertTrue(first.kwargs["coalesce"])

    def test_reschedule_legacy_updates_job_period(self):
        import scheduler as sched

        mock_sched = MagicMock()
        mock_sched.running = True

        with (
            patch.object(sched, "scheduler", mock_sched),
            patch.object(sched, "get_monitor_runtime_mode", return_value="legacy"),
            patch.object(sched, "_register_isp_monitor_job") as isp,
        ):
            sched.reschedule_monitor_job(45)

        mock_sched.add_job.assert_called()
        kwargs = mock_sched.add_job.call_args.kwargs
        self.assertIs(kwargs["func"], sched.monitor_all_devices)
        self.assertEqual(kwargs["seconds"], 45)
        isp.assert_called_once_with(45)

    def test_reschedule_dispatch_does_not_rebind_device_job_to_ping_interval(self):
        import scheduler as sched

        mock_sched = MagicMock()
        mock_sched.running = True
        mock_job = MagicMock()
        mock_job.trigger.interval = timedelta(seconds=5)
        mock_sched.get_job.return_value = mock_job

        with (
            patch.object(sched, "scheduler", mock_sched),
            patch.object(sched, "get_monitor_runtime_mode", return_value="dispatch"),
            patch.object(
                sched, "get_monitor_dispatcher_interval_seconds", return_value=5
            ),
            patch.object(sched, "_register_isp_monitor_job") as isp,
        ):
            sched.reschedule_monitor_job(45)

        # Device job must not be re-added with pingInterval seconds.
        for call in mock_sched.add_job.call_args_list:
            self.assertNotEqual(call.kwargs.get("id"), sched.JOB_ID)
        isp.assert_called_once_with(45)


class TestDispatchMonitorDueDevices(unittest.TestCase):
    def test_skips_when_not_leader(self):
        from services.monitor_dispatch import dispatch_monitor_due_devices

        with (
            patch(
                "services.monitor_dispatch.require_scheduler_leadership",
                return_value=False,
            ),
            patch(
                "services.monitor_dispatch.signal_monitor_runtime_leadership_lost"
            ) as signal,
        ):
            result = dispatch_monitor_due_devices()
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "not_leader")
        signal.assert_called()

    def test_claims_due_and_submits_up_to_capacity(self):
        from services.monitor_dispatch import dispatch_monitor_due_devices

        now = utc_now()
        d1 = {
            "_id": ObjectId(),
            "hostname": "a",
            "ipAddress": "10.0.0.1",
            "monitor": True,
            "nextCheckAt": now - timedelta(seconds=1),
        }
        d2 = {
            "_id": ObjectId(),
            "hostname": "b",
            "ipAddress": "10.0.0.2",
            "monitor": True,
            "nextCheckAt": now - timedelta(seconds=2),
        }

        claimed_docs = []

        def fake_claim(device_id, device=None, now=None):
            doc = {
                **(device or {}),
                "_id": device_id,
                "scanClaimId": f"claim-{device_id}",
            }
            claimed_docs.append(device_id)
            return doc

        submitted = []

        def fake_submit(device, claim_id, suppress_offline=False, cycle_id=None, **_kwargs):
            submitted.append(claim_id)
            return True

        guard = MagicMock()
        guard.ensure.return_value = True
        fake_db = MagicMock()
        fake_db.devices.find.return_value.sort.return_value.limit.return_value = [
            d1,
            d2,
        ]

        with (
            patch(
                "services.monitor_dispatch.require_scheduler_leadership",
                return_value=True,
            ),
            patch(
                "services.monitor_dispatch.start_monitor_runtime",
                return_value=MagicMock(),
            ),
            patch(
                "services.monitor_dispatch.CycleLeadershipGuard",
                return_value=guard,
            ),
            patch(
                "services.monitor_dispatch.begin_cycle_connectivity_check",
                return_value=False,
            ),
            patch(
                "services.monitor_dispatch.free_worker_capacity",
                side_effect=[2, 2, 1, 0],
            ),
            patch("services.monitor_dispatch._db", return_value=fake_db),
            patch("services.monitor_dispatch.claim_device", side_effect=fake_claim),
            patch(
                "services.monitor_dispatch.submit_claimed_device",
                side_effect=fake_submit,
            ),
            patch("services.monitor_dispatch._maybe_run_integrity_audit"),
        ):
            result = dispatch_monitor_due_devices()

        self.assertEqual(result["candidates"], 2)
        self.assertEqual(result["claimed"], 2)
        self.assertEqual(result["submitted"], 2)
        self.assertEqual(len(claimed_docs), 2)
        limit_call = fake_db.devices.find.return_value.sort.return_value.limit
        limit_call.assert_called_with(2)

    def test_no_capacity_claims_nothing(self):
        from services.monitor_dispatch import dispatch_monitor_due_devices

        guard = MagicMock()
        guard.ensure.return_value = True
        fake_db = MagicMock()

        with (
            patch(
                "services.monitor_dispatch.require_scheduler_leadership",
                return_value=True,
            ),
            patch(
                "services.monitor_dispatch.start_monitor_runtime",
                return_value=MagicMock(),
            ),
            patch(
                "services.monitor_dispatch.CycleLeadershipGuard",
                return_value=guard,
            ),
            patch(
                "services.monitor_dispatch.begin_cycle_connectivity_check",
                return_value=False,
            ),
            patch(
                "services.monitor_dispatch.free_worker_capacity",
                return_value=0,
            ),
            patch("services.monitor_dispatch._db", return_value=fake_db),
            patch("services.monitor_dispatch.claim_device") as claim,
            patch("services.monitor_dispatch._maybe_run_integrity_audit"),
        ):
            result = dispatch_monitor_due_devices()

        claim.assert_not_called()
        fake_db.devices.find.assert_not_called()
        self.assertEqual(result["reason"], "no_capacity")

    def test_leadership_loss_signals_runtime_and_stops_claiming(self):
        from services.monitor_dispatch import dispatch_monitor_due_devices

        now = utc_now()
        devices = [
            {
                "_id": ObjectId(),
                "hostname": "a",
                "ipAddress": "10.0.0.1",
                "monitor": True,
                "nextCheckAt": now - timedelta(seconds=1),
            },
            {
                "_id": ObjectId(),
                "hostname": "b",
                "ipAddress": "10.0.0.2",
                "monitor": True,
                "nextCheckAt": now - timedelta(seconds=1),
            },
        ]

        guard = MagicMock()
        # start ok, then fail on first claim loop ensure
        guard.ensure.side_effect = [True, False]

        fake_db = MagicMock()
        fake_db.devices.find.return_value.sort.return_value.limit.return_value = devices

        with (
            patch(
                "services.monitor_dispatch.require_scheduler_leadership",
                return_value=True,
            ),
            patch(
                "services.monitor_dispatch.start_monitor_runtime",
                return_value=MagicMock(),
            ),
            patch(
                "services.monitor_dispatch.CycleLeadershipGuard",
                return_value=guard,
            ),
            patch(
                "services.monitor_dispatch.begin_cycle_connectivity_check",
                return_value=False,
            ),
            patch(
                "services.monitor_dispatch.free_worker_capacity",
                return_value=2,
            ),
            patch("services.monitor_dispatch._db", return_value=fake_db),
            patch("services.monitor_dispatch.claim_device") as claim,
            patch(
                "services.monitor_dispatch.signal_monitor_runtime_leadership_lost"
            ) as signal,
            patch("services.monitor_dispatch._maybe_run_integrity_audit") as integrity,
        ):
            result = dispatch_monitor_due_devices()

        claim.assert_not_called()
        signal.assert_called()
        self.assertTrue(result["aborted"])
        integrity.assert_not_called()

    def test_claim_conflict_does_not_submit(self):
        from services.monitor_dispatch import dispatch_monitor_due_devices

        now = utc_now()
        d1 = {
            "_id": ObjectId(),
            "hostname": "a",
            "ipAddress": "10.0.0.1",
            "monitor": True,
            "nextCheckAt": now - timedelta(seconds=1),
        }
        guard = MagicMock()
        guard.ensure.return_value = True
        fake_db = MagicMock()
        fake_db.devices.find.return_value.sort.return_value.limit.return_value = [d1]

        with (
            patch(
                "services.monitor_dispatch.require_scheduler_leadership",
                return_value=True,
            ),
            patch(
                "services.monitor_dispatch.start_monitor_runtime",
                return_value=MagicMock(),
            ),
            patch(
                "services.monitor_dispatch.CycleLeadershipGuard",
                return_value=guard,
            ),
            patch(
                "services.monitor_dispatch.begin_cycle_connectivity_check",
                return_value=False,
            ),
            patch(
                "services.monitor_dispatch.free_worker_capacity",
                return_value=1,
            ),
            patch("services.monitor_dispatch._db", return_value=fake_db),
            patch("services.monitor_dispatch.claim_device", return_value=None),
            patch("services.monitor_dispatch.submit_claimed_device") as submit,
            patch("services.monitor_dispatch._maybe_run_integrity_audit"),
        ):
            result = dispatch_monitor_due_devices()

        submit.assert_not_called()
        self.assertEqual(result["claim_conflicts"], 1)
        self.assertEqual(result["submitted"], 0)


class TestStopSchedulerRuntime(unittest.TestCase):
    def test_stop_scheduler_stops_runtime(self):
        import scheduler as sched

        mock_sched = MagicMock()
        mock_sched.running = True

        with (
            patch.object(sched, "scheduler", mock_sched),
            patch.object(sched, "release_scheduler_ownership"),
            patch("services.monitor_runtime.stop_monitor_runtime") as stop_rt,
        ):
            sched.stop_scheduler()

        stop_rt.assert_called()
        mock_sched.shutdown.assert_called()


class TestDueFilterIgnoresFutureAndActive(unittest.TestCase):
    def test_due_filter_shape(self):
        from services.monitor_claim import build_due_unclaimed_filter

        now = utc_now()
        filt = build_due_unclaimed_filter(now)
        self.assertEqual(filt["monitor"], True)
        self.assertIn("$and", filt)


if __name__ == "__main__":
    unittest.main()
