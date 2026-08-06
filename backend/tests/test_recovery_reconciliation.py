"""
Tests for Recovery Reconciliation (R6 out-of-sync MITIGATED incidents).
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from bson import ObjectId

from services.storm.recovery.reconciliation import (
    RECONCILED_STATUS,
    RECONCILE_NOTE,
    can_reconcile_r6,
    reconcile_mitigated_incident,
    try_reconcile_from_scheduler,
)
from services.storm.recovery.scheduler import run_recovery_cycle


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


class RecoveryReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.device_id = ObjectId("507f1f77bcf86cd799439011")
        self.incident_id = "storm-2026-000005"
        self.interface = "Gi1/0/17"
        self.incident = {
            "incidentId": self.incident_id,
            "deviceId": self.device_id,
            "interface": self.interface,
            "status": "MITIGATED",
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "recoveryRetryCount": 0,
        }

    # Scenario 2: MITIGATED + interface manually UP → RECONCILED
    @patch("services.storm.recovery.reconciliation.record_recovery_history")
    @patch("services.storm.recovery.reconciliation.invalidate_pipeline_after_recovery")
    @patch("services.storm.recovery.reconciliation.append_timeline_event")
    @patch("services.storm.recovery.reconciliation.get_settings")
    @patch("services.storm.recovery.reconciliation.get_incident")
    @patch("services.storm.recovery.reconciliation._db")
    @patch("services.storm.recovery.reconciliation.LockService.is_mitigation_active", return_value=False)
    @patch("services.storm.recovery.reconciliation.is_recovery_active", return_value=False)
    def test_scenario2_reconciles_to_monitoring_with_reconciled_history(
        self,
        _mock_recovery_active,
        _mock_mitigation_active,
        mock_db,
        mock_get_incident,
        mock_settings,
        mock_timeline,
        mock_invalidate,
        mock_record,
    ):
        mock_get_incident.return_value = self.incident
        mock_settings.return_value = {"stabilizationSeconds": 60}
        mock_db.return_value.storm_recovery_history.find_one.return_value = None

        result = reconcile_mitigated_incident(self.incident_id, _r6_val_res())

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "MONITORING")
        update = mock_db.return_value.storm_incidents.update_one.call_args[0][1]["$set"]
        self.assertEqual(update["status"], "MONITORING")
        self.assertTrue(update.get("reconciledAlreadyUp"))
        mock_invalidate.assert_called_once()
        mock_timeline.assert_called_once()
        mock_record.assert_called_once()
        self.assertEqual(
            mock_record.call_args.kwargs["recovery_status"],
            RECONCILED_STATUS,
        )
        verification = mock_record.call_args.kwargs["verification_result"]
        self.assertTrue(verification["reconciled"])
        self.assertEqual(verification["engine"], "recovery_scheduler")
        self.assertEqual(verification["recoveryRule"], "R6")
        self.assertEqual(verification["previousStatus"], "MITIGATED")
        self.assertEqual(verification["newStatus"], "MONITORING")
        self.assertEqual(verification["note"], RECONCILE_NOTE)

    # Scenario 4: blocked by non-R6 rule → no reconciliation
    def test_scenario4_non_r6_rule_not_eligible(self):
        val_res = {
            "failedRule": "R1",
            "checks": {"sshReachable": True, "interfaceAdminDown": False},
        }
        ok, reason = can_reconcile_r6(self.incident, val_res)
        self.assertFalse(ok)
        self.assertIn("R6", reason)

    # Scenario 5: recovery already running → no reconciliation
    @patch("services.storm.recovery.reconciliation.is_recovery_active", return_value=True)
    @patch("services.storm.recovery.reconciliation.LockService.is_mitigation_active", return_value=False)
    def test_scenario5_recovery_active_skips(self, _mock_mit, _mock_rec):
        ok, reason = can_reconcile_r6(self.incident, _r6_val_res())
        self.assertFalse(ok)
        self.assertIn("Recovery execution", reason)

    @patch("services.storm.recovery.reconciliation.is_recovery_active", return_value=False)
    @patch("services.storm.recovery.reconciliation.LockService.is_mitigation_active", return_value=True)
    def test_scenario5_mitigation_active_skips(self, _mock_mit, _mock_rec):
        ok, reason = can_reconcile_r6(self.incident, _r6_val_res())
        self.assertFalse(ok)
        self.assertIn("Mitigation execution", reason)

    @patch("services.storm.recovery.reconciliation.is_recovery_active", return_value=False)
    @patch("services.storm.recovery.reconciliation.LockService.is_mitigation_active", return_value=False)
    def test_resolved_incident_not_eligible(self, _mock_mit, _mock_rec):
        inc = dict(self.incident)
        inc["status"] = "RESOLVED"
        ok, reason = can_reconcile_r6(inc, _r6_val_res())
        self.assertFalse(ok)
        self.assertIn("RESOLVED", reason)

    @patch("services.storm.recovery.scheduler.try_reconcile_from_scheduler")
    @patch("services.storm.recovery.scheduler.record_recovery_history")
    @patch("services.storm.recovery.scheduler.execute_recovery")
    @patch("services.storm.recovery.scheduler.validate_recovery_policy")
    @patch("services.storm.recovery.scheduler.get_settings")
    @patch("services.storm.recovery.scheduler.db")
    def test_scheduler_r6_skips_blocked_when_reconciled(
        self,
        mock_db,
        mock_settings,
        mock_validate,
        mock_execute,
        mock_record,
        mock_try_reconcile,
    ):
        """Scenario 2: reconciliation path must not write BLOCKED."""
        mock_settings.return_value = {
            "autoRecovery": True,
            "reMitigationThreshold": 75.0,
            "maximumRecoveryAttempts": 3,
        }
        mitigated = dict(self.incident)

        def find_side_effect(query):
            if query.get("status") == "MONITORING":
                return []
            if query.get("status") == "MITIGATED":
                return [mitigated]
            return []

        mock_db.storm_incidents.find.side_effect = find_side_effect
        mock_validate.return_value = _r6_val_res()
        mock_try_reconcile.return_value = True

        run_recovery_cycle()

        mock_try_reconcile.assert_called_once()
        mock_execute.assert_not_called()
        mock_record.assert_not_called()

    @patch("services.storm.recovery.scheduler.try_reconcile_from_scheduler", return_value=False)
    @patch("services.storm.recovery.scheduler.record_recovery_history")
    @patch("services.storm.recovery.scheduler.execute_recovery")
    @patch("services.storm.recovery.scheduler.validate_recovery_policy")
    @patch("services.storm.recovery.scheduler.get_settings")
    @patch("services.storm.recovery.scheduler.db")
    def test_scenario4_r1_still_records_blocked(
        self,
        mock_db,
        mock_settings,
        mock_validate,
        mock_execute,
        mock_record,
        _mock_try_reconcile,
    ):
        mock_settings.return_value = {
            "autoRecovery": True,
            "reMitigationThreshold": 75.0,
            "maximumRecoveryAttempts": 3,
        }
        mitigated = dict(self.incident)

        def find_side_effect(query):
            if query.get("status") == "MONITORING":
                return []
            if query.get("status") == "MITIGATED":
                return [mitigated]
            return []

        mock_db.storm_incidents.find.side_effect = find_side_effect
        mock_db.storm_recovery_history.find_one.return_value = None
        mock_validate.return_value = {
            "passed": False,
            "failedRule": "R1",
            "reason": "Storm is still confirmed",
            "checks": {},
        }

        run_recovery_cycle()

        mock_execute.assert_not_called()
        mock_record.assert_called_once()
        self.assertEqual(mock_record.call_args.kwargs["recovery_status"], "BLOCKED")

    @patch("services.storm.recovery.reconciliation.reconcile_mitigated_incident")
    def test_try_reconcile_from_scheduler_returns_bool(self, mock_reconcile):
        mock_reconcile.return_value = {"success": True}
        self.assertTrue(try_reconcile_from_scheduler(self.incident_id, _r6_val_res()))
        mock_reconcile.return_value = {"success": False, "skipped": True}
        self.assertFalse(try_reconcile_from_scheduler(self.incident_id, _r6_val_res()))


if __name__ == "__main__":
    unittest.main()
