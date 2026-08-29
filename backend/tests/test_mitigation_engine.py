"""
Unit tests for the Enterprise Mitigation Engine.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from bson import ObjectId

from services.interface_collection.ssh_collector import SSHInterfaceCollector
from services.storm.mitigation.engine import execute_mitigation, rollback_mitigation
from services.storm.mitigation.ssh_executor import (
    assert_safe_mitigation_command,
    check_for_errors,
)
from services.storm.mitigation.strategy import (
    NoShutdownRecoveryStrategy,
    ShutdownInterfaceStrategy,
)


class MitigationEngineTests(unittest.TestCase):
    def setUp(self):
        self.device_id = ObjectId("507f1f77bcf86cd799439011")
        self.incident_id = "storm-2026-000001"
        self.interface = "Gi1/0/10"

        # Mock Incident Document
        self.incident_doc = {
            "incidentId": self.incident_id,
            "deviceId": self.device_id,
            "interface": self.interface,
            "status": "READY_FOR_MITIGATION",
            "incidentType": "STORM",
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "severity": "CRITICAL",
        }

        # Mock Device Document
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

    def _fake_db(self):
        """DB mock with live Confirmation + Safety for STANDARD admin shutdown."""
        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        fake_db.storm_confirmation_history.find_one.return_value = {
            "confirmed": True,
            "timestamp": datetime.now(timezone.utc),
        }
        fake_db.storm_safety_history.find_one.return_value = {
            "safe": True,
            "timestamp": datetime.now(timezone.utc),
        }
        return fake_db

    def test_safe_command_whitelisting(self):
        """Verify that whitelisted commands pass and dynamic injection fails."""
        # Whitelisted Cisco
        assert_safe_mitigation_command("configure terminal", self.interface)
        assert_safe_mitigation_command(f"interface {self.interface}", self.interface)
        assert_safe_mitigation_command("shutdown", self.interface)
        assert_safe_mitigation_command("no shutdown", self.interface)
        assert_safe_mitigation_command("end", self.interface)

        # Invalid commands or parameter injection attempts
        with self.assertRaises(ValueError):
            assert_safe_mitigation_command("shutdown; rm -rf /", self.interface)
        with self.assertRaises(ValueError):
            assert_safe_mitigation_command("interface Gi1/0/11", self.interface)
        with self.assertRaises(ValueError):
            assert_safe_mitigation_command("reload", self.interface)

    def test_cli_error_check(self):
        """Verify that CLI output errors raise exceptions."""
        check_for_errors("interface GigabitEthernet1/0/10\nshutdown\n")
        with self.assertRaises(ValueError):
            check_for_errors("% Invalid input detected at '^' marker.")
        with self.assertRaises(ValueError):
            check_for_errors("interface Gi1/0/10\n% Command rejected: Protected port.")

    @patch("services.storm.mitigation.engine.get_incident")
    @patch("services.storm.mitigation.engine._db")
    @patch("services.storm.mitigation.ssh_executor.SSHInterfaceCollector")
    def test_successful_shutdown_mitigation(self, mock_ssh, mock_db_fn, mock_get_incident):
        """Test successful shutdown mitigation execution and verification."""
        mock_get_incident.return_value = self.incident_doc

        fake_db = self._fake_db()
        mock_db_fn.return_value = fake_db
        with patch(
            "services.storm.mitigation.engine.LockService.acquire_mitigation_locks",
            return_value=("device:lock", "interface:lock"),
        ):
            # Mock SSH commands output
            mock_collector = MagicMock(spec=SSHInterfaceCollector)
            mock_ssh.return_value = mock_collector
            # Mock verification command output (show running-config interface)
            mock_collector.run_command.side_effect = lambda cmd, wait=0.4: (
                "interface GigabitEthernet1/0/10\n shutdown\n"
                if "show" in cmd
                else "OK"
            )

            res = execute_mitigation(self.incident_id, "SHUTDOWN", operator="admin")

        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "SUCCESS")

        # Verify locks were cleaned up
        # Verify incident status was updated to MITIGATED
        update_call = fake_db.storm_incidents.update_one.call_args
        self.assertEqual(update_call[0][0], {"incidentId": self.incident_id})
        self.assertEqual(update_call[0][1]["$set"]["status"], "MITIGATED")

    @patch("services.storm.mitigation.engine.get_incident")
    @patch("services.storm.mitigation.engine._db")
    @patch("services.storm.mitigation.ssh_executor.SSHInterfaceCollector")
    def test_verification_failure_triggers_rollback(self, mock_ssh, mock_db_fn, mock_get_incident):
        """Test that verification failure triggers automatic rollback."""
        mock_get_incident.return_value = self.incident_doc

        fake_db = self._fake_db()
        mock_db_fn.return_value = fake_db

        with patch(
            "services.storm.mitigation.engine.LockService.acquire_mitigation_locks",
            return_value=("device:lock", "interface:lock"),
        ):
            # Mock SSH commands output
            mock_collector = MagicMock(spec=SSHInterfaceCollector)
            mock_ssh.return_value = mock_collector
            # Verification output fails (returns running-config without 'shutdown')
            mock_collector.run_command.side_effect = lambda cmd, wait=0.4: (
                "interface GigabitEthernet1/0/10\n no shutdown\n"
                if "show" in cmd
                else "OK"
            )

            res = execute_mitigation(self.incident_id, "SHUTDOWN", operator="admin")

        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "ROLLBACK_SUCCESS")

        # Verify rollback commands (no shutdown) were run
        run_cmds = [call[0][0] for call in mock_collector.run_command.call_args_list]
        self.assertIn("no shutdown", run_cmds)

        # Verify incident status was updated to MITIGATION_FAILED
        fake_db.storm_incidents.update_one.assert_called_with(
            {"incidentId": self.incident_id},
            {"$set": {"status": "MITIGATION_FAILED", "updatedAt": unittest.mock.ANY}},
        )

    @patch("services.storm.mitigation.engine.get_incident")
    @patch("services.storm.mitigation.engine._db")
    def test_duplicate_lock_acquisition_conflict(self, mock_db_fn, mock_get_incident):
        """Test lock conflict raises ValueError."""
        mock_get_incident.return_value = self.incident_doc

        fake_db = self._fake_db()
        mock_db_fn.return_value = fake_db

        with patch(
            "services.storm.mitigation.engine.LockService.acquire_mitigation_locks",
            side_effect=ValueError("Mitigation lock conflict"),
        ), self.assertRaises(ValueError) as ctx:
            execute_mitigation(self.incident_id, "SHUTDOWN", operator="admin")

        self.assertIn("lock conflict", str(ctx.exception))

    @patch("services.storm.mitigation.engine.get_incident")
    @patch("services.storm.mitigation.engine._db")
    @patch("services.storm.mitigation.engine.execute_rollback")
    @patch("services.storm.mitigation.ssh_executor.SSHInterfaceCollector")
    def test_ssh_timeout_triggers_external_rollback(
        self, mock_ssh, mock_rollback, mock_db_fn, mock_get_incident
    ):
        """Test that SSH timeout/connection drops trigger rollback with new connection."""
        mock_get_incident.return_value = self.incident_doc

        fake_db = self._fake_db()
        mock_db_fn.return_value = fake_db

        # Collector connect raises error
        mock_collector = MagicMock(spec=SSHInterfaceCollector)
        mock_ssh.return_value = mock_collector
        mock_collector.connect.side_effect = RuntimeError("SSH Timeout")

        mock_rollback.return_value = (True, ["no shutdown"])

        with patch(
            "services.storm.mitigation.engine.LockService.acquire_mitigation_locks",
            return_value=("device:lock", "interface:lock"),
        ):
            res = execute_mitigation(self.incident_id, "SHUTDOWN", operator="admin")

        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "ROLLBACK_SUCCESS")
        mock_rollback.assert_called_once()

    @patch("services.storm.mitigation.engine.get_incident")
    @patch("services.storm.mitigation.engine._db")
    @patch("services.storm.mitigation.engine.execute_rollback")
    @patch("services.storm.mitigation.ssh_executor.SSHInterfaceCollector")
    def test_rollback_failure_recorded(
        self, mock_ssh, mock_rollback, mock_db_fn, mock_get_incident
    ):
        """Test that rollback failure updates status correctly."""
        mock_get_incident.return_value = self.incident_doc

        fake_db = self._fake_db()
        mock_db_fn.return_value = fake_db

        # Execute fails
        mock_collector = MagicMock(spec=SSHInterfaceCollector)
        mock_ssh.return_value = mock_collector
        mock_collector.run_command.side_effect = RuntimeError("Interrupted")

        # Rollback fails too
        mock_rollback.return_value = (False, ["no shutdown"])

        with patch(
            "services.storm.mitigation.engine.LockService.acquire_mitigation_locks",
            return_value=("device:lock", "interface:lock"),
        ):
            res = execute_mitigation(self.incident_id, "SHUTDOWN", operator="admin")

        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "FAILURE")

    @patch("services.storm.mitigation.engine.get_incident")
    @patch("services.storm.mitigation.engine._db")
    @patch("services.storm.mitigation.ssh_executor.SSHInterfaceCollector")
    def test_manual_rollback_reverts_configuration(self, mock_ssh, mock_db_fn, mock_get_incident):
        """Test manual rollback successfully reverts state."""
        # Mock incident in mitigated status
        mitigated_incident = dict(self.incident_doc)
        mitigated_incident["status"] = "MITIGATED"
        mock_get_incident.return_value = mitigated_incident

        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        mock_db_fn.return_value = fake_db

        mock_collector = MagicMock(spec=SSHInterfaceCollector)
        mock_ssh.return_value = mock_collector

        # Simulate history lookup returns applied strategy SHUTDOWN
        fake_db.__getitem__.return_value.find_one.return_value = {
            "strategy": "SHUTDOWN",
            "status": "SUCCESS",
        }

        with patch(
            "services.storm.mitigation.engine.LockService.acquire_mitigation_locks",
            return_value=("device:lock", "interface:lock"),
        ):
            res = rollback_mitigation(self.incident_id, operator="admin")

        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "ROLLBACK_SUCCESS")

        # Check rollback command (no shutdown) executed
        run_cmds = [call[0][0] for call in mock_collector.run_command.call_args_list]
        self.assertIn("no shutdown", run_cmds)

        # Incident status set back to OPEN
        fake_db.storm_incidents.update_one.assert_called_with(
            {"incidentId": self.incident_id},
            {"$set": {"status": "OPEN", "updatedAt": unittest.mock.ANY}},
        )
