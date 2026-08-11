"""
Tests for dedicated ISP connectivity monitoring.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

from bson import ObjectId

from app import app
from models.isp_connection import STATUS_OFFLINE, STATUS_ONLINE
from utils.auth import create_access_token
from utils.utc import utc_now


class IspServiceTests(unittest.TestCase):
    @patch("services.isp_service.db")
    def test_create_isp(self, mock_db):
        from services.isp_service import create_isp_record

        mock_db.ispConnections.count_documents.return_value = 0
        mock_db.ispConnections.find.return_value = []
        mock_db.ispConnections.insert_one.side_effect = lambda doc: None

        doc = create_isp_record(name="Primary ISP", target="8.8.8.8", monitor=True)

        self.assertEqual(doc["name"], "Primary ISP")
        self.assertEqual(doc["target"], "8.8.8.8")
        self.assertTrue(doc["monitor"])
        mock_db.ispConnections.insert_one.assert_called_once()

    @patch("services.isp_service.db")
    def test_list_and_get_isp(self, mock_db):
        from services.isp_service import get_isp_connection, list_isp_connections

        sample = {
            "_id": "isp-1",
            "name": "ISP 1",
            "target": "1.1.1.1",
            "monitor": True,
        }
        mock_db.ispConnections.find.return_value.sort.return_value = [sample]
        mock_db.ispConnections.find_one.return_value = sample

        listed = list_isp_connections()
        self.assertEqual(len(listed), 1)
        fetched = get_isp_connection("isp-1")
        self.assertIsNotNone(fetched)
        assert fetched is not None  # narrow Optional for type checkers
        self.assertEqual(fetched["name"], "ISP 1")

    @patch("services.isp_service.db")
    def test_update_isp(self, mock_db):
        from services.isp_service import update_isp_record

        existing = {"_id": "isp-1", "name": "Old", "target": "", "monitor": False}
        updated = {**existing, "name": "New ISP", "target": "8.8.4.4", "monitor": True}
        mock_db.ispConnections.find_one.side_effect = [existing, updated]

        result = update_isp_record(
            "isp-1",
            name="New ISP",
            target="8.8.4.4",
            monitor=True,
        )

        self.assertIsNotNone(result)
        assert result is not None  # narrow Optional for type checkers
        self.assertEqual(result["name"], "New ISP")
        mock_db.ispConnections.update_one.assert_called_once()

    @patch("services.isp_service.db")
    def test_delete_isp(self, mock_db):
        from services.isp_service import delete_isp_record

        mock_db.ispConnections.delete_one.return_value.deleted_count = 1
        self.assertTrue(delete_isp_record("isp-1"))


class IspMonitorApplyTests(unittest.TestCase):
    @patch("services.isp_monitor_service.get_failure_confirmation_scans", return_value=2)
    @patch("services.isp_monitor_service._db")
    def test_successful_isp_ping(self, mock_db, _threshold):
        from services.isp_monitor_service import apply_isp_ping_result

        started = utc_now()
        isp = {
            "_id": "isp-1",
            "name": "ISP 1",
            "target": "8.8.8.8",
            "status": "Unknown",
            "consecutiveFailures": 0,
        }
        updated = {
            **isp,
            "status": STATUS_ONLINE,
            "responseTime": 18.5,
            "consecutiveFailures": 0,
            "lastPingAttemptId": "attempt-1",
            "lastPingStartedAt": started,
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = updated
        mock_db.return_value.ispConnections = coll

        status = apply_isp_ping_result(
            isp,
            {
                "success": True,
                "responseTime": 18.5,
                "lastSeen": started,
                "pingStartedAt": started,
                "pingCompletedAt": started,
            },
            attempt_id="attempt-1",
        )

        self.assertEqual(status, STATUS_ONLINE)
        update = coll.find_one_and_update.call_args[0][1]["$set"]
        self.assertEqual(update["status"], STATUS_ONLINE)
        self.assertEqual(update["consecutiveFailures"], 0)
        self.assertEqual(update["responseTime"], 18.5)

    @patch("services.isp_monitor_service.get_failure_confirmation_scans", return_value=2)
    @patch("services.isp_monitor_service._db")
    def test_failed_isp_ping_increments_once(self, mock_db, _threshold):
        from services.isp_monitor_service import apply_isp_ping_result

        started = utc_now()
        isp = {
            "_id": "isp-1",
            "name": "ISP 1",
            "target": "8.8.8.8",
            "status": STATUS_ONLINE,
            "consecutiveFailures": 0,
        }
        updated = {
            **isp,
            "status": STATUS_ONLINE,
            "consecutiveFailures": 1,
            "lastPingAttemptId": "attempt-1",
            "lastPingStartedAt": started,
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = updated
        mock_db.return_value.ispConnections = coll

        apply_isp_ping_result(
            isp,
            {
                "success": False,
                "pingStartedAt": started,
                "pingCompletedAt": started,
            },
            attempt_id="attempt-1",
        )

        pipeline = coll.find_one_and_update.call_args[0][1]
        self.assertIsInstance(pipeline, list)
        self.assertEqual(pipeline[0]["$set"]["consecutiveFailures"]["$add"][1], 1)

    @patch("services.isp_monitor_service.get_failure_confirmation_scans", return_value=2)
    @patch("services.isp_monitor_service._db")
    def test_failure_hysteresis(self, mock_db, _threshold):
        from services.isp_monitor_service import apply_isp_ping_result

        started = utc_now()
        isp = {
            "_id": "isp-1",
            "name": "ISP 1",
            "target": "8.8.8.8",
            "status": STATUS_ONLINE,
            "consecutiveFailures": 1,
        }
        still_online = {
            **isp,
            "status": STATUS_ONLINE,
            "consecutiveFailures": 1,
            "lastPingAttemptId": "attempt-1",
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = still_online
        mock_db.return_value.ispConnections = coll

        status = apply_isp_ping_result(
            isp,
            {"success": False, "pingStartedAt": started, "pingCompletedAt": started},
            attempt_id="attempt-1",
        )
        self.assertEqual(status, STATUS_ONLINE)

        offline = {**still_online, "status": STATUS_OFFLINE, "consecutiveFailures": 2}
        coll.find_one_and_update.return_value = offline
        status = apply_isp_ping_result(
            {**isp, "consecutiveFailures": 1},
            {"success": False, "pingStartedAt": started, "pingCompletedAt": started},
            attempt_id="attempt-2",
        )
        self.assertEqual(status, STATUS_OFFLINE)

    @patch("services.isp_monitor_service.get_failure_confirmation_scans", return_value=1)
    @patch("services.isp_monitor_service._db")
    def test_success_resets_consecutive_failures(self, mock_db, _threshold):
        from services.isp_monitor_service import apply_isp_ping_result

        started = utc_now()
        isp = {
            "_id": "isp-1",
            "name": "ISP 1",
            "target": "8.8.8.8",
            "status": STATUS_OFFLINE,
            "consecutiveFailures": 3,
        }
        updated = {
            **isp,
            "status": STATUS_ONLINE,
            "consecutiveFailures": 0,
            "lastPingAttemptId": "attempt-1",
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = updated
        mock_db.return_value.ispConnections = coll

        apply_isp_ping_result(
            isp,
            {
                "success": True,
                "responseTime": 12.0,
                "lastSeen": started,
                "pingStartedAt": started,
                "pingCompletedAt": started,
            },
            attempt_id="attempt-1",
        )

        update = coll.find_one_and_update.call_args[0][1]["$set"]
        self.assertEqual(update["consecutiveFailures"], 0)

    @patch("services.isp_monitor_service.get_failure_confirmation_scans", return_value=1)
    @patch("services.isp_monitor_service._db")
    def test_failed_ping_does_not_update_last_seen(self, mock_db, _threshold):
        from services.isp_monitor_service import apply_isp_ping_result

        started = utc_now()
        isp = {
            "_id": "isp-1",
            "name": "ISP 1",
            "target": "8.8.8.8",
            "status": STATUS_ONLINE,
            "lastSeen": started - timedelta(minutes=5),
        }
        updated = {
            **isp,
            "status": STATUS_OFFLINE,
            "consecutiveFailures": 1,
            "lastPingAttemptId": "attempt-1",
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = updated
        mock_db.return_value.ispConnections = coll

        apply_isp_ping_result(
            isp,
            {"success": False, "pingStartedAt": started, "pingCompletedAt": started},
            attempt_id="attempt-1",
        )

        pipeline = coll.find_one_and_update.call_args[0][1]
        self.assertNotIn("lastSeen", pipeline[0]["$set"])

    @patch("services.isp_monitor_service.get_failure_confirmation_scans", return_value=1)
    @patch("services.isp_monitor_service._db")
    def test_stale_result_rejected(self, mock_db, _threshold):
        from services.isp_monitor_service import apply_isp_ping_result

        older = utc_now() - timedelta(minutes=2)
        newer = utc_now()
        isp = {
            "_id": "isp-1",
            "name": "ISP 1",
            "target": "8.8.8.8",
            "status": STATUS_ONLINE,
            "lastPingStartedAt": newer,
            "lastPingAttemptId": "newer-attempt",
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = None
        coll.find_one.return_value = isp
        mock_db.return_value.ispConnections = coll

        status = apply_isp_ping_result(
            isp,
            {
                "success": False,
                "pingStartedAt": older,
                "pingCompletedAt": older,
            },
            attempt_id="older-attempt",
        )

        self.assertEqual(status, STATUS_ONLINE)
        coll.find_one_and_update.assert_called_once()

    @patch("services.isp_monitor_service.get_failure_confirmation_scans", return_value=1)
    @patch("services.isp_monitor_service._db")
    def test_duplicate_attempt_idempotent(self, mock_db, _threshold):
        from services.isp_monitor_service import apply_isp_ping_result

        started = utc_now()
        isp = {
            "_id": "isp-1",
            "name": "ISP 1",
            "target": "8.8.8.8",
            "status": STATUS_ONLINE,
            "lastPingAttemptId": "attempt-1",
            "lastPingStartedAt": started,
            "consecutiveFailures": 1,
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = None
        coll.find_one.return_value = isp
        mock_db.return_value.ispConnections = coll

        status = apply_isp_ping_result(
            isp,
            {"success": False, "pingStartedAt": started, "pingCompletedAt": started},
            attempt_id="attempt-1",
        )

        self.assertEqual(status, STATUS_ONLINE)


class IspSchedulerTests(unittest.TestCase):
    @patch("services.isp_monitor_service.require_scheduler_leadership", return_value=True)
    @patch("services.isp_monitor_service.CycleLeadershipGuard.ensure", return_value=True)
    @patch("services.isp_monitor_service._db")
    @patch("services.isp_monitor_service.scan_isp_connection")
    def test_disabled_isp_not_scanned(self, mock_scan, mock_db, _guard, _leadership):
        from services.isp_monitor_service import monitor_all_isp_connections

        # monitor=False entries are excluded by the Mongo query filter.
        mock_db.return_value.ispConnections.find.return_value = []

        monitor_all_isp_connections()
        mock_scan.assert_not_called()

    @patch("services.isp_monitor_service.require_scheduler_leadership", return_value=True)
    @patch("services.isp_monitor_service.CycleLeadershipGuard.ensure", return_value=True)
    @patch("services.isp_monitor_service._db")
    @patch("services.isp_monitor_service._scan_isp_safe", return_value="scanned")
    @patch("services.isp_monitor_service._should_check_now", return_value=True)
    def test_enabled_isp_scanned(self, _due, mock_scan_safe, mock_db, _guard, _leadership):
        from services.isp_monitor_service import monitor_all_isp_connections

        mock_db.return_value.ispConnections.find.return_value = [
            {
                "_id": "isp-1",
                "name": "ISP 1",
                "target": "8.8.8.8",
                "monitor": True,
            }
        ]

        monitor_all_isp_connections()
        mock_scan_safe.assert_called_once()


class IspRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def _auth_headers(self, role: str = "admin") -> dict[str, str]:
        token = create_access_token(
            user_id=str(ObjectId()),
            username="admin",
            role=role,
        )
        return {"Authorization": f"Bearer {token}"}

    @patch("routes.isp_routes.list_isp_connections")
    def test_get_isps(self, mock_list):
        mock_list.return_value = [
            {
                "_id": "isp-1",
                "name": "ISP 1",
                "target": "8.8.8.8",
                "monitor": True,
                "status": STATUS_ONLINE,
                "responseTime": 12.0,
                "lastSeen": utc_now(),
                "lastCheckedAt": utc_now(),
                "consecutiveFailures": 0,
                "createdAt": utc_now(),
                "updatedAt": utc_now(),
            }
        ]
        res = self.client.get("/api/isps", headers=self._auth_headers())
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["data"][0]["id"], "isp-1")

    @patch("routes.isp_routes.scan_isp_connection")
    @patch("routes.isp_routes.get_isp_connection")
    def test_manual_isp_scan(self, mock_get, mock_scan):
        isp_doc = {
            "_id": "isp-1",
            "name": "ISP 1",
            "target": "8.8.8.8",
            "monitor": True,
            "status": STATUS_ONLINE,
            "responseTime": 10.0,
            "lastSeen": utc_now(),
            "lastCheckedAt": utc_now(),
            "consecutiveFailures": 0,
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
        }
        mock_get.side_effect = [isp_doc, isp_doc]
        mock_scan.return_value = {"success": True, "message": "Device is reachable"}

        res = self.client.post("/api/isps/isp-1/scan", headers=self._auth_headers())
        self.assertEqual(res.status_code, 200)
        mock_scan.assert_called_once()
        self.assertTrue(res.get_json()["success"])


if __name__ == "__main__":
    unittest.main()
