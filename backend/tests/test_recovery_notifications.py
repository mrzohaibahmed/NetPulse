"""
Tests for centralized storm port recovery notifications.
"""

from __future__ import annotations

import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from bson import ObjectId
from pymongo import ReturnDocument

from services.storm.recovery.engine import execute_manual_recovery, execute_recovery
from services.storm.recovery.notifications import (
    RECOVERY_SOURCE_AUTOMATIC,
    RECOVERY_SOURCE_MANUAL,
    RECOVERY_SOURCE_OPERATOR,
    RECOVERY_SOURCE_RECONCILIATION,
    notify_port_recovery,
)
from services.storm.recovery.reconciliation import reconcile_mitigated_incident


def _r6_val_res() -> dict:
    return {
        "passed": False,
        "safe": False,
        "failedRule": "R6",
        "reason": "Interface already up (admin=up) — nothing to recover",
        "checks": {
            "sshReachable": True,
            "interfaceAdminDown": False,
            "cooldownExpired": True,
            "stormCleared": True,
            "riskBelowThreshold": True,
        },
    }


class _IncidentStore:
    """Minimal in-memory storm_incidents store with atomic claim semantics."""

    def __init__(self, doc: dict):
        self.doc = dict(doc)
        self.lock = threading.Lock()

    def find_one_and_update(self, flt, update, return_document=None):
        with self.lock:
            if self.doc.get("incidentId") != flt.get("incidentId"):
                return None
            sent_at = self.doc.get("recoveryNotificationSentAt")
            if sent_at is not None:
                return None
            before = dict(self.doc)
            self.doc.update(update["$set"])
            return before

    def find_one(self, flt, projection=None):
        if self.doc.get("incidentId") == flt.get("incidentId"):
            return dict(self.doc)
        return None


def _audit_calls(mock_audit, action: str) -> list:
    matched = []
    for call in mock_audit.call_args_list:
        act = call.kwargs.get("action")
        if act is None and call.args:
            act = call.args[0]
        if act == action:
            matched.append(call)
    return matched


