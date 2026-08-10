"""
Automated tests for final ping-monitoring correctness fixes.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

from bson import ObjectId
from pymongo.errors import DuplicateKeyError, NetworkTimeout


class TestUtcHelpers(unittest.TestCase):
    def test_utc_now_aware(self):
        from utils.utc import utc_now

        self.assertIsNotNone(utc_now().tzinfo)

    def test_ensure_utc_naive(self):
        from datetime import datetime

        from utils.utc import ensure_utc

        aware = ensure_utc(datetime(2024, 1, 1, 12, 0, 0))
        self.assertIsNotNone(aware.tzinfo)


class TestMongoRetryPolicy(unittest.TestCase):
    def test_non_idempotent_does_not_retry(self):
        from services.mongo_retry import with_mongo_retry

        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise NetworkTimeout("timeout")

        with self.assertRaises(NetworkTimeout):
            with_mongo_retry(boom, action="bare_inc", idempotent=False)
        self.assertEqual(calls["n"], 1)

    def test_idempotent_retries_transient(self):
        from services.mongo_retry import with_mongo_retry

        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise NetworkTimeout("timeout")
            return "ok"

        result = with_mongo_retry(
            flaky,
            action="safe_op",
            idempotent=True,
            max_attempts=5,
            base_delay_s=0.01,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 3)


class TestCycleLeadershipGuard(unittest.TestCase):
    @patch("services.scheduler_ownership.try_acquire_or_renew", return_value=True)
    def test_force_renew_at_start(self, mock_renew):
        from services.scheduler_ownership import CycleLeadershipGuard

        guard = CycleLeadershipGuard(cycle_id="c1")
        self.assertTrue(guard.ensure(force=True, reason="start"))
        mock_renew.assert_called()

    @patch("services.scheduler_ownership.try_acquire_or_renew", return_value=False)
    def test_abort_on_leadership_loss(self, _mock):
        from services.scheduler_ownership import CycleLeadershipGuard

        guard = CycleLeadershipGuard(cycle_id="c1")
        self.assertFalse(guard.ensure(force=True, reason="lost"))
        self.assertTrue(guard.aborted)
        self.assertIn("leadership_lost", guard.abort_reason or "")
        # Subsequent ensures stay aborted
        self.assertFalse(guard.ensure(force=True, reason="again"))

    @patch("services.scheduler_ownership.try_acquire_or_renew", return_value=True)
    def test_time_based_heartbeat(self, mock_renew):
        from services.scheduler_ownership import CycleLeadershipGuard
        from utils.utc import utc_now

        guard = CycleLeadershipGuard(cycle_id="c1")
        guard.heartbeat_s = 10
        guard._last_renew_at = utc_now() - timedelta(seconds=11)
        mock_renew.reset_mock()
        self.assertTrue(guard.ensure(reason="elapsed"))
        mock_renew.assert_called_once()

    @patch("services.scheduler_ownership.try_acquire_or_renew", return_value=True)
    def test_device_count_heartbeat(self, mock_renew):
        from services.scheduler_ownership import CycleLeadershipGuard

        guard = CycleLeadershipGuard(cycle_id="c1", device_renew_every=3)
        guard.heartbeat_s = 10_000
        for _ in range(3):
            guard.note_device_visited()
        mock_renew.reset_mock()
        self.assertTrue(guard.ensure(reason="devices"))
        mock_renew.assert_called_once()


class TestIdempotentFailureCounter(unittest.TestCase):
    @patch("services.monitor_service.get_failure_confirmation_scans", return_value=1)
    @patch("services.monitor_service.maybe_send_critical_offline_alert")
    @patch("services.monitor_service.save_ping_history")
    @patch("services.monitor_service._db")
    def test_failure_filter_includes_attempt_id(
        self, mock_db, _hist, mock_alert, _threshold
    ):
        from services.monitor_service import apply_ping_result
        from utils.utc import utc_now

        device_id = ObjectId()
        started = utc_now()
        device = {
            "_id": device_id,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "status": "Online",
            "critical": True,
            "consecutiveFailures": 2,
        }
        updated = {
            **device,
            "status": "Offline (Critical)",
            "consecutiveFailures": 3,
            "lastPingAttemptId": "attempt-1",
            "lastPingStartedAt": started,
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = updated
        mock_db.return_value.devices = coll

        apply_ping_result(
            device,
            {
                "success": False,
                "status": "Offline (Critical)",
                "responseTime": None,
                "lastSeen": None,
                "message": "down",
                "pingStartedAt": started,
            },
            attempt_id="attempt-1",
        )

        filt = coll.find_one_and_update.call_args[0][0]
        self.assertEqual(filt["lastPingAttemptId"], {"$ne": "attempt-1"})
        self.assertIn("$or", filt)
        pipeline = coll.find_one_and_update.call_args[0][1]
        self.assertIsInstance(pipeline, list)
        self.assertEqual(pipeline[0]["$set"]["lastPingAttemptId"], "attempt-1")
        self.assertEqual(mock_alert.call_args.kwargs["consecutive_failures"], 3)

    @patch("services.monitor_service.get_failure_confirmation_scans", return_value=1)
    @patch("services.monitor_service.maybe_send_critical_offline_alert")
    @patch("services.monitor_service.save_ping_history")
    @patch("services.monitor_service._db")
    def test_retry_after_commit_does_not_double_inc(
        self, mock_db, _hist, mock_alert, _threshold
    ):
        from services.monitor_service import apply_ping_result
        from utils.utc import utc_now

        device_id = ObjectId()
        started = utc_now()
        device = {
            "_id": device_id,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "status": "Online",
            "critical": True,
            "consecutiveFailures": 2,
        }
        committed = {
            **device,
            "status": "Offline (Critical)",
            "consecutiveFailures": 3,
            "lastPingAttemptId": "attempt-1",
            "lastPingStartedAt": started,
        }
        coll = MagicMock()
        # First find_one_and_update: as if already applied (None), then recover via find_one
        coll.find_one_and_update.return_value = None
        coll.find_one.return_value = committed
        mock_db.return_value.devices = coll

        apply_ping_result(
            device,
            {
                "success": False,
                "status": "Offline (Critical)",
                "responseTime": None,
                "lastSeen": None,
                "message": "down",
                "pingStartedAt": started,
            },
            attempt_id="attempt-1",
        )

        # Update attempted once; recovery path used find_one — no second inc applied
        self.assertEqual(coll.find_one_and_update.call_count, 1)
        mock_alert.assert_called_once()
        self.assertEqual(mock_alert.call_args.kwargs["consecutive_failures"], 3)


class TestIdempotentHistory(unittest.TestCase):
    @patch("services.history_service.publish")
    @patch("services.history_service._db")
    def test_duplicate_key_treated_as_success(self, mock_db, _pub):
        from services.history_service import save_ping_history

        existing_id = ObjectId()
        coll = MagicMock()
        coll.insert_one.side_effect = DuplicateKeyError("dup")
        coll.find_one.return_value = {"_id": existing_id}
        mock_db.return_value.pingHistory = coll

        hid = save_ping_history(
            {"_id": ObjectId(), "hostname": "h", "ipAddress": "1.1.1.1"},
            {"status": "Online", "responseTime": 1.0},
            attempt_id="hist-1",
        )
        self.assertEqual(hid, existing_id)


class TestAlertRecovery(unittest.TestCase):
    @patch("services.alert_service.send_critical_offline_alert", return_value=True)
    @patch("services.alert_service.db")
    def test_alerts_when_failures_above_threshold(self, mock_db, _email):
        from services.alert_service import maybe_send_critical_offline_alert

        device = {
            "_id": ObjectId(),
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "critical": True,
            "deviceType": "Switch",
        }
        mock_db.alerts.find_one.return_value = None
        insert_result = MagicMock()
        insert_result.acknowledged = True
        insert_result.inserted_id = ObjectId()
        mock_db.alerts.insert_one.return_value = insert_result

        ok = maybe_send_critical_offline_alert(
            device,
            "Online",
            "Offline (Critical)",
            consecutive_failures=5,  # missed exactly-3
        )
        self.assertTrue(ok)
        mock_db.alerts.insert_one.assert_called_once()

    @patch("services.alert_service.send_critical_offline_alert", return_value=True)
    @patch("services.alert_service.db")
    def test_duplicate_key_on_insert_is_idempotent(self, mock_db, _email):
        from services.alert_service import maybe_send_critical_offline_alert

        device = {
            "_id": ObjectId(),
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "critical": True,
        }
        mock_db.alerts.find_one.return_value = None
        mock_db.alerts.insert_one.side_effect = DuplicateKeyError("uniq")

        ok = maybe_send_critical_offline_alert(
            device,
            "Online",
            "Offline (Critical)",
            consecutive_failures=3,
        )
        self.assertFalse(ok)

    @patch("services.alert_service.send_critical_offline_alert", return_value=True)
    @patch("services.alert_service.db")
    def test_skips_when_active_exists(self, mock_db, _email):
        from services.alert_service import maybe_send_critical_offline_alert

        mock_db.alerts.find_one.return_value = {"_id": ObjectId()}
        ok = maybe_send_critical_offline_alert(
            {
                "_id": ObjectId(),
                "hostname": "sw1",
                "ipAddress": "10.0.0.1",
                "critical": True,
            },
            "Online",
            "Offline (Critical)",
            consecutive_failures=3,
        )
        self.assertFalse(ok)
        mock_db.alerts.insert_one.assert_not_called()


class TestMonitorLoopAbort(unittest.TestCase):
    @patch("services.monitor_service.run_integrity_audit")
    @patch("services.monitor_service.begin_cycle_connectivity_check", return_value=False)
    @patch("services.monitor_service._scan_device")
    @patch("services.monitor_service._db")
    @patch("services.monitor_service.require_scheduler_leadership", return_value=True)
    @patch("services.monitor_service.CycleLeadershipGuard")
    def test_cycle_stops_when_guard_fails(
        self,
        mock_guard_cls,
        _lead,
        mock_db,
        mock_scan,
        _probe,
        _audit,
    ):
        from services.monitor_service import monitor_all_devices

        guard = MagicMock()
        # start ok, then fail on first select ensure
        guard.ensure.side_effect = [True, False]
        guard.aborted = True
        guard.abort_reason = "leadership_lost:test"
        guard.heartbeat_s = 30
        mock_guard_cls.return_value = guard

        devices = [
            {"_id": ObjectId(), "hostname": "a", "ipAddress": "1.1.1.1", "monitor": True},
            {"_id": ObjectId(), "hostname": "b", "ipAddress": "1.1.1.2", "monitor": True},
        ]
        mock_db.return_value.devices.find.return_value = devices

        monitor_all_devices()
        mock_scan.assert_not_called()

    @patch("services.monitor_service.get_monitor_ping_concurrency", return_value=2)
    @patch("services.monitor_service.get_ping_config")
    @patch("services.monitor_service.run_integrity_audit")
    @patch("services.monitor_service.begin_cycle_connectivity_check", return_value=False)
    @patch("services.monitor_service._scan_device")
    @patch("services.monitor_service._db")
    @patch("services.monitor_service.require_scheduler_leadership", return_value=True)
    @patch("services.monitor_service.CycleLeadershipGuard")
    def test_parallel_batches_scan_due_devices(
        self,
        mock_guard_cls,
        _lead,
        mock_db,
        mock_scan,
        _probe,
        _audit,
        mock_ping_cfg,
        _conc,
    ):
        from services.monitor_service import monitor_all_devices

        mock_ping_cfg.return_value = {
            "interval": 30,
            "timeout_ms": 1000,
            "retries": 2,
            "failure_confirmation_scans": 2,
        }
        guard = MagicMock()
        guard.ensure.return_value = True
        guard.aborted = False
        guard.abort_reason = None
        guard.heartbeat_s = 30
        mock_guard_cls.return_value = guard

        devices = [
            {
                "_id": ObjectId(),
                "hostname": f"h{i}",
                "ipAddress": f"10.0.0.{i}",
                "monitor": True,
                "lastCheckedAt": None,
            }
            for i in range(5)
        ]
        mock_db.return_value.devices.find.return_value = devices

        monitor_all_devices()
        self.assertEqual(mock_scan.call_count, 5)

    @patch("services.monitor_service.get_monitor_ping_concurrency", return_value=4)
    @patch("services.monitor_service.get_ping_config")
    @patch("services.monitor_service.run_integrity_audit")
    @patch("services.monitor_service.begin_cycle_connectivity_check", return_value=False)
    @patch("services.monitor_service._scan_device")
    @patch("services.monitor_service._db")
    @patch("services.monitor_service.require_scheduler_leadership", return_value=True)
    @patch("services.monitor_service.CycleLeadershipGuard")
    def test_skips_devices_not_due(
        self,
        mock_guard_cls,
        _lead,
        mock_db,
        mock_scan,
        _probe,
        _audit,
        mock_ping_cfg,
        _conc,
    ):
        from datetime import timedelta

        from services.monitor_service import monitor_all_devices
        from utils.utc import utc_now

        mock_ping_cfg.return_value = {
            "interval": 30,
            "timeout_ms": 1000,
            "retries": 2,
            "failure_confirmation_scans": 2,
        }
        guard = MagicMock()
        guard.ensure.return_value = True
        guard.aborted = False
        guard.abort_reason = None
        guard.heartbeat_s = 30
        mock_guard_cls.return_value = guard

        recent = utc_now() - timedelta(seconds=5)
        devices = [
            {
                "_id": ObjectId(),
                "hostname": "fresh",
                "ipAddress": "10.0.0.1",
                "monitor": True,
                "lastCheckedAt": recent,
                "pingInterval": 30,
            },
            {
                "_id": ObjectId(),
                "hostname": "due",
                "ipAddress": "10.0.0.2",
                "monitor": True,
                "lastCheckedAt": None,
            },
        ]
        mock_db.return_value.devices.find.return_value = devices

        with patch(
            "services.monitor_service.get_ping_config",
            side_effect=lambda device=None: {
                "interval": int((device or {}).get("pingInterval") or 30),
                "timeout_ms": 1000,
                "retries": 2,
                "failure_confirmation_scans": 2,
            },
        ):
            monitor_all_devices()

        self.assertEqual(mock_scan.call_count, 1)
        self.assertEqual(mock_scan.call_args[0][0]["hostname"], "due")


class TestPingConcurrencyConfig(unittest.TestCase):
    def test_default_concurrency_in_defaults(self):
        from services.settings_service import DEFAULT_SETTINGS

        self.assertEqual(DEFAULT_SETTINGS["pingConcurrency"], 20)

    @patch("services.settings_service.get_settings")
    def test_concurrency_clamped(self, mock_settings):
        from services.settings_service import get_monitor_ping_concurrency

        mock_settings.return_value = {"pingConcurrency": 1000}
        self.assertEqual(get_monitor_ping_concurrency(), 64)
        mock_settings.return_value = {"pingConcurrency": 0}
        self.assertEqual(get_monitor_ping_concurrency(), 1)


class TestOnlineApply(unittest.TestCase):
    @patch("services.monitor_service.resolve_critical_offline_alerts")
    @patch("services.monitor_service.save_ping_history")
    @patch("services.monitor_service._db")
    def test_success_resets_failures(self, mock_db, mock_hist, mock_resolve):
        from services.monitor_service import apply_ping_result
        from utils.utc import utc_now

        device_id = ObjectId()
        started = utc_now()
        device = {
            "_id": device_id,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "status": "Offline (Critical)",
            "critical": True,
            "consecutiveFailures": 3,
        }
        updated = {
            **device,
            "status": "Online",
            "consecutiveFailures": 0,
            "responseTime": 1.5,
            "lastPingAttemptId": "a1",
            "lastPingStartedAt": started,
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = updated
        mock_db.return_value.devices = coll

        apply_ping_result(
            device,
            {
                "success": True,
                "status": "Online",
                "responseTime": 1.5,
                "lastSeen": started,
                "message": "ok",
                "pingStartedAt": started,
            },
            scan_type="Manual",
            attempt_id="a1",
        )
        update = coll.find_one_and_update.call_args[0][1]
        self.assertEqual(update["$set"]["consecutiveFailures"], 0)
        self.assertEqual(update["$set"]["lastPingStartedAt"], started)
        filt = coll.find_one_and_update.call_args[0][0]
        self.assertIn("$or", filt)
        mock_hist.assert_called_once()
        mock_resolve.assert_called_once()


class TestPartitionSuppress(unittest.TestCase):
    @patch("services.monitor_service.save_ping_history")
    @patch("services.monitor_service._db")
    def test_partition_suppresses_offline(self, mock_db, mock_hist):
        from services.monitor_service import apply_ping_result

        device = {
            "_id": ObjectId(),
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "status": "Online",
            "critical": False,
            "consecutiveFailures": 0,
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = {**device}
        mock_db.return_value.devices = coll

        apply_ping_result(
            device,
            {
                "success": False,
                "status": "Not Reachable",
                "responseTime": None,
                "lastSeen": None,
                "message": "unreachable",
            },
            suppress_offline=True,
            cycle_id="abc",
        )
        update = coll.find_one_and_update.call_args[0][1]
        self.assertNotIn("status", update["$set"])
        mock_hist.assert_not_called()


class TestMonitorEvents(unittest.TestCase):
    def test_handler_failure_does_not_raise(self):
        from services import monitor_events as ev

        def bad(_t, _p):
            raise RuntimeError("boom")

        ev.subscribe(bad)
        try:
            ev.publish(ev.EVENT_DEVICE_STATUS_CHANGED, {"deviceId": "x"})
        finally:
            ev.unsubscribe(bad)

    def test_owner_id_stable(self):
        from services.scheduler_ownership import get_owner_id

        self.assertEqual(get_owner_id(), get_owner_id())


class TestIntegrity(unittest.TestCase):
    def test_invalid_status(self):
        from services.monitor_integrity import validate_device_document

        issues = validate_device_document(
            {
                "_id": ObjectId(),
                "hostname": "x",
                "ipAddress": "1.2.3.4",
                "status": "BROKEN",
                "monitor": True,
                "consecutiveFailures": 0,
            }
        )
        self.assertTrue(any(i.startswith("invalid_status") for i in issues))

    @patch("services.monitor_integrity.get_failure_confirmation_scans", return_value=2)
    def test_online_with_sub_threshold_failures_ok(self, _threshold):
        from services.monitor_integrity import validate_device_document

        issues = validate_device_document(
            {
                "_id": ObjectId(),
                "hostname": "x",
                "ipAddress": "1.2.3.4",
                "status": "Online",
                "monitor": True,
                "consecutiveFailures": 1,
            }
        )
        self.assertFalse(any(i == "online_with_failures" for i in issues))

    @patch("services.monitor_integrity.get_failure_confirmation_scans", return_value=2)
    def test_online_with_threshold_failures_flagged(self, _threshold):
        from services.monitor_integrity import validate_device_document

        issues = validate_device_document(
            {
                "_id": ObjectId(),
                "hostname": "x",
                "ipAddress": "1.2.3.4",
                "status": "Online",
                "monitor": True,
                "consecutiveFailures": 2,
            }
        )
        self.assertTrue(any(i == "online_with_failures" for i in issues))


class TestFreshnessOrdering(unittest.TestCase):
    @patch("services.monitor_service.maybe_send_critical_offline_alert")
    @patch("services.monitor_service.resolve_critical_offline_alerts")
    @patch("services.monitor_service.save_ping_history")
    @patch("services.monitor_service._db")
    def test_older_failure_cannot_overwrite_newer_online(
        self, mock_db, mock_hist, mock_resolve, mock_alert
    ):
        from services.monitor_service import apply_ping_result
        from utils.utc import utc_now

        device_id = ObjectId()
        t_old = utc_now() - timedelta(seconds=5)
        t_new = utc_now()
        device = {
            "_id": device_id,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "status": "Online",
            "critical": False,
            "consecutiveFailures": 0,
            "lastPingAttemptId": "newer",
            "lastPingStartedAt": t_new,
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = None
        coll.find_one.return_value = device
        mock_db.return_value.devices = coll

        apply_ping_result(
            device,
            {
                "success": False,
                "status": "Not Reachable",
                "responseTime": None,
                "lastSeen": None,
                "message": "timeout",
                "pingStartedAt": t_old,
                "pingCompletedAt": utc_now(),
            },
            scan_type="Automatic",
            attempt_id="older",
        )

        mock_hist.assert_not_called()
        mock_alert.assert_not_called()
        mock_resolve.assert_not_called()

    @patch("services.monitor_service.get_failure_confirmation_scans", return_value=1)
    @patch("services.monitor_service.maybe_send_critical_offline_alert")
    @patch("services.monitor_service.resolve_critical_offline_alerts")
    @patch("services.monitor_service.save_ping_history")
    @patch("services.monitor_service._db")
    def test_older_online_cannot_overwrite_newer_failure(
        self, mock_db, mock_hist, mock_resolve, mock_alert, _threshold
    ):
        from services.monitor_service import apply_ping_result
        from utils.utc import utc_now

        device_id = ObjectId()
        t_old = utc_now() - timedelta(seconds=5)
        t_new = utc_now()
        device = {
            "_id": device_id,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "status": "Not Reachable",
            "critical": False,
            "consecutiveFailures": 2,
            "lastPingAttemptId": "newer-fail",
            "lastPingStartedAt": t_new,
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = None
        coll.find_one.return_value = device
        mock_db.return_value.devices = coll

        apply_ping_result(
            device,
            {
                "success": True,
                "status": "Online",
                "responseTime": 12.0,
                "lastSeen": t_old,
                "message": "ok",
                "pingStartedAt": t_old,
                "pingCompletedAt": utc_now(),
            },
            scan_type="Manual",
            attempt_id="older-ok",
        )

        mock_hist.assert_not_called()
        mock_resolve.assert_not_called()

    @patch("services.monitor_service.resolve_critical_offline_alerts")
    @patch("services.monitor_service.save_ping_history")
    @patch("services.monitor_service._db")
    def test_newer_result_can_update(self, mock_db, mock_hist, mock_resolve):
        from services.monitor_service import apply_ping_result
        from utils.utc import utc_now

        device_id = ObjectId()
        t_old = utc_now() - timedelta(seconds=5)
        t_new = utc_now()
        device = {
            "_id": device_id,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "status": "Not Reachable",
            "critical": False,
            "consecutiveFailures": 2,
            "lastPingAttemptId": "old",
            "lastPingStartedAt": t_old,
        }
        updated = {
            **device,
            "status": "Online",
            "consecutiveFailures": 0,
            "lastPingAttemptId": "new",
            "lastPingStartedAt": t_new,
            "responseTime": 5.0,
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = updated
        mock_db.return_value.devices = coll

        apply_ping_result(
            device,
            {
                "success": True,
                "status": "Online",
                "responseTime": 5.0,
                "lastSeen": t_new,
                "message": "ok",
                "pingStartedAt": t_new,
            },
            scan_type="Discovery",
            attempt_id="new",
        )

        filt = coll.find_one_and_update.call_args[0][0]
        self.assertEqual(filt["lastPingAttemptId"], {"$ne": "new"})
        mock_hist.assert_called_once()
        mock_resolve.assert_called_once()

    @patch("services.monitor_service.get_failure_confirmation_scans", return_value=1)
    @patch("services.monitor_service.maybe_send_critical_offline_alert")
    @patch("services.monitor_service.save_ping_history")
    @patch("services.monitor_service._db")
    def test_completion_order_does_not_determine_freshness(
        self, mock_db, mock_hist, mock_alert, _threshold
    ):
        """Later-finishing older start is still rejected via start-time filter."""
        from services.monitor_service import _freshness_filter
        from utils.utc import utc_now

        t1 = utc_now() - timedelta(seconds=2)
        t2 = utc_now()
        filt = _freshness_filter(ObjectId(), "attempt-a", t1)
        lte_clause = next(
            c
            for c in filt["$or"]
            if isinstance(c.get("lastPingStartedAt"), dict)
            and "$lte" in c["lastPingStartedAt"]
        )
        self.assertEqual(lte_clause["lastPingStartedAt"]["$lte"], t1)
        self.assertLess(t1, t2)

    @patch("services.monitor_service.maybe_send_critical_offline_alert")
    @patch("services.monitor_service.save_ping_history")
    @patch("services.monitor_service._db")
    def test_stale_skips_alerts(self, mock_db, mock_hist, mock_alert):
        from services.monitor_service import apply_ping_result
        from utils.utc import utc_now

        device_id = ObjectId()
        t_old = utc_now() - timedelta(seconds=3)
        device = {
            "_id": device_id,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "status": "Online",
            "critical": True,
            "consecutiveFailures": 0,
            "lastPingAttemptId": "fresh",
            "lastPingStartedAt": utc_now(),
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = None
        coll.find_one.return_value = device
        mock_db.return_value.devices = coll

        apply_ping_result(
            device,
            {
                "success": False,
                "status": "Offline (Critical)",
                "responseTime": None,
                "lastSeen": None,
                "message": "down",
                "pingStartedAt": t_old,
            },
            attempt_id="stale",
        )
        mock_alert.assert_not_called()
        mock_hist.assert_not_called()


class TestFailureHysteresis(unittest.TestCase):
    @patch("services.monitor_service.get_failure_confirmation_scans", return_value=2)
    @patch("services.monitor_service.maybe_send_critical_offline_alert")
    @patch("services.monitor_service.save_ping_history")
    @patch("services.monitor_service._db")
    def test_one_failed_scan_keeps_online(self, mock_db, mock_hist, mock_alert, _thr):
        from services.monitor_service import apply_ping_result
        from utils.utc import utc_now

        device_id = ObjectId()
        started = utc_now()
        device = {
            "_id": device_id,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "status": "Online",
            "critical": False,
            "consecutiveFailures": 0,
        }
        # After first failed scan with threshold=2, status stays Online.
        updated = {
            **device,
            "status": "Online",
            "consecutiveFailures": 1,
            "lastPingAttemptId": "a1",
            "lastPingStartedAt": started,
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = updated
        mock_db.return_value.devices = coll

        apply_ping_result(
            device,
            {
                "success": False,
                "status": "Not Reachable",
                "responseTime": None,
                "lastSeen": None,
                "message": "unreachable",
                "pingStartedAt": started,
            },
            attempt_id="a1",
        )

        pipeline = coll.find_one_and_update.call_args[0][1]
        status_expr = pipeline[0]["$set"]["status"]
        self.assertEqual(status_expr["$cond"][0]["$gte"][1], 2)
        mock_alert.assert_called_once()
        # Alert receives authoritative Online status — will no-op inside alert service
        # for non-critical / wrong status; here critical=False so skipped anyway.
        self.assertEqual(mock_alert.call_args.args[2], "Online")
        self.assertEqual(mock_alert.call_args.kwargs["consecutive_failures"], 1)

    @patch("services.monitor_service.get_failure_confirmation_scans", return_value=2)
    @patch("services.monitor_service.maybe_send_critical_offline_alert")
    @patch("services.monitor_service.save_ping_history")
    @patch("services.monitor_service._db")
    def test_second_failed_scan_sets_not_reachable(
        self, mock_db, mock_hist, mock_alert, _thr
    ):
        from services.monitor_service import apply_ping_result
        from utils.utc import utc_now

        device_id = ObjectId()
        started = utc_now()
        device = {
            "_id": device_id,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "status": "Online",
            "critical": False,
            "consecutiveFailures": 1,
        }
        updated = {
            **device,
            "status": "Not Reachable",
            "consecutiveFailures": 2,
            "lastPingAttemptId": "a2",
            "lastPingStartedAt": started,
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = updated
        mock_db.return_value.devices = coll

        apply_ping_result(
            device,
            {
                "success": False,
                "status": "Not Reachable",
                "responseTime": None,
                "lastSeen": None,
                "message": "unreachable",
                "pingStartedAt": started,
            },
            attempt_id="a2",
        )
        self.assertEqual(mock_alert.call_args.args[2], "Not Reachable")
        self.assertEqual(mock_alert.call_args.kwargs["consecutive_failures"], 2)

    @patch("services.monitor_service.resolve_critical_offline_alerts")
    @patch("services.monitor_service.save_ping_history")
    @patch("services.monitor_service._db")
    def test_success_resets_confirmation_sequence(
        self, mock_db, mock_hist, mock_resolve
    ):
        from services.monitor_service import apply_ping_result
        from utils.utc import utc_now

        started = utc_now()
        device_id = ObjectId()
        device = {
            "_id": device_id,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "status": "Online",
            "critical": False,
            "consecutiveFailures": 1,
        }
        updated = {
            **device,
            "status": "Online",
            "consecutiveFailures": 0,
            "lastPingAttemptId": "ok",
            "lastPingStartedAt": started,
            "responseTime": 3.0,
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = updated
        mock_db.return_value.devices = coll

        apply_ping_result(
            device,
            {
                "success": True,
                "status": "Online",
                "responseTime": 3.0,
                "lastSeen": started,
                "message": "ok",
                "pingStartedAt": started,
            },
            attempt_id="ok",
        )
        self.assertEqual(
            coll.find_one_and_update.call_args[0][1]["$set"]["consecutiveFailures"], 0
        )

    @patch("services.ping_service.get_ping_config")
    @patch("services.ping_service.ping")
    def test_icmp_retries_are_one_scan(self, mock_ping, mock_cfg):
        from services.ping_service import ping_device

        mock_cfg.return_value = {
            "interval": 30,
            "timeout_ms": 1000,
            "retries": 3,
            "failure_confirmation_scans": 2,
        }
        mock_ping.side_effect = [None, None, None]
        result = ping_device("10.0.0.1", critical=False)
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "Not Reachable")
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(mock_ping.call_count, 3)
        self.assertIn("pingStartedAt", result)
        self.assertIn("pingCompletedAt", result)


class TestPingConfigDefaults(unittest.TestCase):
    def test_failure_confirmation_default(self):
        from services.settings_service import DEFAULT_SETTINGS

        self.assertEqual(DEFAULT_SETTINGS["pingFailureConfirmationScans"], 2)


if __name__ == "__main__":
    unittest.main()
