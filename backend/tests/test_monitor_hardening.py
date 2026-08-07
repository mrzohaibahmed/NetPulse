"""
Unit tests for ping monitoring hardening (Phases 1–5, 11).

Uses mongomock-style manual mocks where possible to avoid live Mongo.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bson import ObjectId


class TestUtcHelpers(unittest.TestCase):
    def test_utc_now_aware(self):
        from utils.utc import utc_now

        now = utc_now()
        self.assertIsNotNone(now.tzinfo)

    def test_ensure_utc_naive(self):
        from datetime import datetime

        from utils.utc import ensure_utc

        naive = datetime(2024, 1, 1, 12, 0, 0)
        aware = ensure_utc(naive)
        self.assertIsNotNone(aware.tzinfo)


class TestMonitorIntegrity(unittest.TestCase):
    def test_invalid_status_detected(self):
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

    def test_online_with_failures(self):
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
        self.assertIn("online_with_failures", issues)


class TestAtomicApplyPingResult(unittest.TestCase):
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
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = updated
        mock_db.return_value.devices = coll

        result = {
            "success": True,
            "status": "Online",
            "responseTime": 1.5,
            "lastSeen": utc_now(),
            "message": "ok",
        }
        apply_ping_result(device, result, scan_type="Manual")

        coll.find_one_and_update.assert_called_once()
        args, kwargs = coll.find_one_and_update.call_args
        update = args[1]
        self.assertEqual(update["$set"]["consecutiveFailures"], 0)
        self.assertEqual(update["$set"]["status"], "Online")
        mock_hist.assert_called_once()
        mock_resolve.assert_called_once()

    @patch("services.monitor_service.maybe_send_critical_offline_alert")
    @patch("services.monitor_service.save_ping_history")
    @patch("services.monitor_service._db")
    def test_failure_uses_inc(self, mock_db, mock_hist, mock_alert):
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
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = updated
        mock_db.return_value.devices = coll

        result = {
            "success": False,
            "status": "Offline (Critical)",
            "responseTime": None,
            "lastSeen": None,
            "message": "unreachable",
        }
        apply_ping_result(device, result, scan_type="Automatic")

        args, kwargs = coll.find_one_and_update.call_args
        update = args[1]
        self.assertEqual(update["$inc"]["consecutiveFailures"], 1)
        self.assertNotIn("lastSeen", update["$set"])
        mock_alert.assert_called_once()
        # consecutive_failures is keyword-only after previous_status/new_status
        kwargs = mock_alert.call_args.kwargs
        self.assertEqual(kwargs.get("consecutive_failures"), 3)

    @patch("services.monitor_service.save_ping_history")
    @patch("services.monitor_service._db")
    def test_partition_suppresses_offline(self, mock_db, mock_hist):
        from services.monitor_service import apply_ping_result

        device_id = ObjectId()
        device = {
            "_id": device_id,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "status": "Online",
            "critical": False,
            "consecutiveFailures": 0,
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = {**device}
        mock_db.return_value.devices = coll

        result = {
            "success": False,
            "status": "Not Reachable",
            "responseTime": None,
            "lastSeen": None,
            "message": "unreachable",
        }
        apply_ping_result(
            device,
            result,
            scan_type="Automatic",
            suppress_offline=True,
            cycle_id="abc",
        )

        args, kwargs = coll.find_one_and_update.call_args
        update = args[1]
        self.assertIn("lastCheckedAt", update["$set"])
        self.assertNotIn("status", update["$set"])
        mock_hist.assert_not_called()


class TestAlertIdempotency(unittest.TestCase):
    @patch("services.alert_service.send_critical_offline_alert", return_value=True)
    @patch("services.alert_service.db")
    def test_skips_when_active_alert_exists(self, mock_db, _email):
        from services.alert_service import maybe_send_critical_offline_alert

        device = {
            "_id": ObjectId(),
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "critical": True,
        }
        mock_db.alerts.find_one.return_value = {"_id": ObjectId()}
        ok = maybe_send_critical_offline_alert(
            device,
            "Online",
            "Offline (Critical)",
            consecutive_failures=3,
        )
        self.assertFalse(ok)
        mock_db.alerts.insert_one.assert_not_called()


class TestSchedulerOwnershipHelpers(unittest.TestCase):
    def test_owner_id_stable(self):
        from services.scheduler_ownership import get_owner_id

        a = get_owner_id()
        b = get_owner_id()
        self.assertEqual(a, b)
        self.assertIn(":", a)


class TestMonitorEvents(unittest.TestCase):
    def test_publish_invokes_subscriber(self):
        from services import monitor_events as ev

        seen = []

        def handler(event_type, payload):
            seen.append((event_type, payload.get("deviceId")))

        ev.subscribe(handler)
        try:
            ev.publish(ev.EVENT_DEVICE_STATUS_CHANGED, {"deviceId": "x"})
            self.assertTrue(any(t == ev.EVENT_DEVICE_STATUS_CHANGED for t, _ in seen))
        finally:
            ev.unsubscribe(handler)


if __name__ == "__main__":
    unittest.main()
