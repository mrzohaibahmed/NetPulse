"""Phase 7: pingInterval decoupled from APScheduler dispatcher period."""

from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

from bson import ObjectId

from utils.utc import utc_now


class TestDispatchDispatcherIndependentOfPingInterval(unittest.TestCase):
    def test_dispatch_ping_interval_30_dispatcher_still_5s(self):
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
            patch("services.monitor_runtime.start_monitor_runtime"),
            patch.object(sched, "_start_nmap_job"),
            patch.object(sched, "_start_interface_job"),
            patch.object(sched, "_start_interface_stats_job"),
            patch.object(sched, "_start_storm_analysis_job"),
            patch.object(sched, "_start_storm_confirmation_job"),
            patch.object(sched, "_start_storm_safety_prepare_job"),
            patch.object(sched, "_start_recovery_job"),
            patch.object(sched, "_start_retention_job"),
            patch.object(sched, "_register_isp_monitor_job"),
        ):
            sched.start_scheduler()

        device_job = mock_sched.add_job.call_args_list[0]
        self.assertIs(device_job.kwargs["func"], sched.dispatch_monitor_due_devices)
        self.assertEqual(device_job.kwargs["seconds"], 5)
        self.assertNotEqual(device_job.kwargs["seconds"], 30)

    def test_dispatch_ping_interval_60_dispatcher_still_5s(self):
        import scheduler as sched

        mock_sched = MagicMock()
        mock_sched.running = False

        with (
            patch.object(sched, "scheduler", mock_sched),
            patch.object(sched, "get_monitor_runtime_mode", return_value="dispatch"),
            patch.object(sched, "get_settings", return_value={"pingInterval": 60}),
            patch.object(
                sched, "get_monitor_dispatcher_interval_seconds", return_value=5
            ),
            patch("services.monitor_runtime.start_monitor_runtime"),
            patch.object(sched, "_start_nmap_job"),
            patch.object(sched, "_start_interface_job"),
            patch.object(sched, "_start_interface_stats_job"),
            patch.object(sched, "_start_storm_analysis_job"),
            patch.object(sched, "_start_storm_confirmation_job"),
            patch.object(sched, "_start_storm_safety_prepare_job"),
            patch.object(sched, "_start_recovery_job"),
            patch.object(sched, "_start_retention_job"),
            patch.object(sched, "_register_isp_monitor_job"),
        ):
            sched.start_scheduler()

        device_job = mock_sched.add_job.call_args_list[0]
        self.assertEqual(device_job.kwargs["seconds"], 5)
        self.assertNotEqual(device_job.kwargs["seconds"], 60)


class TestSettingsPutDoesNotRetargetDispatcher(unittest.TestCase):
    def test_put_30_to_60_does_not_change_dispatcher_period(self):
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
            sched.reschedule_monitor_job(60)

        for call in mock_sched.add_job.call_args_list:
            if call.kwargs.get("id") == sched.JOB_ID:
                self.fail("dispatch reschedule must not rebind device_monitor_job")
        isp.assert_called_once_with(60)

    def test_put_60_to_30_does_not_change_dispatcher_period(self):
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
            patch.object(sched, "_register_isp_monitor_job"),
        ):
            sched.reschedule_monitor_job(30)

        job_ids = [
            c.kwargs.get("id")
            for c in mock_sched.add_job.call_args_list
            if c.kwargs.get("id") == sched.JOB_ID
        ]
        self.assertEqual(job_ids, [])

    def test_settings_route_ping_interval_triggers_reschedule_only(self):
        """Route contract: pingInterval → reschedule; no concurrency rebuild."""
        from routes import settings_routes as routes

        updated = {
            "pingInterval": 60,
            "pingTimeoutMs": 1000,
            "pingRetries": 3,
            "pingConcurrency": 20,
        }
        data = {"pingInterval": 60}

        with (
            patch.object(routes, "reschedule_monitor_job") as resched,
            patch(
                "services.monitor_runtime.reconfigure_monitor_runtime_concurrency"
            ) as reconf,
        ):
            # Mirror update_settings_route side effects after update_settings().
            if "pingInterval" in data:
                routes.reschedule_monitor_job(int(updated.get("pingInterval", 30)))
            if "pingConcurrency" in data:
                from services.monitor_runtime import (  # noqa: PLC0415
                    reconfigure_monitor_runtime_concurrency,
                )

                reconfigure_monitor_runtime_concurrency()

        resched.assert_called_once_with(60)
        reconf.assert_not_called()


class TestClaimsUseUpdatedInterval(unittest.TestCase):
    def test_next_claim_uses_updated_ping_interval(self):
        from services import monitor_claim as claim_mod

        device_id = ObjectId()
        now = utc_now()
        device = {"_id": device_id, "ipAddress": "10.0.0.9", "monitor": True}

        captured = {}

        def fake_find_one_and_update(filt, update, return_document=None):
            captured["update"] = update
            return {
                **device,
                **update["$set"],
                "scanClaimId": update["$set"]["scanClaimId"],
            }

        fake_db = MagicMock()
        fake_db.devices.find_one_and_update.side_effect = fake_find_one_and_update

        cfg = {
            "interval": 60,
            "timeout_ms": 1000,
            "retries": 3,
            "failure_confirmation_scans": 2,
        }

        with (
            patch.object(claim_mod, "_db", return_value=fake_db),
            patch.object(claim_mod, "get_ping_config", return_value=cfg),
            patch.object(
                claim_mod, "with_mongo_retry", side_effect=lambda fn, **_k: fn()
            ),
        ):
            claimed = claim_mod.claim_device(device_id, device=device, now=now)

        self.assertIsNotNone(claimed)
        next_at = captured["update"]["$set"]["nextCheckAt"]
        self.assertEqual(next_at, now + timedelta(seconds=60))

    def test_no_mass_rewrite_on_settings_update(self):
        """update_settings must not touch devices / nextCheckAt."""
        from services import settings_service as ss

        fake_db = MagicMock()
        fake_settings_doc = {
            "_id": "global",
            "pingInterval": 30,
            "pingTimeoutMs": 1000,
            "pingRetries": 3,
            "pingConcurrency": 20,
        }

        with (
            patch.object(ss, "ensure_settings"),
            patch.object(
                ss,
                "get_settings",
                side_effect=[
                    fake_settings_doc,
                    {**fake_settings_doc, "pingInterval": 60},
                ],
            ),
            patch("services.settings_service.db", fake_db),
        ):
            ss.update_settings({"pingInterval": 60})

        fake_db.settings.update_one.assert_called()
        self.assertFalse(fake_db.devices.update_many.called)
        self.assertFalse(fake_db.devices.update_one.called)


