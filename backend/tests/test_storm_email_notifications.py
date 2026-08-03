"""
Unit tests for Storm Protection email notifications.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from services.email_service import (
    SUBJECT_MITIGATION_FAILURE,
    SUBJECT_RECOVERY,
    SUBJECT_RECOVERY_FAILURE,
    SUBJECT_SHUTDOWN,
    send_storm_mitigation_failure,
    send_storm_recovery_failure,
    send_storm_recovery_notification,
    send_storm_shutdown_notification,
)


def _incident(**overrides):
    base = {
        "incidentId": "storm-2026-000100",
        "hostname": "sw1",
        "ipAddress": "10.0.0.1",
        "interface": "Gi1/0/5",
        "severity": "CRITICAL",
        "status": "MITIGATED",
        "reason": "Storm confirmed",
        "risk": {"riskScore": 92.5},
        "trigger": {"risk": 92.5, "confirmation": True, "safety": True},
        "createdAt": datetime(2026, 7, 31, 10, 0, 0, tzinfo=timezone.utc),
        "mitigatedAt": datetime(2026, 7, 31, 10, 5, 0, tzinfo=timezone.utc),
        "timeline": [
            {
                "event": "Shutdown Executed",
                "time": datetime(2026, 7, 31, 10, 5, 0, tzinfo=timezone.utc),
            }
        ],
    }
    base.update(overrides)
    return base


def _settings(*, storm=None, smtp=None):
    return {
        "smtp": smtp
        or {
            "enabled": True,
            "host": "smtp.example.com",
            "port": 587,
            "user": "alerts@example.com",
            "password": "secret",
            "fromAddress": "alerts@example.com",
            "toAddress": "ops@example.com",
            "useTls": True,
        },
        "stormNotifications": storm
        or {
            "enabled": True,
            "shutdownEmails": True,
            "recoveryEmails": True,
            "failureEmails": True,
            "toAddress": "storm@example.com",
        },
    }


class StormEmailNotificationTests(unittest.TestCase):
    @patch("services.audit_service.log_audit")
    @patch("services.email_service.send_email", return_value=True)
    @patch("services.email_service.get_settings")
    def test_shutdown_success_email(self, mock_settings, mock_send, mock_audit):
        mock_settings.return_value = _settings()
        incident = _incident()

        ok = send_storm_shutdown_notification(
            incident,
            verification_result={"success": True, "output": "admin is down"},
            operator="SYSTEM",
        )
        self.assertTrue(ok)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        self.assertEqual(args[0], SUBJECT_SHUTDOWN)
        self.assertIn("Gi1/0/5", args[1])
        self.assertIn("storm-2026-000100", args[1])
        self.assertIn("MITIGATED", args[1])
        self.assertIn("NetPulse", args[2])
        self.assertEqual(kwargs.get("to_address"), "storm@example.com")
        mock_audit.assert_called_once()
        details = mock_audit.call_args.kwargs["details"]
        self.assertEqual(details.get("deliveryStatus"), "SENT")
        self.assertEqual(details.get("subject"), SUBJECT_SHUTDOWN)
        self.assertTrue(details.get("emailSent"))

    @patch("services.audit_service.log_audit")
    @patch("services.email_service.send_email", return_value=True)
    @patch("services.email_service.get_settings")
    def test_recovery_success_email(self, mock_settings, mock_send, mock_audit):
        mock_settings.return_value = _settings()
        recovered_at = datetime(2026, 7, 31, 10, 20, 0, tzinfo=timezone.utc)
        incident = _incident(status="MONITORING", recoveredAt=recovered_at)

        ok = send_storm_recovery_notification(
            incident,
            verification_result={"success": True, "output": "admin is up"},
            recovered_at=recovered_at,
        )
        self.assertTrue(ok)
        args, kwargs = mock_send.call_args
        self.assertEqual(args[0], SUBJECT_RECOVERY)
        self.assertIn("RECOVERED", args[1])
        self.assertIn("Recovery Duration", args[1])
        self.assertIn("15m", args[1])  # 10:05 → 10:20
        mock_audit.assert_called_once()

    @patch("services.audit_service.log_audit")
    @patch("services.email_service.send_email", return_value=True)
    @patch("services.email_service.get_settings")
    def test_mitigation_failure_email(self, mock_settings, mock_send, mock_audit):
        mock_settings.return_value = _settings()
        ok = send_storm_mitigation_failure(
            _incident(status="MITIGATION_FAILED"),
            verification_result={"success": False, "error": "verification failed"},
            reason="Mitigation verification failed",
        )
        self.assertTrue(ok)
        self.assertEqual(mock_send.call_args[0][0], SUBJECT_MITIGATION_FAILURE)
        self.assertIn("WARNING", mock_send.call_args[0][2])
        mock_audit.assert_called_once()

    @patch("services.audit_service.log_audit")
    @patch("services.email_service.send_email", return_value=True)
    @patch("services.email_service.get_settings")
    def test_recovery_failure_email(self, mock_settings, mock_send, mock_audit):
        mock_settings.return_value = _settings()
        ok = send_storm_recovery_failure(
            _incident(status="RECOVERY_FAILED"),
            verification_result={"success": False, "error": "still down"},
            action_status="RECOVERY_FAILED",
        )
        self.assertTrue(ok)
        self.assertEqual(mock_send.call_args[0][0], SUBJECT_RECOVERY_FAILURE)
        self.assertIn("RECOVERY_FAILED", mock_send.call_args[0][1])
        mock_audit.assert_called_once()

    @patch("services.audit_service.log_audit")
    @patch("services.email_service.send_email", return_value=True)
    @patch("services.email_service.get_settings")
    def test_notifications_disabled(self, mock_settings, mock_send, mock_audit):
        mock_settings.return_value = _settings(
            storm={
                "enabled": False,
                "shutdownEmails": True,
                "recoveryEmails": True,
                "failureEmails": True,
                "toAddress": "storm@example.com",
            }
        )
        ok = send_storm_shutdown_notification(_incident())
        self.assertFalse(ok)
        mock_send.assert_not_called()
        mock_audit.assert_not_called()

    @patch("services.audit_service.log_audit")
    @patch("services.email_service.send_email", return_value=True)
    @patch("services.email_service.get_settings")
    def test_shutdown_emails_flag_disabled(self, mock_settings, mock_send, mock_audit):
        mock_settings.return_value = _settings(
            storm={
                "enabled": True,
                "shutdownEmails": False,
                "recoveryEmails": True,
                "failureEmails": True,
                "toAddress": "",
            }
        )
        self.assertFalse(send_storm_shutdown_notification(_incident()))
        mock_send.assert_not_called()

    @patch("services.audit_service.log_audit")
    @patch("services.email_service.send_email", return_value=False)
    @patch("services.email_service.get_settings")
    def test_smtp_failure_handled_gracefully(self, mock_settings, mock_send, mock_audit):
        mock_settings.return_value = _settings()
        ok = send_storm_shutdown_notification(_incident())
        self.assertFalse(ok)
        mock_send.assert_called_once()
        details = mock_audit.call_args.kwargs["details"]
        self.assertEqual(details["deliveryStatus"], "FAILED")
        self.assertFalse(details["emailSent"])

    @patch("services.audit_service.log_audit", side_effect=RuntimeError("audit down"))
    @patch("services.email_service.send_email", side_effect=RuntimeError("smtp boom"))
    @patch("services.email_service.get_settings")
    def test_send_exception_does_not_raise(self, mock_settings, mock_send, mock_audit):
        mock_settings.return_value = _settings()
        # Outer dispatch catches all exceptions
        ok = send_storm_recovery_notification(_incident())
        self.assertFalse(ok)


class StormEmailEngineHookTests(unittest.TestCase):
    """Ensure engines notify only for SYSTEM verified outcomes."""

    @patch("services.storm.mitigation.engine.LockService.release_mitigation_locks")
    @patch("services.storm.mitigation.engine.LockService.acquire_mitigation_locks")
    @patch("services.storm.mitigation.engine.record_mitigation_history")
    @patch("services.storm.mitigation.engine.append_timeline_event")
    @patch("services.storm.mitigation.engine.verify_mitigation", return_value=(True, "down"))
    @patch("services.storm.mitigation.engine.SSHMitigationExecutor")
    @patch("services.storm.mitigation.engine.get_incident")
    @patch("services.storm.mitigation.engine._db")
    @patch("services.alert_service.create_storm_shutdown_alert", return_value="alert-test")
    @patch("services.alert_service.mark_alert_email_sent")
    @patch("services.email_service.send_storm_shutdown_notification")
    def test_engine_sends_shutdown_email_for_system(
        self,
        mock_mail,
        _mark_email,
        _alert,
        mock_db_fn,
        mock_get,
        mock_ssh,
        _verify,
        _timeline,
        _history,
        mock_locks,
        _release,
    ):
        from services.storm.mitigation.engine import execute_mitigation

        device_id = "507f1f77bcf86cd799439011"
        incident = _incident(
            deviceId=device_id,
            status="READY_FOR_MITIGATION",
            incidentType="STORM",
        )
        mock_get.return_value = incident
        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = {
            "_id": device_id,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "credentials": {"sshUsername": "a", "sshPassword": "b", "sshVendor": "cisco_ios"},
        }
        # Confirmation / safety gates for SYSTEM shutdown
        fake_db.storm_confirmation_history.find_one.return_value = {
            "confirmed": True,
            "state": "CONFIRMED",
        }
        fake_db.storm_safety_history.find_one.return_value = {"safe": True}
        mock_db_fn.return_value = fake_db
        mock_locks.return_value = ("dlock", "ilock")

        executor = MagicMock()
        executor.creds.vendor = "cisco_ios"
        mock_ssh.return_value.__enter__.return_value = executor

        res = execute_mitigation("storm-2026-000100", "SHUTDOWN", operator="SYSTEM")
        self.assertTrue(res["success"])
        mock_mail.assert_called_once()
        _alert.assert_called_once()

    @patch("services.storm.mitigation.engine.LockService.release_mitigation_locks")
    @patch("services.storm.mitigation.engine.LockService.acquire_mitigation_locks")
    @patch("services.storm.mitigation.engine.record_mitigation_history")
    @patch("services.storm.mitigation.engine.append_timeline_event")
    @patch("services.storm.mitigation.engine.verify_mitigation", return_value=(True, "down"))
    @patch("services.storm.mitigation.engine.SSHMitigationExecutor")
    @patch("services.storm.mitigation.engine.get_incident")
    @patch("services.storm.mitigation.engine._db")
    @patch("services.email_service.send_storm_shutdown_notification")
    def test_engine_skips_email_for_manual_operator(
        self,
        mock_mail,
        mock_db_fn,
        mock_get,
        mock_ssh,
        _verify,
        _timeline,
        _history,
        mock_locks,
        _release,
    ):
        from services.storm.mitigation.engine import execute_mitigation

        device_id = "507f1f77bcf86cd799439011"
        mock_get.return_value = _incident(
            deviceId=device_id,
            status="READY_FOR_MITIGATION",
        )
        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = {
            "_id": device_id,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "credentials": {},
        }
        mock_db_fn.return_value = fake_db
        mock_locks.return_value = ("dlock", "ilock")
        executor = MagicMock()
        executor.creds.vendor = "cisco_ios"
        mock_ssh.return_value.__enter__.return_value = executor

        res = execute_mitigation("storm-2026-000100", "SHUTDOWN", operator="admin")
        self.assertTrue(res["success"])
        mock_mail.assert_not_called()


if __name__ == "__main__":
    unittest.main()
