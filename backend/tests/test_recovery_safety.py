"""
Unit tests for the Recovery Safety Engine (R1–R8).

Mitigation Safety Engine must not be invoked from these paths.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from bson import ObjectId

from services.storm.recovery.policy import validate_recovery_policy
from services.storm.recovery.safety import evaluate_recovery_safety


class RecoverySafetyTests(unittest.TestCase):
    def setUp(self):
        self.device_id = ObjectId("507f1f77bcf86cd799439011")
        self.incident_id = "storm-2026-000010"
        self.interface = "Gi1/0/5"
        self.created_at = datetime.now(timezone.utc) - timedelta(hours=1)

        self.incident_doc = {
            "incidentId": self.incident_id,
            "deviceId": self.device_id,
            "interface": self.interface,
            "status": "MITIGATED",
            "incidentType": "STORM",
            "createdAt": self.created_at,
            "recoveryRetryCount": 0,
        }
        self.device_doc = {
            "_id": self.device_id,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "status": "Online",
            "credentials": {
                "sshUsername": "admin",
                "sshPassword": "password",
                "sshVendor": "cisco_ios",
                "sshPort": 22,
            },
        }

    def _db_mock(
        self,
        *,
        confirmation=None,
        risk=None,
        mitigation_ago_minutes=10,
        newer_incident=None,
        iface_admin="down",
    ):
        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        fake_db.storm_mitigation_history.find_one.return_value = {
            "timestamp": datetime.now(timezone.utc)
            - timedelta(minutes=mitigation_ago_minutes),
            "status": "SUCCESS",
        }
        fake_db.storm_confirmation_history.find_one.return_value = confirmation or {
            "confirmed": False
        }
        fake_db.storm_risk_history.find_one.return_value = risk or {"riskScore": 10.0}
        fake_db.storm_incidents.find_one.return_value = newer_incident
        fake_db.interfaces.find_one.return_value = {"adminStatus": iface_admin}
        return fake_db

    def _pass_ssh(self, mock_ssh_exec, admin_status="down"):
        collector = MagicMock()
        collector.run_command.return_value = (
            f"{self.interface} is administratively down, line protocol is down"
        )
        mock_ssh_exec.return_value.__enter__.return_value.collector = collector
        return collector

    @patch("services.storm.recovery.safety.recovery_locks_available", return_value=True)
    @patch("services.storm.recovery.safety.parse_interface_snapshot", create=True)
    @patch("services.storm.recovery.safety.SSHMitigationExecutor")
    @patch("services.storm.recovery.safety.get_settings")
    @patch("services.storm.recovery.safety.get_incident")
    @patch("services.storm.recovery.safety._db")
    def test_storm_cleared_and_recovery_succeeds(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_settings,
        mock_ssh,
        _mock_parse,
        _mock_locks,
    ):
        mock_get_incident.return_value = self.incident_doc
        mock_settings.return_value = {
            "cooldownMinutes": 5,
            "reMitigationThreshold": 75.0,
        }
        mock_db_fn.return_value = self._db_mock()
        self._pass_ssh(mock_ssh)

        with patch(
            "services.storm.diagnostics.snapshots.parse_interface_snapshot",
            return_value={"adminStatus": "down", "available": True},
        ):
            res = evaluate_recovery_safety(self.incident_id)

        self.assertTrue(res.safe)
        self.assertIsNone(res.failed_rule)
        self.assertTrue(res.checks["stormCleared"])
        self.assertTrue(res.checks["cooldownExpired"])
        self.assertTrue(res.checks["deviceReachable"])
        self.assertTrue(res.checks["sshReachable"])
        self.assertTrue(res.checks["interfaceAdminDown"])
        self.assertTrue(res.checks["noNewerActiveIncident"])
        self.assertTrue(res.checks["recoveryLockAvailable"])

    @patch("services.storm.recovery.safety.recovery_locks_available", return_value=True)
    @patch("services.storm.recovery.safety.SSHMitigationExecutor")
    @patch("services.storm.recovery.safety.get_settings")
    @patch("services.storm.recovery.safety.get_incident")
    @patch("services.storm.recovery.safety._db")
    def test_blocked_because_cooldown(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_settings,
        mock_ssh,
        _mock_locks,
    ):
        mock_get_incident.return_value = self.incident_doc
        mock_settings.return_value = {
            "cooldownMinutes": 5,
            "reMitigationThreshold": 75.0,
        }
        mock_db_fn.return_value = self._db_mock(mitigation_ago_minutes=1)

        res = evaluate_recovery_safety(self.incident_id)

        self.assertFalse(res.safe)
        self.assertEqual(res.failed_rule, "R3")
        self.assertFalse(res.checks["cooldownExpired"])
        mock_ssh.assert_not_called()  # SSH deferred until cheap checks pass

    @patch("services.storm.recovery.safety.recovery_locks_available", return_value=True)
    @patch("services.storm.recovery.safety.SSHMitigationExecutor")
    @patch("services.storm.recovery.safety.get_settings")
    @patch("services.storm.recovery.safety.get_incident")
    @patch("services.storm.recovery.safety._db")
    def test_blocked_because_device_offline(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_settings,
        mock_ssh,
        _mock_locks,
    ):
        mock_get_incident.return_value = self.incident_doc
        mock_settings.return_value = {
            "cooldownMinutes": 5,
            "reMitigationThreshold": 75.0,
        }
        offline = dict(self.device_doc)
        offline["status"] = "Not Reachable"
        fake_db = self._db_mock()
        fake_db.devices.find_one.return_value = offline
        mock_db_fn.return_value = fake_db

        res = evaluate_recovery_safety(self.incident_id)

        self.assertFalse(res.safe)
        self.assertEqual(res.failed_rule, "R4")
        self.assertFalse(res.checks["deviceReachable"])
        mock_ssh.assert_not_called()

    @patch("services.storm.recovery.safety.recovery_locks_available", return_value=True)
    @patch("services.storm.recovery.safety.SSHMitigationExecutor")
    @patch("services.storm.recovery.safety.get_settings")
    @patch("services.storm.recovery.safety.get_incident")
    @patch("services.storm.recovery.safety._db")
    def test_blocked_because_ssh_unavailable(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_settings,
        mock_ssh,
        _mock_locks,
    ):
        mock_get_incident.return_value = self.incident_doc
        mock_settings.return_value = {
            "cooldownMinutes": 5,
            "reMitigationThreshold": 75.0,
        }
        mock_db_fn.return_value = self._db_mock()
        mock_ssh.side_effect = ConnectionError("ssh down")

        res = evaluate_recovery_safety(self.incident_id)

        self.assertFalse(res.safe)
        self.assertEqual(res.failed_rule, "R5")
        self.assertFalse(res.checks["sshReachable"])

    @patch("services.storm.recovery.safety.recovery_locks_available", return_value=True)
    @patch("services.storm.recovery.safety.SSHMitigationExecutor")
    @patch("services.storm.recovery.safety.get_settings")
    @patch("services.storm.recovery.safety.get_incident")
    @patch("services.storm.recovery.safety._db")
    def test_blocked_because_interface_already_up(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_settings,
        mock_ssh,
        _mock_locks,
    ):
        mock_get_incident.return_value = self.incident_doc
        mock_settings.return_value = {
            "cooldownMinutes": 5,
            "reMitigationThreshold": 75.0,
        }
        mock_db_fn.return_value = self._db_mock(iface_admin="up")
        collector = MagicMock()
        collector.run_command.return_value = f"{self.interface} is up, line protocol is up"
        mock_ssh.return_value.__enter__.return_value.collector = collector

        with patch(
            "services.storm.diagnostics.snapshots.parse_interface_snapshot",
            return_value={"adminStatus": "up", "available": True},
        ):
            res = evaluate_recovery_safety(self.incident_id)

        self.assertFalse(res.safe)
        self.assertEqual(res.failed_rule, "R6")
        self.assertFalse(res.checks["interfaceAdminDown"])

    @patch("services.storm.recovery.safety.recovery_locks_available", return_value=True)
    @patch("services.storm.recovery.safety.SSHMitigationExecutor")
    @patch("services.storm.recovery.safety.get_settings")
    @patch("services.storm.recovery.safety.get_incident")
    @patch("services.storm.recovery.safety._db")
    def test_blocked_because_newer_incident_exists(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_settings,
        mock_ssh,
        _mock_locks,
    ):
        mock_get_incident.return_value = self.incident_doc
        mock_settings.return_value = {
            "cooldownMinutes": 5,
            "reMitigationThreshold": 75.0,
        }
        mock_db_fn.return_value = self._db_mock(
            newer_incident={"incidentId": "storm-2026-000099", "status": "OPEN"}
        )

        res = evaluate_recovery_safety(self.incident_id)

        self.assertFalse(res.safe)
        self.assertEqual(res.failed_rule, "R7")
        self.assertFalse(res.checks["noNewerActiveIncident"])
        mock_ssh.assert_not_called()

    @patch("services.storm.recovery.safety.recovery_locks_available", return_value=False)
    @patch("services.storm.recovery.safety.SSHMitigationExecutor")
    @patch("services.storm.recovery.safety.get_settings")
    @patch("services.storm.recovery.safety.get_incident")
    @patch("services.storm.recovery.safety._db")
    def test_blocked_because_recovery_lock_conflict(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_settings,
        mock_ssh,
        _mock_locks,
    ):
        mock_get_incident.return_value = self.incident_doc
        mock_settings.return_value = {
            "cooldownMinutes": 5,
            "reMitigationThreshold": 75.0,
        }
        mock_db_fn.return_value = self._db_mock()

        res = evaluate_recovery_safety(self.incident_id)

        self.assertFalse(res.safe)
        self.assertEqual(res.failed_rule, "R8")
        self.assertFalse(res.checks["recoveryLockAvailable"])
        mock_ssh.assert_not_called()

    @patch("services.storm.recovery.safety.recovery_locks_available", return_value=True)
    @patch("services.storm.recovery.safety.SSHMitigationExecutor")
    @patch("services.storm.recovery.safety.get_settings")
    @patch("services.storm.recovery.safety.get_incident")
    @patch("services.storm.recovery.safety._db")
    def test_blocked_because_storm_still_confirmed(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_settings,
        mock_ssh,
        _mock_locks,
    ):
        mock_get_incident.return_value = self.incident_doc
        mock_settings.return_value = {
            "cooldownMinutes": 5,
            "reMitigationThreshold": 75.0,
        }
        mock_db_fn.return_value = self._db_mock(confirmation={"confirmed": True})

        res = evaluate_recovery_safety(self.incident_id)

        self.assertFalse(res.safe)
        self.assertEqual(res.failed_rule, "R1")
        self.assertFalse(res.checks["stormCleared"])
        mock_ssh.assert_not_called()

    @patch("services.storm.safety.evaluate")
    @patch("services.storm.recovery.safety.recovery_locks_available", return_value=True)
    @patch("services.storm.recovery.safety.SSHMitigationExecutor")
    @patch("services.storm.recovery.safety.get_settings")
    @patch("services.storm.recovery.safety.get_incident")
    @patch("services.storm.recovery.safety._db")
    def test_policy_never_calls_mitigation_safety(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_settings,
        mock_ssh,
        _mock_locks,
        mock_mitigation_safety,
    ):
        mock_get_incident.return_value = self.incident_doc
        mock_settings.return_value = {
            "cooldownMinutes": 5,
            "reMitigationThreshold": 75.0,
        }
        mock_db_fn.return_value = self._db_mock()
        collector = MagicMock()
        mock_ssh.return_value.__enter__.return_value.collector = collector

        with patch(
            "services.storm.diagnostics.snapshots.parse_interface_snapshot",
            return_value={"adminStatus": "down", "available": True},
        ):
            res = validate_recovery_policy(self.incident_id)

        self.assertTrue(res["passed"])
        self.assertNotIn("safetyPassed", res["checks"])
        self.assertIn("stormCleared", res["checks"])
        mock_mitigation_safety.assert_not_called()


if __name__ == "__main__":
    unittest.main()
