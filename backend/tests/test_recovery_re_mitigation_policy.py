"""
Unit tests for post-recovery re-mitigation scheduler policy.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, MagicMock, patch

from bson import ObjectId

from services.storm.recovery.re_mitigation import (
    BLOCKED_THROTTLE_SECONDS,
    ESCALATED_STATUS,
    consume_re_mitigation_pending,
    handle_consumed_re_mitigation_opportunity,
    handle_storm_reappearance,
    is_post_recovery_re_mitigation_pending,
    should_record_blocked_history,
)
from services.storm.recovery.scheduler import run_recovery_cycle
from services.storm.safety_checks import check_max_attempts
from services.storm.safety_history import SafetyContext
from services.storm.safety_rules import SafetyConfig


class ReMitigationPolicyHelperTests(unittest.TestCase):
    def test_pending_true_when_flag_set(self):
        inc = {"status": "MONITORING", "postRecoveryReMitigationPending": True}
        self.assertTrue(is_post_recovery_re_mitigation_pending(inc))

    def test_pending_false_when_attempted(self):
        inc = {
            "status": "MONITORING",
            "postRecoveryReMitigationPending": False,
            "postRecoveryReMitigationAttempted": True,
        }
        self.assertFalse(is_post_recovery_re_mitigation_pending(inc))

    def test_legacy_monitoring_treated_as_pending(self):
        inc = {"status": "MONITORING"}
        self.assertTrue(is_post_recovery_re_mitigation_pending(inc))

    def test_post_recovery_allowance_grants_one_extra_attempt(self):
        ctx = SafetyContext(
            device_id="507f1f77bcf86cd799439011",
            interface="Gi1/0/1",
            mitigation_attempts=3,
            extras={"post_recovery_remitigation": True},
        )
        cfg = SafetyConfig(maximum_attempts=3)
        passed, _detail = check_max_attempts(ctx, cfg)
        self.assertTrue(passed)

        ctx_no_allowance = SafetyContext(
            device_id="507f1f77bcf86cd799439011",
            interface="Gi1/0/1",
            mitigation_attempts=3,
            extras={},
        )
        passed2, detail2 = check_max_attempts(ctx_no_allowance, cfg)
        self.assertFalse(passed2)
        self.assertIn("Maximum mitigation attempts reached", detail2)

    @patch("services.storm.recovery.re_mitigation.db")
    def test_should_record_blocked_history_throttles_duplicates(self, mock_db):
        now = datetime.now(timezone.utc)
        mock_db.storm_recovery_history.find_one.return_value = {
            "timestamp": now - timedelta(seconds=60),
            "verificationResult": {
                "failedRule": "RULE_13",
                "error": "Maximum mitigation attempts reached (3/3)",
            },
        }
        self.assertFalse(
            should_record_blocked_history(
                "storm-1",
                failed_rule="RULE_13",
                reason="Maximum mitigation attempts reached (3/3)",
                now=now,
            )
        )

    @patch("services.storm.recovery.re_mitigation.db")
    def test_should_record_blocked_after_throttle_window(self, mock_db):
        now = datetime.now(timezone.utc)
        mock_db.storm_recovery_history.find_one.return_value = {
            "timestamp": now - timedelta(seconds=BLOCKED_THROTTLE_SECONDS + 1),
            "verificationResult": {
                "failedRule": "RULE_13",
                "error": "Maximum mitigation attempts reached (3/3)",
            },
        }
        self.assertTrue(
            should_record_blocked_history(
                "storm-1",
                failed_rule="RULE_13",
                reason="Maximum mitigation attempts reached (3/3)",
                now=now,
            )
        )

    @patch("services.storm.recovery.re_mitigation.db")
    def test_consume_re_mitigation_pending(self, mock_db):
        consume_re_mitigation_pending("storm-1")
        update = mock_db.storm_incidents.update_one.call_args[0][1]["$set"]
        self.assertFalse(update["postRecoveryReMitigationPending"])
        self.assertTrue(update["postRecoveryReMitigationAttempted"])

    @patch("services.storm.recovery.re_mitigation.escalate_remitigation_blocked")
    @patch("services.storm.recovery.re_mitigation.record_recovery_history")
    @patch("services.storm.recovery.re_mitigation._resolve_after_successful_re_mitigation")
    def test_handle_success_records_remitigated_only(
        self, mock_resolve, mock_record, mock_escalate
    ):
        inc = {
            "incidentId": "storm-1",
            "deviceId": ObjectId(),
            "interface": "Gi1/0/1",
            "recoveryRetryCount": 0,
        }
        handle_storm_reappearance(
            inc,
            reason="Storm confirmed",
            trigger_result={
                "success": True,
                "incidentId": "storm-2",
                "status": "SUCCESS",
            },
        )
        mock_resolve.assert_called_once()
        mock_record.assert_called_once()
        self.assertEqual(
            mock_record.call_args.kwargs["recovery_status"],
            "REMITIGATED",
        )
        self.assertTrue(mock_record.call_args.kwargs["verification_result"]["success"])
        mock_escalate.assert_not_called()

    @patch("services.storm.recovery.re_mitigation.escalate_remitigation_blocked")
    @patch("services.storm.recovery.re_mitigation.record_recovery_history")
    def test_handle_blocked_escalates(self, mock_record, mock_escalate):
        inc = {
            "incidentId": "storm-1",
            "deviceId": ObjectId(),
            "interface": "Gi1/0/1",
            "recoveryRetryCount": 0,
        }
        handle_storm_reappearance(
            inc,
            reason="Storm confirmed",
            trigger_result={
                "success": False,
                "status": "BLOCKED",
                "error": "Maximum mitigation attempts reached (3/3)",
                "failedRule": "RULE_13",
                "checks": {"attemptsOk": False},
            },
        )
        mock_record.assert_not_called()
        mock_escalate.assert_called_once()

    @patch("services.storm.recovery.re_mitigation.escalate_remitigation_blocked")
    @patch("services.storm.recovery.re_mitigation.record_recovery_history")
    def test_handle_failed_records_failed_and_escalates(self, mock_record, mock_escalate):
        inc = {
            "incidentId": "storm-1",
            "deviceId": ObjectId(),
            "interface": "Gi1/0/1",
            "recoveryRetryCount": 0,
        }
        handle_storm_reappearance(
            inc,
            reason="Storm confirmed",
            trigger_result={
                "success": False,
                "status": "MITIGATION_FAILED",
                "error": "SSH unreachable",
            },
        )
        mock_record.assert_called_once()
        self.assertEqual(mock_record.call_args.kwargs["recovery_status"], "FAILED")
        mock_escalate.assert_called_once()

    @patch("services.storm.recovery.re_mitigation.escalate_remitigation_blocked")
    def test_handle_consumed_opportunity_escalates(self, mock_escalate):
        inc = {
            "incidentId": "storm-1",
            "deviceId": ObjectId(),
            "interface": "Gi1/0/1",
            "postRecoveryReMitigationAttempted": True,
        }
        handle_consumed_re_mitigation_opportunity(inc, reason="Storm confirmed")
        mock_escalate.assert_called_once()


class ReMitigationSchedulerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.device_id = ObjectId("507f1f77bcf86cd799439011")
        self.incident_id = "storm-2026-000004"
        self.interface = "Gi1/0/5"
        self.recovered_at = datetime.now(timezone.utc) - timedelta(seconds=20)

    def _monitoring_incident(self, **extra):
        inc = {
            "incidentId": self.incident_id,
            "deviceId": self.device_id,
            "interface": self.interface,
            "status": "MONITORING",
            "recoveredAt": self.recovered_at,
            "stabilizationEnd": datetime.now(timezone.utc) + timedelta(minutes=5),
            "recoveryRetryCount": 0,
            "postRecoveryReMitigationPending": True,
        }
        inc.update(extra)
        return inc

    @patch("services.storm.recovery.scheduler.get_settings")
    @patch("services.storm.recovery.scheduler.handle_storm_reappearance")
    @patch("services.storm.recovery.scheduler.consume_re_mitigation_pending")
    @patch("services.storm.recovery.scheduler.trigger_re_mitigation")
    @patch("services.storm.recovery.scheduler.db")
    def test_successful_re_mitigation_attempt(
        self, mock_db, mock_trigger, mock_consume, mock_handle, mock_settings
    ):
        mock_settings.return_value = {
            "autoRecovery": False,
            "reMitigationThreshold": 75.0,
        }
        inc = self._monitoring_incident()
        self._configure_storm_detected(mock_db, inc)
        mock_trigger.return_value = {
            "success": True,
            "incidentId": "storm-2026-000099",
            "status": "SUCCESS",
        }

        run_recovery_cycle()

        mock_trigger.assert_called_once_with(
            self.incident_id,
            "Storm confirmed by confirmation engine after recovery.",
            post_recovery_allowance=True,
        )
        mock_consume.assert_called_once_with(self.incident_id, now=ANY)
        mock_handle.assert_called_once()

    @patch("services.storm.recovery.scheduler.get_settings")
    @patch("services.storm.recovery.scheduler.handle_consumed_re_mitigation_opportunity")
    @patch("services.storm.recovery.scheduler.trigger_re_mitigation")
    @patch("services.storm.recovery.scheduler.db")
    def test_escalated_incidents_not_retried(
        self, mock_db, mock_trigger, mock_handle_consumed, mock_settings
    ):
        mock_settings.return_value = {
            "autoRecovery": False,
            "reMitigationThreshold": 75.0,
        }
        inc = self._monitoring_incident(
            postRecoveryReMitigationPending=False,
            postRecoveryReMitigationAttempted=True,
        )
        self._configure_storm_detected(mock_db, inc)

        run_recovery_cycle()

        mock_trigger.assert_not_called()
        mock_handle_consumed.assert_called_once()

    @patch("services.storm.recovery.scheduler.get_settings")
    @patch("services.storm.recovery.scheduler.handle_storm_reappearance")
    @patch("services.storm.recovery.scheduler.consume_re_mitigation_pending")
    @patch("services.storm.recovery.scheduler.trigger_re_mitigation")
    @patch("services.storm.recovery.scheduler.db")
    def test_blocked_rule_13_passes_full_result_to_handler(
        self, mock_db, mock_trigger, mock_consume, mock_handle, mock_settings
    ):
        mock_settings.return_value = {
            "autoRecovery": False,
            "reMitigationThreshold": 75.0,
        }
        inc = self._monitoring_incident()
        self._configure_storm_detected(mock_db, inc)
        mock_trigger.return_value = {
            "success": False,
            "incidentId": self.incident_id,
            "status": "BLOCKED",
            "error": "Maximum mitigation attempts reached (3/3)",
            "failedRule": "RULE_13",
            "checks": {"attemptsOk": False},
            "engine": "mitigation_safety",
        }

        run_recovery_cycle()

        mock_handle.assert_called_once()
        trigger_result = mock_handle.call_args.kwargs["trigger_result"]
        self.assertEqual(trigger_result["failedRule"], "RULE_13")
        self.assertEqual(trigger_result["engine"], "mitigation_safety")

    @patch("services.storm.recovery.re_mitigation.db")
    @patch("services.storm.recovery.re_mitigation.append_timeline_event")
    @patch("services.alert_service.create_storm_remitigation_blocked_alert")
    @patch("services.email_service.send_storm_remitigation_blocked_notification")
    @patch("services.alert_service.mark_alert_email_sent")
    @patch("services.storm.recovery.re_mitigation.record_recovery_history")
    def test_escalation_sets_terminal_status(
        self,
        mock_record,
        mock_mark_email,
        mock_send_email,
        mock_alert,
        mock_timeline,
        mock_db,
    ):
        from services.storm.recovery.re_mitigation import escalate_remitigation_blocked

        mock_alert.return_value = "alert-1"
        mock_send_email.return_value = True
        mock_db.devices.find_one.return_value = None
        mock_db.storm_recovery_history.find_one.return_value = None

        inc = {
            "incidentId": self.incident_id,
            "deviceId": self.device_id,
            "interface": self.interface,
            "status": "MONITORING",
            "recoveryRetryCount": 0,
        }
        escalate_remitigation_blocked(
            inc,
            reason="Maximum mitigation attempts reached (3/3)",
            failed_rule="RULE_13",
            checks={"attemptsOk": False},
        )

        update = mock_db.storm_incidents.update_one.call_args[0][1]["$set"]
        self.assertEqual(update["status"], ESCALATED_STATUS)
        mock_alert.assert_called_once()
        mock_send_email.assert_called_once()
        mock_record.assert_called_once()
        self.assertEqual(mock_record.call_args.kwargs["recovery_status"], "BLOCKED")

    def _configure_storm_detected(self, mock_db, inc):
        def find_side_effect(query):
            if query.get("status") == "MONITORING":
                return [inc]
            return []

        mock_db.storm_incidents.find.side_effect = find_side_effect
        mock_db.storm_confirmation_history.find_one.return_value = {
            "confirmed": True,
            "state": "CONFIRMED",
            "timestamp": self.recovered_at + timedelta(seconds=5),
            "reset": False,
        }
        mock_db.storm_risk_history.find_one.return_value = {
            "riskScore": 90.0,
            "timestamp": self.recovered_at + timedelta(seconds=5),
        }


if __name__ == "__main__":
    unittest.main()