class RecoveryNotificationTests(unittest.TestCase):
    def setUp(self):
        self.device_id = ObjectId("507f1f77bcf86cd799439011")
        self.incident_id = "storm-2026-notify-001"
        self.interface = "Gi1/0/10"
        self.operator = "admin"
        self.incident_doc = {
            "incidentId": self.incident_id,
            "deviceId": self.device_id,
            "interface": self.interface,
            "status": "MITIGATED",
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "severity": "CRITICAL",
            "recoveryRetryCount": 0,
            "mitigatedAt": datetime(2026, 7, 31, 10, 5, 0, tzinfo=timezone.utc),
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
        self.settings = {
            "maximumRecoveryAttempts": 3,
            "stabilizationSeconds": 60,
            "stormNotifications": {
                "enabled": True,
                "recoveryEmails": True,
                "shutdownEmails": True,
                "failureEmails": True,
                "toAddress": "ops@example.com",
            },
            "smtp": {
                "enabled": True,
                "host": "smtp.example.com",
                "port": 587,
                "user": "alerts@example.com",
                "password": "secret",
                "fromAddress": "alerts@example.com",
                "toAddress": "ops@example.com",
                "useTls": True,
            },
        }

    def _wire_notification_db(self, store: _IncidentStore):
        fake_db = MagicMock()
        fake_db.storm_incidents = store
        fake_db.devices.find_one.return_value = self.device_doc
        fake_db.alerts.insert_one.return_value = MagicMock(
            inserted_id=ObjectId("507f1f77bcf86cd7994390aa")
        )
        return fake_db

    def _notification_patches(self, store: _IncidentStore):
        fake_db = self._wire_notification_db(store)
        return fake_db

    @patch("services.alert_service.log_audit")
    @patch("services.alert_service.db")
    @patch("services.audit_service.log_audit")
    @patch("services.email_service.send_email", return_value=True)
    @patch("services.email_service.get_settings")
    @patch("services.storm.recovery.notifications.get_incident")
    @patch("services.storm.recovery.notifications._db")
    def test_automatic_recovery_notifies(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_email_settings,
        mock_send,
        mock_email_audit,
        mock_alert_db,
        _mock_alert_audit,
    ):
        store = _IncidentStore(self.incident_doc)
        fake_db = self._notification_patches(store)
        mock_db_fn.return_value = fake_db
        mock_alert_db.alerts = fake_db.alerts
        mock_get_incident.return_value = store.doc
        mock_email_settings.return_value = self.settings

        result = notify_port_recovery(
            self.incident_id,
            source=RECOVERY_SOURCE_AUTOMATIC,
            operator="SYSTEM",
            device=self.device_doc,
            verification_result={"success": True, "output": "admin is up"},
            recovered_at=datetime(2026, 7, 31, 10, 20, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(result["notified"])
        self.assertTrue(result["email_sent"])
        mock_send.assert_called_once()
        mock_alert_db.alerts.insert_one.assert_called_once()
        email_audits = _audit_calls(mock_email_audit, "storm_email_notification")
        self.assertEqual(len(email_audits), 1)
        details = email_audits[0].kwargs.get("details") or email_audits[0].args[3]
        self.assertEqual(details["eventType"], "Automatic Port Recovery")
        self.assertIsNotNone(store.doc.get("recoveryNotificationSentAt"))

    @patch("services.storm.recovery.reconciliation.notify_port_recovery")
    @patch("services.storm.recovery.reconciliation.record_recovery_history")
    @patch("services.storm.recovery.reconciliation.invalidate_pipeline_after_recovery")
    @patch("services.storm.recovery.reconciliation.append_timeline_event")
    @patch("services.storm.recovery.reconciliation.get_settings")
    @patch("services.storm.recovery.reconciliation.get_incident")
    @patch("services.storm.recovery.reconciliation._db")
    @patch(
        "services.storm.recovery.reconciliation.LockService.is_mitigation_active",
        return_value=False,
    )
    @patch(
        "services.storm.recovery.reconciliation.is_recovery_active",
        return_value=False,
    )
    def test_r6_reconciliation_notifies_without_ssh_recovery(
        self,
        _mock_recovery_active,
        _mock_mitigation_active,
        mock_db,
        mock_get_incident,
        mock_settings,
        _timeline,
        _invalidate,
        _history,
        mock_notify,
    ):
        mock_get_incident.return_value = self.incident_doc
        mock_settings.return_value = {"stabilizationSeconds": 60}
        mock_db.return_value.storm_recovery_history.find_one.return_value = None

        with patch(
            "services.storm.recovery.engine.execute_recovery",
        ) as mock_execute:
            result = reconcile_mitigated_incident(
                self.incident_id,
                _r6_val_res(),
                operator="SYSTEM",
            )
            mock_execute.assert_not_called()

        self.assertTrue(result["success"])
        mock_notify.assert_called_once()
        kwargs = mock_notify.call_args.kwargs
        self.assertEqual(kwargs["source"], RECOVERY_SOURCE_RECONCILIATION)
        self.assertEqual(kwargs["operator"], "SYSTEM")

    @patch("services.alert_service.db")
    @patch("services.audit_service.log_audit")
    @patch("services.email_service.send_email", return_value=True)
    @patch("services.email_service.get_settings")
    @patch("services.storm.recovery.notifications.get_incident")
    @patch("services.storm.recovery.notifications._db")
    def test_operator_recovery_event_type(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_email_settings,
        _mock_send,
        mock_email_audit,
        mock_alert_db,
    ):
        store = _IncidentStore(self.incident_doc)
        fake_db = self._notification_patches(store)
        mock_db_fn.return_value = fake_db
        mock_alert_db.alerts = fake_db.alerts
        mock_get_incident.return_value = store.doc
        mock_email_settings.return_value = self.settings

        notify_port_recovery(
            self.incident_id,
            source=RECOVERY_SOURCE_OPERATOR,
            operator=self.operator,
            device=self.device_doc,
            verification_result={"success": True},
        )

        alert_doc = mock_alert_db.alerts.insert_one.call_args[0][0]
        self.assertEqual(alert_doc["title"], "Operator Port Recovery")
        email_audits = _audit_calls(mock_email_audit, "storm_email_notification")
        email_details = email_audits[0].kwargs.get("details") or email_audits[0].args[3]
        self.assertEqual(email_details["eventType"], "Operator Port Recovery")

    @patch("services.storm.recovery.engine.notify_port_recovery")
    @patch("services.storm.recovery.engine.collect_post_recovery_stats", return_value={})
    @patch("services.storm.recovery.engine.LockService.release_recovery_locks")
    @patch("services.storm.recovery.engine.LockService.acquire_recovery_locks")
    @patch("services.storm.recovery.engine.LockService.is_mitigation_active", return_value=False)
    @patch("services.storm.recovery.reconciliation.is_recovery_active", return_value=False)
    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine._db")
    @patch("services.storm.recovery.engine.SSHMitigationExecutor")
    @patch("services.storm.recovery.engine.record_recovery_history")
    @patch("services.storm.recovery.engine.append_timeline_event")
    @patch("services.storm.recovery.post_recovery.invalidate_pipeline_after_recovery")
    def test_manual_recovery_notifies(
        self,
        _invalidate,
        _timeline,
        _history,
        mock_ssh,
        mock_db_fn,
        mock_get_incident,
        _mock_recovery_active,
        _mock_mitigation_active,
        mock_acquire,
        mock_release,
        _stats,
        mock_notify,
    ):
        mock_get_incident.return_value = self.incident_doc
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
            return_value=(True, "admin status is up"),
        ), patch(
            "services.storm.recovery.engine.get_settings",
            return_value=self.settings,
        ):
            res = execute_manual_recovery(self.incident_id, operator=self.operator)

        self.assertTrue(res["success"])
        mock_notify.assert_called_once()
        kwargs = mock_notify.call_args.kwargs
        self.assertEqual(kwargs["source"], RECOVERY_SOURCE_MANUAL)
        self.assertEqual(kwargs["operator"], self.operator)

    @patch("services.alert_service.db")
    @patch("services.audit_service.log_audit")
    @patch("services.email_service.send_email", return_value=True)
    @patch("services.email_service.get_settings")
    @patch("services.storm.recovery.notifications.get_incident")
    @patch("services.storm.recovery.notifications._db")
    def test_deduplication_single_notification(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_email_settings,
        mock_send,
        _mock_email_audit,
        mock_alert_db,
    ):
        store = _IncidentStore(self.incident_doc)
        fake_db = self._notification_patches(store)
        mock_db_fn.return_value = fake_db
        mock_alert_db.alerts = fake_db.alerts
        mock_get_incident.return_value = store.doc
        mock_email_settings.return_value = self.settings

        first = notify_port_recovery(
            self.incident_id,
            source=RECOVERY_SOURCE_AUTOMATIC,
            operator="SYSTEM",
            device=self.device_doc,
        )
        second = notify_port_recovery(
            self.incident_id,
            source=RECOVERY_SOURCE_OPERATOR,
            operator="admin",
            device=self.device_doc,
        )

        self.assertTrue(first["notified"])
        self.assertTrue(second["skipped"])
        mock_send.assert_called_once()
        mock_alert_db.alerts.insert_one.assert_called_once()

    @patch("services.alert_service.db")
    @patch("services.audit_service.log_audit")
    @patch("services.email_service.send_email", return_value=True)
    @patch("services.email_service.get_settings")
    @patch("services.storm.recovery.notifications.get_incident")
    @patch("services.storm.recovery.notifications._db")
    def test_concurrent_claim_only_one_notification(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_email_settings,
        mock_send,
        _mock_email_audit,
        mock_alert_db,
    ):
        store = _IncidentStore(self.incident_doc)
        fake_db = self._notification_patches(store)
        mock_db_fn.return_value = fake_db
        mock_alert_db.alerts = fake_db.alerts
        mock_get_incident.return_value = store.doc
        mock_email_settings.return_value = self.settings

        results: list[dict] = []

        def _attempt():
            results.append(
                notify_port_recovery(
                    self.incident_id,
                    source=RECOVERY_SOURCE_AUTOMATIC,
                    operator="SYSTEM",
                    device=self.device_doc,
                )
            )

        threads = [threading.Thread(target=_attempt) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        notified = [r for r in results if r.get("notified")]
        skipped = [r for r in results if r.get("skipped")]
        self.assertEqual(len(notified), 1)
        self.assertEqual(len(skipped), 3)
        mock_send.assert_called_once()

    @patch("services.alert_service.db")
    @patch("services.audit_service.log_audit")
    @patch("services.email_service.send_email", return_value=True)
    @patch("services.email_service.get_settings")
    @patch("services.storm.recovery.notifications.get_incident")
    @patch("services.storm.recovery.notifications._db")
    def test_recovery_emails_disabled(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_email_settings,
        mock_send,
        _mock_email_audit,
        mock_alert_db,
    ):
        store = _IncidentStore(self.incident_doc)
        fake_db = self._notification_patches(store)
        mock_db_fn.return_value = fake_db
        mock_alert_db.alerts = fake_db.alerts
        mock_get_incident.return_value = store.doc
        disabled = dict(self.settings)
        disabled["stormNotifications"] = dict(
            self.settings["stormNotifications"],
            recoveryEmails=False,
        )
        mock_email_settings.return_value = disabled

        result = notify_port_recovery(
            self.incident_id,
            source=RECOVERY_SOURCE_AUTOMATIC,
            operator="SYSTEM",
            device=self.device_doc,
        )

        self.assertTrue(result["notified"])
        self.assertFalse(result["email_sent"])
        mock_send.assert_not_called()
        mock_alert_db.alerts.insert_one.assert_called_once()

    @patch("services.email_service.send_storm_recovery_notification", return_value=False)
    @patch("services.alert_service.db")
    @patch("services.storm.recovery.engine.collect_post_recovery_stats", return_value={})
    @patch("services.storm.recovery.engine.record_recovery_history")
    @patch("services.storm.recovery.engine.append_timeline_event")
    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine._db")
    @patch("services.storm.recovery.engine.get_settings")
    @patch("services.storm.recovery.engine.SSHMitigationExecutor")
    def test_smtp_failure_does_not_break_recovery(
        self,
        mock_ssh,
        mock_settings,
        mock_db_fn,
        mock_get_incident_engine,
        _timeline,
        _history,
        _stats,
        mock_alert_db,
        _mock_recovery_email,
    ):
        mock_settings.return_value = self.settings
        mitigated = dict(self.incident_doc, status="MITIGATED", recoveryRetryCount=0)
        mock_get_incident_engine.return_value = mitigated
        store = _IncidentStore(mitigated)
        notification_db = self._notification_patches(store)
        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        fake_db.storm_incidents = MagicMock()
        mock_db_fn.return_value = fake_db
        mock_alert_db.alerts = notification_db.alerts

        executor = MagicMock()
        mock_ssh.return_value.__enter__.return_value = executor
        executor.creds.vendor = "cisco_ios"

        with patch(
            "services.storm.recovery.engine.LockService.acquire_recovery_locks",
            return_value=("device:lock", "interface:lock"),
        ), patch(
            "services.storm.recovery.engine.verify_interface_up",
            return_value=(True, "admin is up"),
        ), patch(
            "services.storm.recovery.post_recovery.invalidate_pipeline_after_recovery",
        ), patch(
            "services.storm.recovery.notifications.get_incident",
            return_value=mitigated,
        ), patch(
            "services.storm.recovery.notifications._db",
            return_value=notification_db,
        ):
            res = execute_recovery(
                self.incident_id,
                operator="SYSTEM",
                skip_policy_validation=True,
            )

        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "MONITORING")

    @patch("services.alert_service.db")
    @patch("services.audit_service.log_audit")
    @patch("services.email_service.send_email", return_value=True)
    @patch("services.email_service.get_settings")
    @patch("services.storm.recovery.notifications.get_incident")
    @patch("services.storm.recovery.notifications._db")
    def test_storm_notifications_disabled(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_email_settings,
        mock_send,
        mock_email_audit,
        mock_alert_db,
    ):
        store = _IncidentStore(self.incident_doc)
        fake_db = self._notification_patches(store)
        mock_db_fn.return_value = fake_db
        mock_alert_db.alerts = fake_db.alerts
        mock_get_incident.return_value = store.doc
        disabled = dict(self.settings)
        disabled["stormNotifications"] = dict(
            self.settings["stormNotifications"],
            enabled=False,
        )
        mock_email_settings.return_value = disabled

        result = notify_port_recovery(
            self.incident_id,
            source=RECOVERY_SOURCE_AUTOMATIC,
            operator="SYSTEM",
            device=self.device_doc,
        )

        self.assertTrue(result["notified"])
        self.assertFalse(result["email_sent"])
        mock_send.assert_not_called()
        email_audits = _audit_calls(mock_email_audit, "storm_email_notification")
        self.assertEqual(len(email_audits), 0)

    @patch("services.storm.recovery.engine.collect_post_recovery_stats", return_value={})
    @patch("services.storm.recovery.engine.record_recovery_history")
    @patch("services.storm.recovery.engine.append_timeline_event")
    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine._db")
    @patch("services.storm.recovery.engine.get_settings")
    @patch("services.storm.recovery.engine.SSHMitigationExecutor")
    def test_execute_recovery_operator_calls_notify(
        self,
        mock_ssh,
        mock_settings,
        mock_db_fn,
        mock_get_incident,
        _timeline,
        _history,
        _stats,
    ):
        mock_settings.return_value = self.settings
        mitigated = dict(self.incident_doc, status="MITIGATED", recoveryRetryCount=0)
        mock_get_incident.return_value = mitigated
        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        mock_db_fn.return_value = fake_db

        executor = MagicMock()
        mock_ssh.return_value.__enter__.return_value = executor
        executor.creds.vendor = "cisco_ios"

        with patch(
            "services.storm.recovery.engine.LockService.acquire_recovery_locks",
            return_value=("device:lock", "interface:lock"),
        ), patch(
            "services.storm.recovery.engine.verify_interface_up",
            return_value=(True, "admin is up"),
        ), patch(
            "services.storm.recovery.post_recovery.invalidate_pipeline_after_recovery",
        ), patch(
            "services.storm.recovery.engine.notify_port_recovery",
            return_value={"notified": True, "email_sent": True},
        ) as mock_notify:
            res = execute_recovery(
                self.incident_id,
                operator=self.operator,
                skip_policy_validation=True,
            )

        self.assertTrue(res["success"])
        mock_notify.assert_called_once()
        self.assertEqual(
            mock_notify.call_args.kwargs["source"],
            RECOVERY_SOURCE_OPERATOR,
        )
        self.assertEqual(mock_notify.call_args.kwargs["operator"], self.operator)


if __name__ == "__main__":
    unittest.main()