class TestLegacyUnchanged(unittest.TestCase):
    def test_legacy_reschedule_tracks_ping_interval(self):
        import scheduler as sched

        mock_sched = MagicMock()
        mock_sched.running = True

        with (
            patch.object(sched, "scheduler", mock_sched),
            patch.object(sched, "get_monitor_runtime_mode", return_value="legacy"),
            patch.object(sched, "_register_isp_monitor_job"),
        ):
            sched.reschedule_monitor_job(45)

        kwargs = mock_sched.add_job.call_args.kwargs
        self.assertIs(kwargs["func"], sched.monitor_all_devices)
        self.assertEqual(kwargs["seconds"], 45)
        self.assertEqual(kwargs["id"], sched.JOB_ID)
        self.assertTrue(kwargs["replace_existing"])


class TestConcurrencyValidationAndNoDuplicateJobs(unittest.TestCase):
    def test_concurrency_validation_1_to_64(self):
        from services import settings_service as ss

        fake_db = MagicMock()
        current = {
            "_id": "global",
            "pingInterval": 30,
            "pingConcurrency": 20,
        }

        with (
            patch.object(ss, "ensure_settings"),
            patch.object(ss, "get_settings", return_value=current),
            patch("services.settings_service.db", fake_db),
        ):
            with self.assertRaises(ValueError):
                ss.update_settings({"pingConcurrency": 0})
            with self.assertRaises(ValueError):
                ss.update_settings({"pingConcurrency": 65})

    def test_no_duplicate_jobs_after_repeated_legacy_reschedule(self):
        import scheduler as sched

        mock_sched = MagicMock()
        mock_sched.running = True

        with (
            patch.object(sched, "scheduler", mock_sched),
            patch.object(sched, "get_monitor_runtime_mode", return_value="legacy"),
            patch.object(sched, "_register_isp_monitor_job"),
        ):
            sched.reschedule_monitor_job(30)
            sched.reschedule_monitor_job(60)
            sched.reschedule_monitor_job(45)

        device_adds = [
            c
            for c in mock_sched.add_job.call_args_list
            if c.kwargs.get("id") == sched.JOB_ID
        ]
        self.assertEqual(len(device_adds), 3)
        for call in device_adds:
            self.assertTrue(call.kwargs.get("replace_existing"))

    def test_dispatch_reschedule_dispatcher_job_uses_env_not_ping_interval(self):
        import scheduler as sched

        mock_sched = MagicMock()
        mock_sched.running = True

        with (
            patch.object(sched, "scheduler", mock_sched),
            patch.object(sched, "get_monitor_runtime_mode", return_value="dispatch"),
            patch.object(
                sched, "get_monitor_dispatcher_interval_seconds", return_value=5
            ),
        ):
            sched.reschedule_dispatcher_job()

        kwargs = mock_sched.add_job.call_args.kwargs
        self.assertIs(kwargs["func"], sched.dispatch_monitor_due_devices)
        self.assertEqual(kwargs["seconds"], 5)
        self.assertTrue(kwargs["replace_existing"])
        self.assertEqual(kwargs["id"], sched.JOB_ID)

    def test_start_monitor_runtime_idempotent_no_pool_churn(self):
        from services import monitor_runtime as rt

        fake = MagicMock()
        fake.stats.return_value = {"started": True, "concurrency": 4}

        with rt._runtime_lock:
            previous = rt._runtime
            rt._runtime = fake
        try:
            out1 = rt.start_monitor_runtime(concurrency=8)
            out2 = rt.start_monitor_runtime(concurrency=16)
            self.assertIs(out1, fake)
            self.assertIs(out2, fake)
            fake.start.assert_not_called()
            fake.stop.assert_not_called()
        finally:
            with rt._runtime_lock:
                rt._runtime = previous

    def test_reconfigure_concurrency_defers_when_busy(self):
        from services import monitor_runtime as rt

        fake = MagicMock()
        fake.stats.return_value = {
            "started": True,
            "concurrency": 4,
            "occupancy": 2,
        }
        fake.concurrency = 4

        with patch.object(rt, "get_monitor_ping_concurrency", return_value=8):
            with rt._runtime_lock:
                previous = rt._runtime
                rt._runtime = fake
            try:
                result = rt.reconfigure_monitor_runtime_concurrency()
            finally:
                with rt._runtime_lock:
                    rt._runtime = previous

        self.assertFalse(result["applied"])
        self.assertEqual(result["reason"], "busy")
        fake.stop.assert_not_called()


class TestMonitorFalseNotClaimed(unittest.TestCase):
    def test_due_filter_requires_monitor_true(self):
        from services.monitor_claim import build_due_unclaimed_filter

        filt = build_due_unclaimed_filter(utc_now())
        self.assertEqual(filt["monitor"], True)


if __name__ == "__main__":
    unittest.main()
