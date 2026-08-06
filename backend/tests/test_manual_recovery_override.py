"""
Tests for Manual Recovery operator override.

Manual recovery must bypass Recovery Safety (R1–R8) while still enforcing
execution checks: recovery lock, mitigation lock, and SSH connectivity.

Automatic recovery (execute_recovery / scheduler) must remain unchanged.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import ANY, MagicMock, patch

from bson import ObjectId

from services.storm.recovery.engine import execute_manual_recovery, execute_recovery


class ManualRecoveryOverrideTests(unittest.TestCase):
    def setUp(self):
        self.device_id = ObjectId("507f1f77bcf86cd799439011")
        self.incident_id = "storm-2026-manual-001"
        self.interface = "Gi1/0/10"
        self.operator = "op1"

        self.incident_doc = {
            "incidentId": self.incident_id,
            "deviceId": self.device_id,
            "interface": self.interface,
            "status": "MITIGATED",
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "severity": "CRITICAL",
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

    @patch("services.storm.recovery.engine.collect_post_recovery_stats")
    @patch("services.storm.recovery.engine.LockService.release_recovery_locks")
    @patch("services.storm.recovery.engine.LockService.acquire_recovery_locks")
    @patch("services.storm.recovery.engine.LockService.is_mitigation_active", return_value=False)
    @patch("services.storm.recovery.reconciliation.is_recovery_active", return_value=False)
    @patch("services.storm.recovery.engine.validate_recovery_policy")
    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine._db")
    @patch("services.storm.recovery.engine.SSHMitigationExecutor")
    @patch("services.storm.recovery.engine.record_recovery_history")
    @patch("services.storm.recovery.engine.append_timeline_event")
    @patch("services.storm.recovery.post_recovery.invalidate_pipeline_after_recovery")
    def test_manual_recovery_bypasses_safety_and_succeeds(
        self,
        mock_invalidate,
        mock_timeline,
        mock_history,
        mock_ssh,
        mock_db_fn,
        mock_get_incident,
        mock_validate,
        _mock_recovery_active,
        _mock_mitigation_active,
        mock_acquire,
        mock_release,
        mock_stats,
    ):
        """Test 1: Manual recover skips R1–R8 and executes no shutdown immediately."""
        mock_get_incident.return_value = self.incident_doc
        mock_invalidate.return_value = {"ok": True}
        mock_stats.return_value = {"adminStatus": "up"}
        mock_acquire.return_value = ("recovery:device", "recovery:interface")

        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        mock_db_fn.return_value = fake_db

        executor = MagicMock()
        mock_ssh.return_value.__enter__.return_value = executor
        executor.creds.vendor = "cisco_ios"
        executor.collector.run_command.return_value = (
            "GigabitEthernet1/0/10 is up\n admin status is up"
        )

        with patch(
            "services.storm.recovery.engine.verify_interface_up",
            return_value=(True, "admin status is up"),
        ):
            res = execute_manual_recovery(self.incident_id, operator=self.operator)

        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "RECOVERED")
        mock_validate.assert_not_called()
        mock_acquire.assert_called_once()
        mock_release.assert_called_once()
        executor.execute_commands.assert_called_once()

        history_kwargs = mock_history.call_args.kwargs
        self.assertEqual(history_kwargs["recovery_status"], "RECOVERED")
        self.assertEqual(history_kwargs["recovery_type"], "MANUAL")
        self.assertEqual(history_kwargs["trigger"], "OPERATOR")
        self.assertEqual(history_kwargs["safety_rules"], "BYPASSED")
        self.assertEqual(history_kwargs["execution_checks"], "PASSED")
        self.assertEqual(history_kwargs["executed_by"], self.operator)
        self.assertEqual(history_kwargs["recovery_method"], "Manual Override")

        timeline_events = [c.args[1] for c in mock_timeline.call_args_list]
        self.assertIn("Manual Recovery Executed", timeline_events)

        fake_db.storm_incidents.update_one.assert_called_with(
            {"incidentId": self.incident_id},
            {
                "$set": {
                    "status": "MONITORING",
                    "stabilizationEnd": ANY,
                    "recoveredAt": ANY,
                    "updatedAt": ANY,
                    "postRecoveryReMitigationPending": True,
                    "postRecoveryReMitigationAttempted": False,
                }
            },
        )

    @patch("services.storm.recovery.engine.LockService.release_recovery_locks")
    @patch("services.storm.recovery.engine.LockService.acquire_recovery_locks")
    @patch("services.storm.recovery.engine.LockService.is_mitigation_active", return_value=False)
    @patch("services.storm.recovery.reconciliation.is_recovery_active", return_value=False)
    @patch("services.storm.recovery.engine.validate_recovery_policy")
    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine._db")
    @patch("services.storm.recovery.engine.SSHMitigationExecutor")
    @patch("services.storm.recovery.engine.record_recovery_history")
    @patch("services.storm.recovery.engine.append_timeline_event")
    def test_manual_recovery_ssh_unavailable(
        self,
        mock_timeline,
        mock_history,
        mock_ssh,
        mock_db_fn,
        mock_get_incident,
        mock_validate,
        _mock_recovery_active,
        _mock_mitigation_active,
        mock_acquire,
        mock_release,
    ):
        """Test 2: SSH unavailable fails before command execution."""
        mock_get_incident.return_value = self.incident_doc
        mock_acquire.return_value = ("recovery:device", "recovery:interface")
        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        mock_db_fn.return_value = fake_db

        mock_ssh.return_value.__enter__.side_effect = RuntimeError(
            "SSH reachability check failed: connection refused"
        )

        res = execute_manual_recovery(self.incident_id, operator=self.operator)

        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "FAILED")
        self.assertEqual(res["error"], "Unable to establish SSH connection.")
        mock_validate.assert_not_called()
        mock_release.assert_called_once()
        self.assertEqual(mock_history.call_args.kwargs["recovery_status"], "FAILED")
        self.assertEqual(mock_history.call_args.kwargs["safety_rules"], "BYPASSED")

    @patch("services.storm.recovery.engine.LockService.acquire_recovery_locks")
    @patch("services.storm.recovery.engine.LockService.is_mitigation_active", return_value=False)
    @patch("services.storm.recovery.reconciliation.is_recovery_active", return_value=True)
    @patch("services.storm.recovery.engine.validate_recovery_policy")
    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine.record_recovery_history")
    @patch("services.storm.recovery.engine.append_timeline_event")
    def test_manual_recovery_blocked_by_recovery_lock(
        self,
        mock_timeline,
        mock_history,
        mock_get_incident,
        mock_validate,
        _mock_recovery_active,
        _mock_mitigation_active,
        mock_acquire,
    ):
        """Test 3: Active recovery lock blocks manual recovery."""
        mock_get_incident.return_value = self.incident_doc

        res = execute_manual_recovery(self.incident_id, operator=self.operator)

        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "BLOCKED")
        self.assertEqual(res["error"], "Recovery already in progress.")
        mock_acquire.assert_not_called()
        mock_validate.assert_not_called()
        self.assertEqual(mock_history.call_args.kwargs["recovery_status"], "BLOCKED")

    @patch("services.storm.recovery.engine.LockService.acquire_recovery_locks")
    @patch("services.storm.recovery.engine.LockService.is_mitigation_active", return_value=True)
    @patch("services.storm.recovery.reconciliation.is_recovery_active", return_value=False)
    @patch("services.storm.recovery.engine.validate_recovery_policy")
    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine.record_recovery_history")
    @patch("services.storm.recovery.engine.append_timeline_event")
    def test_manual_recovery_blocked_by_mitigation_lock(
        self,
        mock_timeline,
        mock_history,
        mock_get_incident,
        mock_validate,
        _mock_recovery_active,
        _mock_mitigation_active,
        mock_acquire,
    ):
        """Test 4: Active mitigation lock blocks manual recovery."""
        mock_get_incident.return_value = self.incident_doc

        res = execute_manual_recovery(self.incident_id, operator=self.operator)

        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "BLOCKED")
        self.assertEqual(res["error"], "Mitigation currently executing.")
        mock_acquire.assert_not_called()
        mock_validate.assert_not_called()

    @patch("services.storm.recovery.engine.LockService.release_recovery_locks")
    @patch("services.storm.recovery.engine.LockService.acquire_recovery_locks")
    @patch("services.storm.recovery.engine.LockService.is_mitigation_active", return_value=False)
    @patch("services.storm.recovery.reconciliation.is_recovery_active", return_value=False)
    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine._db")
    @patch("services.storm.recovery.engine.SSHMitigationExecutor")
    @patch("services.storm.recovery.engine.record_recovery_history")
    @patch("services.storm.recovery.engine.append_timeline_event")
    def test_manual_recovery_command_rejected(
        self,
        mock_timeline,
        mock_history,
        mock_ssh,
        mock_db_fn,
        mock_get_incident,
        _mock_recovery_active,
        _mock_mitigation_active,
        mock_acquire,
        mock_release,
    ):
        """SSH connected but switch rejects recovery command → FAILED."""
        mock_get_incident.return_value = self.incident_doc
        mock_acquire.return_value = ("recovery:device", "recovery:interface")
        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        mock_db_fn.return_value = fake_db

        executor = MagicMock()
        mock_ssh.return_value.__enter__.return_value = executor
        executor.creds.vendor = "cisco_ios"
        executor.execute_commands.side_effect = RuntimeError(
            "Command execution failed on 'no shutdown': CLI rejected"
        )

        res = execute_manual_recovery(self.incident_id, operator=self.operator)

        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "FAILED")
        self.assertEqual(res["error"], "Switch rejected recovery command.")
        mock_release.assert_called_once()

    @patch("services.storm.recovery.engine.collect_post_recovery_stats")
    @patch("services.storm.recovery.engine.LockService.release_recovery_locks")
    @patch("services.storm.recovery.engine.LockService.acquire_recovery_locks")
    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine._db")
    @patch("services.storm.recovery.engine.validate_recovery_policy")
    @patch("services.storm.recovery.engine.SSHMitigationExecutor")
    @patch("services.storm.recovery.post_recovery.invalidate_pipeline_after_recovery")
    def test_automatic_recovery_still_runs_safety_engine(
        self,
        mock_invalidate,
        mock_ssh,
        mock_val,
        mock_db_fn,
        mock_get_incident,
        mock_acquire,
        mock_release,
        mock_stats,
    ):
        """Test 5: Automatic path still invokes Recovery Safety (R1–R8)."""
        mock_get_incident.return_value = self.incident_doc
        mock_val.return_value = {"passed": True}
        mock_invalidate.return_value = {"ok": True}
        mock_stats.return_value = {"adminStatus": "up"}
        mock_acquire.return_value = ("recovery:device", "recovery:interface")

        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        mock_db_fn.return_value = fake_db

        executor = MagicMock()
        mock_ssh.return_value.__enter__.return_value = executor
        executor.creds.vendor = "cisco_ios"
        executor.collector.run_command.return_value = "admin status is up"

        with patch(
            "services.storm.recovery.engine.verify_interface_up",
            return_value=(True, "up"),
        ):
            res = execute_recovery(
                self.incident_id, force=False, operator="SYSTEM"
            )

        self.assertTrue(res["success"])
        mock_val.assert_called_once_with(self.incident_id)


if __name__ == "__main__":
    unittest.main()
