"""
Tests for post-recovery pipeline invalidation and remmitigation freshness.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from bson import ObjectId

from services.storm.recovery.post_recovery import invalidate_pipeline_after_recovery
from services.storm.recovery.scheduler import run_recovery_cycle


class PostRecoveryInvalidationTests(unittest.TestCase):
    def setUp(self):
        self.device_id = ObjectId("507f1f77bcf86cd799439011")
        self.interface = "Gi1/0/5"
        self.incident_id = "storm-2026-000099"

    @patch("services.storm.recovery.post_recovery._db")
    def test_invalidate_writes_reset_and_cancels_ready(self, mock_db_fn):
        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = {
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
        }
        fake_db.storm_incidents.update_many.return_value = MagicMock(modified_count=2)
        mock_db_fn.return_value = fake_db

        res = invalidate_pipeline_after_recovery(
            self.device_id,
            self.interface,
            incident_id=self.incident_id,
            reason="Post-recovery reset",
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["cancelledIncidents"], 2)

        confirm_doc = fake_db.storm_confirmation_history.insert_one.call_args[0][0]
        self.assertFalse(confirm_doc["confirmed"])
        self.assertEqual(confirm_doc["state"], "NOT_CONFIRMED")
        self.assertEqual(confirm_doc["consecutiveHighSamples"], 0)
        self.assertTrue(confirm_doc["reset"])
        self.assertNotIn("pipelineGeneration", confirm_doc)

        safety_doc = fake_db.storm_safety_history.insert_one.call_args[0][0]
        self.assertFalse(safety_doc["safe"])
        self.assertEqual(safety_doc["failedRule"], "POST_RECOVERY")
        self.assertEqual(safety_doc["status"], "UNSAFE")
        self.assertNotIn("pipelineGeneration", safety_doc)

        cancel_query = fake_db.storm_incidents.update_many.call_args[0][0]
        self.assertEqual(cancel_query["incidentId"], {"$ne": self.incident_id})
        self.assertIn("READY_FOR_MITIGATION", cancel_query["status"]["$in"])


class RecoverySchedulerFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.device_id = ObjectId("507f1f77bcf86cd799439011")
        self.incident_id = "storm-2026-000050"
        self.interface = "Gi1/0/5"
        self.recovered_at = datetime.now(timezone.utc) - timedelta(seconds=30)

    @patch("services.storm.recovery.scheduler.get_settings")
    @patch("services.storm.recovery.scheduler.record_recovery_history")
    @patch("services.storm.recovery.scheduler.trigger_re_mitigation")
    @patch("services.storm.recovery.scheduler.db")
    def test_stale_pre_recovery_confirmation_does_not_remitigate(
        self, mock_db, mock_trigger, mock_record, mock_settings
    ):
        mock_settings.return_value = {
            "autoRecovery": False,
            "reMitigationThreshold": 75.0,
            "maximumRecoveryAttempts": 3,
        }
        inc = {
            "incidentId": self.incident_id,
            "deviceId": self.device_id,
            "interface": self.interface,
            "status": "MONITORING",
            "recoveredAt": self.recovered_at,
            "stabilizationEnd": datetime.now(timezone.utc) + timedelta(minutes=5),
            "recoveryRetryCount": 0,
        }

        def find_side_effect(query):
            if query.get("status") == "MONITORING":
                return [inc]
            return []

        mock_db.storm_incidents.find.side_effect = find_side_effect
        # Stale confirmation from BEFORE recovery
        mock_db.storm_confirmation_history.find_one.return_value = {
            "confirmed": True,
            "state": "CONFIRMED",
            "timestamp": self.recovered_at - timedelta(hours=3),
            "reset": False,
        }
        mock_db.storm_risk_history.find_one.return_value = {
            "riskScore": 10.0,
            "timestamp": self.recovered_at - timedelta(hours=3),
        }

        run_recovery_cycle()
        mock_trigger.assert_not_called()
        mock_record.assert_not_called()

    @patch("services.storm.recovery.scheduler.get_settings")
    @patch("services.storm.recovery.scheduler.record_recovery_history")
    @patch("services.storm.recovery.scheduler.trigger_re_mitigation")
    @patch("services.storm.recovery.scheduler.db")
    def test_post_recovery_confirmation_does_remitigate(
        self, mock_db, mock_trigger, mock_record, mock_settings
    ):
        mock_settings.return_value = {
            "autoRecovery": False,
            "reMitigationThreshold": 75.0,
            "maximumRecoveryAttempts": 3,
        }
        inc = {
            "incidentId": self.incident_id,
            "deviceId": self.device_id,
            "interface": self.interface,
            "status": "MONITORING",
            "recoveredAt": self.recovered_at,
            "stabilizationEnd": datetime.now(timezone.utc) + timedelta(minutes=5),
            "recoveryRetryCount": 0,
        }

        def find_side_effect(query):
            if query.get("status") == "MONITORING":
                return [inc]
            return []

        mock_db.storm_incidents.find.side_effect = find_side_effect
        mock_db.storm_confirmation_history.find_one.return_value = {
            "confirmed": True,
            "state": "CONFIRMED",
            "timestamp": self.recovered_at + timedelta(seconds=10),
            "reset": False,
        }
        mock_db.storm_risk_history.find_one.return_value = {
            "riskScore": 10.0,
            "timestamp": self.recovered_at + timedelta(seconds=10),
        }
        mock_trigger.return_value = {
            "success": True,
            "incidentId": "storm-2026-000051",
            "status": "SUCCESS",
        }

        run_recovery_cycle()
        mock_trigger.assert_called_once()
        self.assertIn("after recovery", mock_trigger.call_args[0][1].lower())


if __name__ == "__main__":
    unittest.main()
