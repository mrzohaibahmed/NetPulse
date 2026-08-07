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
    @patch("services.monitor_service.maybe_send_critical_offline_alert")
    @patch("services.monitor_service.save_ping_history")
    @patch("services.monitor_service._db")
    def test_failure_filter_includes_attempt_id(self, mock_db, _hist, mock_alert):
        from services.monitor_service import apply_ping_result

        device_id = ObjectId()
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
            },
            attempt_id="attempt-1",
        )

        filt = coll.find_one_and_update.call_args[0][0]
        self.assertEqual(filt["lastPingAttemptId"], {"$ne": "attempt-1"})
        update = coll.find_one_and_update.call_args[0][1]
        self.assertEqual(update["$inc"]["consecutiveFailures"], 1)
        self.assertEqual(update["$set"]["lastPingAttemptId"], "attempt-1")
        self.assertEqual(mock_alert.call_args.kwargs["consecutive_failures"], 3)

    @patch("services.monitor_service.maybe_send_critical_offline_alert")
    @patch("services.monitor_service.save_ping_history")
    @patch("services.monitor_service._db")
    def test_retry_after_commit_does_not_double_inc(self, mock_db, _hist, mock_alert):
        from services.monitor_service import apply_ping_result

        device_id = ObjectId()
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
            },
            attempt_id="attempt-1",
        )

        # $inc only attempted once; recovery path used find_one — no second inc payload applied
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
        # start ok, then fail on first pre_device ensure
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


class TestOnlineApply(unittest.TestCase):
    @patch("services.monitor_service.resolve_critical_offline_alerts")
    @patch("services.monitor_service.save_ping_history")
    @patch("services.monitor_service._db")
    def test_success_resets_failures(self, mock_db, mock_hist, mock_resolve):
        from services.monitor_service import apply_ping_result
        from utils.utc import utc_now

        device_id = ObjectId()
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
                "lastSeen": utc_now(),
                "message": "ok",
            },
            scan_type="Manual",
            attempt_id="a1",
        )
        update = coll.find_one_and_update.call_args[0][1]
        self.assertEqual(update["$set"]["consecutiveFailures"], 0)
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


if __name__ == "__main__":
    unittest.main()
