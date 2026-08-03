"""
Unit tests for Storm Protection alerts integrated with the Alerts module.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from bson import ObjectId

from routes.alert_routes import serialize_alert
from services.alert_service import (
    create_storm_recovery_alert,
    create_storm_recovery_failure_alert,
    create_storm_shutdown_alert,
    create_storm_shutdown_failure_alert,
)


def _incident(**overrides):
    base = {
        "incidentId": "storm-2026-000200",
        "deviceId": ObjectId("507f1f77bcf86cd799439011"),
        "hostname": "sw1",
        "ipAddress": "10.0.0.1",
        "interface": "Gi1/0/5",
        "severity": "CRITICAL",
        "status": "MITIGATED",
        "risk": {"riskScore": 88.0},
        "createdAt": datetime(2026, 8, 1, 8, 0, 0, tzinfo=timezone.utc),
        "mitigatedAt": datetime(2026, 8, 1, 8, 5, 0, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


class StormAlertServiceTests(unittest.TestCase):
    @patch("services.alert_service.log_audit")
    @patch("services.alert_service.db")
    def test_shutdown_success_creates_alert(self, mock_db, mock_audit):
        mock_db.alerts.insert_one.return_value = MagicMock(
            inserted_id=ObjectId("507f1f77bcf86cd7994390aa")
        )
        incident = _incident()

        alert_id = create_storm_shutdown_alert(incident)

        self.assertEqual(alert_id, "507f1f77bcf86cd7994390aa")
        mock_db.alerts.insert_one.assert_called_once()
        doc = mock_db.alerts.insert_one.call_args[0][0]
        self.assertEqual(doc["title"], "Automatic Port Shutdown")
        self.assertEqual(doc["severity"], "CRITICAL")
        self.assertEqual(doc["category"], "Storm Protection")
        self.assertEqual(doc["alertType"], "Storm Protection")
        self.assertEqual(doc["action"], "SHUTDOWN")
        self.assertEqual(doc["status"], "MITIGATED")
        self.assertEqual(doc["generatedBy"], "SYSTEM")
        self.assertEqual(doc["interface"], "Gi1/0/5")
        self.assertEqual(doc["incidentId"], "storm-2026-000200")
        self.assertEqual(doc["riskScore"], 88.0)
        self.assertIn("Storm detected on interface Gi1/0/5", doc["message"])
        self.assertFalse(doc["emailSent"])
        mock_audit.assert_called_once()
        details = mock_audit.call_args.kwargs["details"]
        self.assertEqual(details["action"], "SHUTDOWN")
        self.assertEqual(details["interface"], "Gi1/0/5")
        self.assertEqual(details["incident"], "storm-2026-000200")
        self.assertEqual(details["alertId"], alert_id)

    @patch("services.alert_service.log_audit")
    @patch("services.alert_service.db")
    def test_recovery_success_creates_alert(self, mock_db, mock_audit):
        mock_db.alerts.insert_one.return_value = MagicMock(
            inserted_id=ObjectId("507f1f77bcf86cd7994390bb")
        )
        incident = _incident(status="MONITORING")
        recovered_at = datetime(2026, 8, 1, 8, 20, 0, tzinfo=timezone.utc)

        alert_id = create_storm_recovery_alert(incident, recovered_at=recovered_at)

        self.assertEqual(alert_id, "507f1f77bcf86cd7994390bb")
        doc = mock_db.alerts.insert_one.call_args[0][0]
        self.assertEqual(doc["title"], "Automatic Port Recovery")
        self.assertEqual(doc["severity"], "INFO")
        self.assertEqual(doc["status"], "RECOVERED")
        self.assertEqual(doc["action"], "NO_SHUTDOWN")
        self.assertIn("Storm conditions cleared", doc["message"])
        self.assertIn("Gi1/0/5", doc["message"])
        self.assertIn("recoveryDuration", doc)
        self.assertTrue(doc["recoveryDuration"])
        mock_audit.assert_called_once()

    @patch("services.alert_service.log_audit")
    @patch("services.alert_service.db")
    def test_shutdown_failure_creates_alert(self, mock_db, mock_audit):
        mock_db.alerts.insert_one.return_value = MagicMock(
            inserted_id=ObjectId("507f1f77bcf86cd7994390cc")
        )

        alert_id = create_storm_shutdown_failure_alert(_incident(status="MITIGATION_FAILED"))

        self.assertEqual(alert_id, "507f1f77bcf86cd7994390cc")
        doc = mock_db.alerts.insert_one.call_args[0][0]
        self.assertEqual(doc["title"], "Automatic Shutdown Failed")
        self.assertEqual(doc["severity"], "CRITICAL")
        self.assertEqual(doc["status"], "MITIGATION_FAILED")
        self.assertIn("Manual investigation is required", doc["message"])
        mock_audit.assert_called_once()

    @patch("services.alert_service.log_audit")
    @patch("services.alert_service.db")
    def test_recovery_failure_creates_alert(self, mock_db, mock_audit):
        mock_db.alerts.insert_one.return_value = MagicMock(
            inserted_id=ObjectId("507f1f77bcf86cd7994390dd")
        )

        alert_id = create_storm_recovery_failure_alert(
            _incident(),
            action_status="FAILURE",
        )

        self.assertEqual(alert_id, "507f1f77bcf86cd7994390dd")
        doc = mock_db.alerts.insert_one.call_args[0][0]
        self.assertEqual(doc["title"], "Automatic Recovery Failed")
        self.assertEqual(doc["severity"], "WARNING")
        self.assertEqual(doc["status"], "FAILURE")
        self.assertIn("Manual intervention may be required", doc["message"])
        mock_audit.assert_called_once()

    def test_alert_appears_in_existing_alerts_api_serializer(self):
        alert_doc = {
            "_id": ObjectId("507f1f77bcf86cd7994390ee"),
            "deviceId": ObjectId("507f1f77bcf86cd799439011"),
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "deviceName": "sw1",
            "status": "MITIGATED",
            "message": "Storm detected on interface Gi1/0/5.",
            "title": "Automatic Port Shutdown",
            "scanType": "Storm Protection",
            "alertType": "Storm Protection",
            "category": "Storm Protection",
            "severity": "CRITICAL",
            "interface": "Gi1/0/5",
            "incidentId": "storm-2026-000200",
            "riskScore": 88.0,
            "action": "SHUTDOWN",
            "generatedBy": "SYSTEM",
            "emailSent": False,
            "acknowledged": False,
            "dismissed": False,
            "acknowledgedAt": None,
            "dismissedAt": None,
            "createdAt": datetime(2026, 8, 1, 8, 5, 0, tzinfo=timezone.utc),
        }

        serialized = serialize_alert(alert_doc)

        self.assertEqual(serialized["category"], "Storm Protection")
        self.assertEqual(serialized["alertType"], "Storm Protection")
        self.assertEqual(serialized["title"], "Automatic Port Shutdown")
        self.assertEqual(serialized["severity"], "CRITICAL")
        self.assertEqual(serialized["interface"], "Gi1/0/5")
        self.assertEqual(serialized["incidentId"], "storm-2026-000200")
        self.assertEqual(serialized["action"], "SHUTDOWN")
        self.assertEqual(serialized["generatedBy"], "SYSTEM")
        self.assertEqual(serialized["riskScore"], 88.0)
        # Frontend Alerts page consumes these fields from the same API payload
        self.assertIn("message", serialized)
        self.assertIn("status", serialized)
        self.assertIn("createdAt", serialized)


class StormAlertEngineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.device_id = ObjectId("507f1f77bcf86cd799439011")
        self.incident_id = "storm-2026-000201"
        self.interface = "Gi1/0/10"
        self.incident_doc = {
            "incidentId": self.incident_id,
            "deviceId": self.device_id,
            "interface": self.interface,
            "status": "READY_FOR_MITIGATION",
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "severity": "CRITICAL",
            "risk": {"riskScore": 91.0},
        }
        self.device_doc = {
            "_id": self.device_id,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "credentials": {
                "sshUsername": "admin",
                "sshPassword": "password",
                "sshVendor": "cisco_ios",
            },
        }

    def _mock_system_gates(self, fake_db):
        fake_db.storm_confirmation_history.find_one.return_value = {
            "confirmed": True,
            "state": "CONFIRMED",
        }
        fake_db.storm_safety_history.find_one.return_value = {"safe": True}

    @patch("services.storm.mitigation.engine.record_mitigation_history")
    @patch("services.storm.mitigation.engine.append_timeline_event")
    @patch("services.storm.mitigation.engine.get_incident")
    @patch("services.storm.mitigation.engine._db")
    @patch("services.storm.mitigation.ssh_executor.SSHInterfaceCollector")
    def test_shutdown_success_creates_alert_even_if_email_fails(
        self, mock_ssh, mock_db_fn, mock_get_incident, _timeline, _history
    ):
        from services.storm.mitigation.engine import execute_mitigation

        mock_get_incident.return_value = self.incident_doc
        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        self._mock_system_gates(fake_db)
        mock_db_fn.return_value = fake_db

        mock_collector = MagicMock()
        mock_ssh.return_value = mock_collector
        mock_collector.run_command.side_effect = lambda cmd, wait=0.4: (
            "interface GigabitEthernet1/0/10\n shutdown\n" if "show" in cmd else "OK"
        )

        with patch(
            "services.storm.mitigation.engine.LockService.acquire_mitigation_locks",
            return_value=("device:lock", "interface:lock"),
        ), patch(
            "services.alert_service.create_storm_shutdown_alert",
            return_value="alert-1",
        ) as mock_alert, patch(
            "services.email_service.send_storm_shutdown_notification",
            side_effect=RuntimeError("SMTP down"),
        ) as mock_email, patch(
            "services.alert_service.mark_alert_email_sent",
        ):
            res = execute_mitigation(self.incident_id, "SHUTDOWN", operator="SYSTEM")

        self.assertTrue(res["success"])
        mock_alert.assert_called_once()
        mock_email.assert_called_once()

    @patch("services.storm.mitigation.engine.record_mitigation_history")
    @patch("services.storm.mitigation.engine.append_timeline_event")
    @patch("services.storm.mitigation.engine.execute_rollback", return_value=(True, ["no shutdown"]))
    @patch("services.storm.mitigation.engine.get_incident")
    @patch("services.storm.mitigation.engine._db")
    @patch("services.storm.mitigation.ssh_executor.SSHInterfaceCollector")
    def test_shutdown_failure_creates_alert(
        self, mock_ssh, mock_db_fn, mock_get_incident, _rollback, _timeline, _history
    ):
        from services.storm.mitigation.engine import execute_mitigation

        mock_get_incident.return_value = self.incident_doc
        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        self._mock_system_gates(fake_db)
        mock_db_fn.return_value = fake_db

        mock_collector = MagicMock()
        mock_ssh.return_value = mock_collector
        mock_collector.run_command.side_effect = RuntimeError("SSH dropped")

        with patch(
            "services.storm.mitigation.engine.LockService.acquire_mitigation_locks",
            return_value=("device:lock", "interface:lock"),
        ), patch(
            "services.alert_service.create_storm_shutdown_failure_alert",
            return_value="alert-fail-1",
        ) as mock_alert, patch(
            "services.email_service.send_storm_mitigation_failure",
            return_value=False,
        ) as mock_email, patch(
            "services.alert_service.mark_alert_email_sent",
        ):
            res = execute_mitigation(self.incident_id, "SHUTDOWN", operator="SYSTEM")

        self.assertFalse(res["success"])
        mock_alert.assert_called_once()
        mock_email.assert_called_once()

    @patch("services.storm.recovery.engine.collect_post_recovery_stats", return_value={})
    @patch("services.storm.recovery.engine.record_recovery_history")
    @patch("services.storm.recovery.engine.append_timeline_event")
    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine._db")
    @patch("services.storm.recovery.engine.get_settings")
    @patch("services.storm.mitigation.ssh_executor.SSHInterfaceCollector")
    def test_recovery_success_creates_alert_even_if_email_fails(
        self,
        mock_ssh,
        mock_settings,
        mock_db_fn,
        mock_get_incident,
        _timeline,
        _history,
        _stats,
    ):
        from services.storm.recovery.engine import execute_recovery

        mock_settings.return_value = {
            "maximumRecoveryAttempts": 3,
            "stabilizationSeconds": 60,
        }
        mitigated = dict(self.incident_doc, status="MITIGATED", recoveryRetryCount=0)
        mock_get_incident.return_value = mitigated
        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        mock_db_fn.return_value = fake_db

        mock_collector = MagicMock()
        mock_ssh.return_value = mock_collector
        mock_collector.run_command.side_effect = lambda cmd, wait=0.4: (
            "interface GigabitEthernet1/0/10\n no shutdown\n" if "show" in cmd else "OK"
        )

        with patch(
            "services.storm.recovery.engine.LockService.acquire_recovery_locks",
            return_value=("device:lock", "interface:lock"),
        ), patch(
            "services.storm.recovery.engine.verify_interface_up",
            return_value=(True, "admin is up"),
        ), patch(
            "services.storm.recovery.post_recovery.invalidate_pipeline_after_recovery",
        ), patch(
            "services.alert_service.create_storm_recovery_alert",
            return_value="alert-rec-1",
        ) as mock_alert, patch(
            "services.email_service.send_storm_recovery_notification",
            side_effect=RuntimeError("SMTP down"),
        ) as mock_email, patch(
            "services.alert_service.mark_alert_email_sent",
        ):
            res = execute_recovery(
                self.incident_id,
                operator="SYSTEM",
                skip_policy_validation=True,
            )

        self.assertTrue(res["success"])
        mock_alert.assert_called_once()
        mock_email.assert_called_once()

    @patch("services.storm.recovery.engine.record_recovery_history")
    @patch("services.storm.recovery.engine.append_timeline_event")
    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine._db")
    @patch("services.storm.recovery.engine.get_settings")
    @patch("services.storm.mitigation.ssh_executor.SSHInterfaceCollector")
    def test_recovery_failure_creates_alert(
        self,
        mock_ssh,
        mock_settings,
        mock_db_fn,
        mock_get_incident,
        _timeline,
        _history,
    ):
        from services.storm.recovery.engine import execute_recovery

        mock_settings.return_value = {
            "maximumRecoveryAttempts": 3,
            "stabilizationSeconds": 60,
        }
        mitigated = dict(self.incident_doc, status="MITIGATED", recoveryRetryCount=0)
        mock_get_incident.return_value = mitigated
        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        mock_db_fn.return_value = fake_db

        mock_collector = MagicMock()
        mock_ssh.return_value = mock_collector
        mock_collector.run_command.side_effect = RuntimeError("Recovery SSH failed")

        with patch(
            "services.storm.recovery.engine.LockService.acquire_recovery_locks",
            return_value=("device:lock", "interface:lock"),
        ), patch(
            "services.alert_service.create_storm_recovery_failure_alert",
            return_value="alert-rec-fail",
        ) as mock_alert, patch(
            "services.email_service.send_storm_recovery_failure",
            return_value=False,
        ) as mock_email, patch(
            "services.alert_service.mark_alert_email_sent",
        ):
            res = execute_recovery(
                self.incident_id,
                operator="SYSTEM",
                skip_policy_validation=True,
            )

        self.assertFalse(res["success"])
        mock_alert.assert_called_once()
        mock_email.assert_called_once()


if __name__ == "__main__":
    unittest.main()
