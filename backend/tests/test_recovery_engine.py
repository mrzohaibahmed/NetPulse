"""
Unit tests for the Enterprise Recovery Engine.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, MagicMock, call, patch

from bson import ObjectId

from services.storm.recovery.engine import execute_recovery, retry_recovery
from services.storm.recovery.policy import validate_recovery_policy
from services.storm.recovery.scheduler import run_recovery_cycle
from services.storm.recovery.state_machine import RecoveryState, get_next_state


class RecoveryEngineTests(unittest.TestCase):
    def setUp(self):
        self.device_id = ObjectId("507f1f77bcf86cd799439011")
        self.incident_id = "storm-2026-000002"
        self.interface = "Gi1/0/10"

        # Mock Incident Document
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

        # Mock Device Document
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

    def test_state_machine_transitions(self):
        """Verify the state machine logic transitions cleanly."""
        # MITIGATED -> WAITING
        self.assertEqual(get_next_state(RecoveryState.MITIGATED), RecoveryState.WAITING)

        # WAITING -> RECHECK (if cooldown expired)
        self.assertEqual(
            get_next_state(RecoveryState.WAITING, cooldown_expired=True),
            RecoveryState.RECHECK,
        )

        # WAITING -> WAITING (if cooldown not expired)
        self.assertEqual(
            get_next_state(RecoveryState.WAITING, cooldown_expired=False),
            RecoveryState.WAITING,
        )

        # RECHECK -> READY_FOR_RECOVERY (if policy passes)
        self.assertEqual(
            get_next_state(RecoveryState.RECHECK, policy_passed=True),
            RecoveryState.READY_FOR_RECOVERY,
        )

        # RECHECK -> WAITING (if policy fails)
        self.assertEqual(
            get_next_state(RecoveryState.RECHECK, policy_passed=False),
            RecoveryState.WAITING,
        )

        # READY_FOR_RECOVERY -> RECOVERING (when execution started)
        self.assertEqual(
            get_next_state(RecoveryState.READY_FOR_RECOVERY, recovery_started=True),
            RecoveryState.RECOVERING,
        )

        # RECOVERING -> MONITORING (if verification passes)
        self.assertEqual(
            get_next_state(RecoveryState.RECOVERING, verification_passed=True),
            RecoveryState.MONITORING,
        )

        # MONITORING -> REMITIGATE (if storm reappears)
        self.assertEqual(
            get_next_state(RecoveryState.MONITORING, storm_reappeared=True),
            RecoveryState.REMITIGATE,
        )

        # MONITORING -> RECOVERED (if stabilization completes)
        self.assertEqual(
            get_next_state(RecoveryState.MONITORING, stabilization_complete=True),
            RecoveryState.RECOVERED,
        )

        # RECOVERED -> REMITIGATE (if storm reappears after complete recovery)
        self.assertEqual(
            get_next_state(RecoveryState.RECOVERED, storm_reappeared=True),
            RecoveryState.REMITIGATE,
        )

    def test_policy_validation_all_passing(self):
        """Test policy checks pass when all recovery safety conditions are satisfied."""
        # Delegates to Recovery Safety Engine — patch evaluate_recovery_safety.
        with patch(
            "services.storm.recovery.policy.evaluate_recovery_safety"
        ) as mock_eval:
            mock_eval.return_value = MagicMock(
                to_api_dict=lambda: {
                    "passed": True,
                    "safe": True,
                    "checks": {
                        "stormCleared": True,
                        "riskBelowThreshold": True,
                        "cooldownExpired": True,
                        "deviceReachable": True,
                        "sshReachable": True,
                        "interfaceAdminDown": True,
                        "noNewerActiveIncident": True,
                        "recoveryLockAvailable": True,
                    },
                    "reason": "All recovery safety checks passed",
                    "failedRule": None,
                    "status": "SAFE",
                }
            )
            res = validate_recovery_policy(self.incident_id)
            self.assertTrue(res["passed"], f"Policy check failed: {res.get('reason')}")
            mock_eval.assert_called_once()

    @patch(
        "services.storm.recovery.safety.recovery_locks_available", return_value=True
    )
    @patch("services.storm.recovery.safety.SSHMitigationExecutor")
    @patch("services.storm.recovery.safety.get_settings")
    @patch("services.storm.recovery.safety.get_incident")
    @patch("services.storm.recovery.safety._db")
    def test_policy_fails_if_storm_confirmed(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_settings,
        mock_ssh,
        _mock_locks,
    ):
        """Storm still confirmed → R1 fails (Recovery Safety, not Mitigation RULE_1)."""
        mock_get_incident.return_value = self.incident_doc
        mock_settings.return_value = {
            "cooldownMinutes": 5,
            "reMitigationThreshold": 75.0,
        }
        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        fake_db.storm_mitigation_history.find_one.return_value = {
            "timestamp": datetime.now(timezone.utc) - timedelta(minutes=10),
            "status": "SUCCESS",
        }
        fake_db.storm_confirmation_history.find_one.return_value = {"confirmed": True}
        fake_db.storm_risk_history.find_one.return_value = {"riskScore": 15.0}
        fake_db.storm_incidents.find_one.return_value = None
        mock_db_fn.return_value = fake_db

        res = validate_recovery_policy(self.incident_id)
        self.assertFalse(res["passed"])
        self.assertFalse(res["checks"]["stormCleared"])
        self.assertEqual(res["failedRule"], "R1")
        mock_ssh.assert_not_called()

    @patch(
        "services.storm.recovery.safety.recovery_locks_available", return_value=True
    )
    @patch("services.storm.recovery.safety.SSHMitigationExecutor")
    @patch("services.storm.recovery.safety.get_settings")
    @patch("services.storm.recovery.safety.get_incident")
    @patch("services.storm.recovery.safety._db")
    def test_policy_offline_stale_response_time_not_reachable(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_settings,
        mock_ssh,
        _mock_locks,
    ):
        """Offline status with leftover responseTime must not pass R4."""
        mock_get_incident.return_value = self.incident_doc
        mock_settings.return_value = {
            "cooldownMinutes": 5,
            "reMitigationThreshold": 75.0,
        }
        offline_device = dict(self.device_doc)
        offline_device["status"] = "Not Reachable"
        offline_device["responseTime"] = 12.5

        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = offline_device
        fake_db.storm_mitigation_history.find_one.return_value = {
            "timestamp": datetime.now(timezone.utc) - timedelta(minutes=10),
            "status": "SUCCESS",
        }
        fake_db.storm_confirmation_history.find_one.return_value = {"confirmed": False}
        fake_db.storm_risk_history.find_one.return_value = {"riskScore": 10.0}
        fake_db.storm_incidents.find_one.return_value = None
        mock_db_fn.return_value = fake_db

        res = validate_recovery_policy(self.incident_id)
        self.assertFalse(res["checks"]["deviceReachable"])
        self.assertFalse(res["passed"])
        self.assertEqual(res["failedRule"], "R4")
        mock_ssh.assert_not_called()

    @patch(
        "services.storm.recovery.safety.recovery_locks_available", return_value=True
    )
    @patch("services.storm.recovery.safety.SSHMitigationExecutor")
    @patch("services.storm.recovery.safety.get_settings")
    @patch("services.storm.recovery.safety.get_incident")
    @patch("services.storm.recovery.safety._db")
    def test_policy_online_status_is_reachable_regardless_of_response_time(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_settings,
        mock_ssh,
        _mock_locks,
    ):
        """Online status is authoritative for R4 even when responseTime is null."""
        mock_get_incident.return_value = self.incident_doc
        mock_settings.return_value = {
            "cooldownMinutes": 5,
            "reMitigationThreshold": 75.0,
        }
        online_device = dict(self.device_doc)
        online_device["status"] = "Online"
        online_device["responseTime"] = None

        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = online_device
        fake_db.storm_mitigation_history.find_one.return_value = {
            "timestamp": datetime.now(timezone.utc) - timedelta(minutes=10),
            "status": "SUCCESS",
        }
        fake_db.storm_confirmation_history.find_one.return_value = {"confirmed": False}
        fake_db.storm_risk_history.find_one.return_value = {"riskScore": 10.0}
        fake_db.storm_incidents.find_one.return_value = None
        fake_db.interfaces.find_one.return_value = {"adminStatus": "down"}
        mock_db_fn.return_value = fake_db
        mock_ssh.return_value.__enter__.return_value.collector = MagicMock()

        with patch(
            "services.storm.diagnostics.snapshots.parse_interface_snapshot",
            return_value={"adminStatus": "down", "available": True},
        ):
            res = validate_recovery_policy(self.incident_id)

        self.assertTrue(res["checks"]["deviceReachable"])
        self.assertTrue(res["passed"])
        self.assertIsNone(res.get("failedRule"))

    @patch("services.storm.recovery.engine.collect_post_recovery_stats")
    @patch("services.storm.recovery.engine.LockService.release_recovery_locks")
    @patch("services.storm.recovery.engine.LockService.acquire_recovery_locks")
    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine._db")
    @patch("services.storm.recovery.engine.validate_recovery_policy")
    @patch("services.storm.recovery.engine.SSHMitigationExecutor")
    @patch("services.storm.recovery.post_recovery.invalidate_pipeline_after_recovery")
    def test_successful_recovery_execution(
        self,
        mock_invalidate,
        mock_ssh,
        mock_val,
        mock_db_fn,
        mock_get_incident,
        mock_acquire_locks,
        mock_release_locks,
        mock_collect_stats,
    ):
        """Test successful recovery execute, verification, and status monitoring transition."""
        mock_get_incident.return_value = self.incident_doc
        mock_val.return_value = {"passed": True}
        mock_invalidate.return_value = {"ok": True, "cancelledIncidents": 0}

        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        mock_db_fn.return_value = fake_db
        mock_acquire_locks.return_value = ("recovery:device", "recovery:interface")
        mock_collect_stats.return_value = {"adminStatus": "up", "operStatus": "up"}

        mock_collector = MagicMock()
        mock_ssh.return_value.__enter__.return_value = mock_collector
        # Mock verification CLI output showing interface up (admin status up)
        mock_collector.creds.vendor = "cisco_ios"
        mock_collector.collector.run_command.return_value = "GigabitEthernet1/0/10 is up, line protocol is up\n admin status is up"

        res = execute_recovery(self.incident_id, force=False, operator="admin")
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "MONITORING")

        # Verify incident status updated to MONITORING
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
        mock_invalidate.assert_called_once()
        self.assertEqual(mock_invalidate.call_args[0][0], self.device_id)
        self.assertEqual(mock_invalidate.call_args[0][1], self.interface)
        self.assertNotIn("advance_generation", mock_invalidate.call_args.kwargs)

    @patch("services.storm.recovery.engine.LockService.release_recovery_locks")
    @patch("services.storm.recovery.engine.LockService.acquire_recovery_locks")
    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine._db")
    @patch("services.storm.recovery.engine.validate_recovery_policy")
    @patch("services.storm.recovery.engine.SSHMitigationExecutor")
    def test_verification_failure_triggers_retry(
        self,
        mock_ssh,
        mock_val,
        mock_db_fn,
        mock_get_incident,
        mock_acquire_locks,
        mock_release_locks,
    ):
        """Test verification failure increments retry count and updates state."""
        mock_get_incident.return_value = self.incident_doc
        mock_val.return_value = {"passed": True}

        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        mock_db_fn.return_value = fake_db
        mock_acquire_locks.return_value = ("recovery:device", "recovery:interface")

        mock_collector = MagicMock()
        mock_ssh.return_value.__enter__.return_value = mock_collector
        mock_collector.creds.vendor = "cisco_ios"
        # admin status is down! (verification fails)
        mock_collector.collector.run_command.return_value = "GigabitEthernet1/0/10 is administratively down\n admin status is down"

        res = execute_recovery(self.incident_id, force=False, operator="admin")
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "FAILURE")

        # Check retry count incremented
        fake_db.storm_incidents.update_one.assert_any_call(
            {"incidentId": self.incident_id},
            {
                "$set": {
                    "recoveryRetryCount": 1,
                    "updatedAt": ANY,
                }
            },
        )

        # Soft failure must leave incident as MITIGATED (port still shut down)
        for call in fake_db.storm_incidents.update_one.call_args_list:
            set_fields = call[0][1].get("$set", {})
            self.assertNotEqual(
                set_fields.get("status"),
                "MITIGATION_FAILED",
                "recovery soft-failure must not mislabel status as MITIGATION_FAILED",
            )
            if "status" in set_fields:
                self.assertNotEqual(set_fields["status"], "RECOVERY_FAILED")

    @patch("services.storm.recovery.engine.LockService.release_recovery_locks")
    @patch("services.storm.recovery.engine.LockService.acquire_recovery_locks")
    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine._db")
    @patch("services.storm.recovery.engine.validate_recovery_policy")
    @patch("services.storm.recovery.engine.SSHMitigationExecutor")
    def test_recovery_soft_failure_keeps_mitigated_status(
        self,
        mock_ssh,
        mock_val,
        mock_db_fn,
        mock_get_incident,
        mock_acquire_locks,
        mock_release_locks,
    ):
        """Failed recovery with retries remaining must keep status MITIGATED."""
        mock_get_incident.return_value = self.incident_doc  # status MITIGATED
        mock_val.return_value = {"passed": True}

        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        mock_db_fn.return_value = fake_db
        mock_acquire_locks.return_value = ("recovery:device", "recovery:interface")

        mock_collector = MagicMock()
        mock_ssh.return_value.__enter__.return_value = mock_collector
        mock_collector.creds.vendor = "cisco_ios"
        mock_collector.collector.run_command.return_value = (
            "GigabitEthernet1/0/10 is administratively down\n admin status is down"
        )

        res = execute_recovery(self.incident_id, force=False, operator="admin")
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "FAILURE")
        self.assertEqual(res["retryCount"], 1)

        status_updates = [
            call[0][1].get("$set", {}).get("status")
            for call in fake_db.storm_incidents.update_one.call_args_list
            if "status" in call[0][1].get("$set", {})
        ]
        self.assertEqual(
            status_updates,
            [],
            "soft-failure must not rewrite incident status; leave MITIGATED",
        )

    @patch("services.storm.recovery.engine.LockService.release_recovery_locks")
    @patch("services.storm.recovery.engine.LockService.acquire_recovery_locks")
    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine._db")
    @patch("services.storm.recovery.engine.validate_recovery_policy")
    @patch("services.storm.recovery.engine.SSHMitigationExecutor")
    def test_retry_limit_exceeded_escalation(
        self,
        mock_ssh,
        mock_val,
        mock_db_fn,
        mock_get_incident,
        mock_acquire_locks,
        mock_release_locks,
    ):
        """Test that exceeding retry limit updates status to RECOVERY_FAILED."""
        # Setup incident with 2 existing failed retries (max is 3)
        doc = dict(self.incident_doc)
        doc["recoveryRetryCount"] = 2
        mock_get_incident.return_value = doc
        mock_val.return_value = {"passed": True}

        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        mock_db_fn.return_value = fake_db
        mock_acquire_locks.return_value = ("recovery:device", "recovery:interface")

        mock_collector = MagicMock()
        mock_ssh.return_value.__enter__.return_value = mock_collector
        mock_collector.creds.vendor = "cisco_ios"
        mock_collector.collector.run_command.return_value = "admin status is down"

        res = execute_recovery(self.incident_id, force=False, operator="admin")
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "RECOVERY_FAILED")

        # Verify status is set to RECOVERY_FAILED
        fake_db.storm_incidents.update_one.assert_any_call(
            {"incidentId": self.incident_id},
            {
                "$set": {
                    "status": "RECOVERY_FAILED",
                    "updatedAt": ANY,
                }
            },
        )

    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine._db")
    def test_lock_conflict_raises_error(self, mock_db_fn, mock_get_incident):
        """Test lock conflict (DuplicateKeyError) aborts execution immediately."""
        mock_get_incident.return_value = self.incident_doc

        fake_db = MagicMock()
        mock_db_fn.return_value = fake_db

        with patch(
            "services.storm.recovery.engine.LockService.acquire_recovery_locks",
            side_effect=ValueError("Recovery lock conflict"),
        ), self.assertRaises(ValueError):
            execute_recovery(self.incident_id)

    @patch("services.storm.recovery.scheduler.get_settings")
    @patch("services.storm.recovery.scheduler.handle_storm_reappearance")
    @patch("services.storm.recovery.scheduler.consume_re_mitigation_pending")
    @patch("services.storm.recovery.scheduler.db")
    @patch("services.storm.recovery.scheduler.trigger_re_mitigation")
    def test_scheduler_stabilization_re_mitigates_if_storm_reappears(
        self, mock_trigger, mock_db, mock_consume, mock_handle, mock_settings
    ):
        """Test that scheduling cycle triggers re-mitigation if storm reappears during stabilization."""
        mock_settings.return_value = {
            "autoRecovery": True,
            "reMitigationThreshold": 75.0,
            "maximumRecoveryAttempts": 3,
        }
        # Mock incident in MONITORING status with recoveredAt anchor
        recovered_at = datetime.now(timezone.utc) - timedelta(seconds=20)
        inc = dict(self.incident_doc)
        inc["status"] = "MONITORING"
        inc["recoveredAt"] = recovered_at
        inc["stabilizationEnd"] = datetime.now(timezone.utc) + timedelta(seconds=30)
        inc["postRecoveryReMitigationPending"] = True

        def find_side_effect(query):
            if query.get("status") == "MONITORING":
                return [inc]
            # Do not bleed MONITORING rows into the MITIGATED sweep.
            return []

        mock_db.storm_incidents.find.side_effect = find_side_effect

        # Storm confirmed AFTER recovery (fresh evidence only)
        mock_db.storm_confirmation_history.find_one.return_value = {
            "confirmed": True,
            "state": "CONFIRMED",
            "timestamp": recovered_at + timedelta(seconds=5),
            "reset": False,
        }
        mock_db.storm_risk_history.find_one.return_value = {
            "riskScore": 90.0,
            "timestamp": recovered_at + timedelta(seconds=5),
        }

        mock_trigger.return_value = {
            "success": True,
            "incidentId": "storm-2026-000099",
            "status": "SUCCESS",
        }
        run_recovery_cycle()
        mock_trigger.assert_called_with(
            self.incident_id,
            "Storm confirmed by confirmation engine after recovery.",
            post_recovery_allowance=True,
        )
        mock_consume.assert_called_once()
        mock_handle.assert_called_once()
        handle_kwargs = mock_handle.call_args.kwargs
        self.assertEqual(handle_kwargs["trigger_result"]["success"], True)
        self.assertEqual(handle_kwargs["trigger_result"]["incidentId"], "storm-2026-000099")

    @patch("services.storm.recovery.scheduler.execute_recovery")
    @patch("services.storm.recovery.scheduler.validate_recovery_policy")
    @patch("services.storm.recovery.scheduler.get_settings")
    @patch("services.storm.recovery.scheduler.db")
    def test_sweep_excludes_mitigation_failed(
        self, mock_db, mock_settings, mock_validate, mock_execute
    ):
        """True MITIGATION_FAILED incidents must not be selected by auto-recovery."""
        mock_settings.return_value = {
            "autoRecovery": True,
            "reMitigationThreshold": 75.0,
            "maximumRecoveryAttempts": 3,
        }
        failed = dict(self.incident_doc)
        failed["status"] = "MITIGATION_FAILED"
        failed["recoveryRetryCount"] = 0

        def find_side_effect(query):
            status = query.get("status")
            if status == "MONITORING":
                return []
            if status == "MITIGATED":
                return []
            # Legacy/buggy $in query would incorrectly return the failed incident
            if isinstance(status, dict) and "MITIGATION_FAILED" in status.get("$in", []):
                return [failed]
            return []

        mock_db.storm_incidents.find.side_effect = find_side_effect

        run_recovery_cycle()

        find_queries = [c.args[0] for c in mock_db.storm_incidents.find.call_args_list]
        self.assertIn({"status": "MITIGATED"}, find_queries)
        self.assertNotIn(
            {"status": {"$in": ["MITIGATED", "MITIGATION_FAILED"]}},
            find_queries,
        )
        mock_validate.assert_not_called()
        mock_execute.assert_not_called()

    @patch("services.storm.recovery.scheduler.execute_recovery")
    @patch("services.storm.recovery.scheduler.validate_recovery_policy")
    @patch("services.storm.recovery.scheduler.get_settings")
    @patch("services.storm.recovery.scheduler.db")
    def test_sweep_selects_mitigated_including_soft_fail_retry(
        self, mock_db, mock_settings, mock_validate, mock_execute
    ):
        """MITIGATED after a soft-failed recovery retry must still be swept next cycle."""
        mock_settings.return_value = {
            "autoRecovery": True,
            "reMitigationThreshold": 75.0,
            "maximumRecoveryAttempts": 3,
        }
        # Part 1 outcome: soft-fail left status MITIGATED and bumped retry count
        soft_failed = dict(self.incident_doc)
        soft_failed["status"] = "MITIGATED"
        soft_failed["recoveryRetryCount"] = 1

        def find_side_effect(query):
            if query.get("status") == "MONITORING":
                return []
            if query.get("status") == "MITIGATED":
                return [soft_failed]
            return []

        mock_db.storm_incidents.find.side_effect = find_side_effect
        mock_validate.return_value = {"passed": True}
        mock_execute.return_value = {"success": True, "status": "MONITORING"}

        run_recovery_cycle()

        mock_validate.assert_called_once_with(self.incident_id)
        mock_execute.assert_called_once_with(
            self.incident_id,
            force=False,
            operator="SYSTEM",
            skip_policy_validation=True,
        )

    @patch(
        "services.storm.recovery.safety.recovery_locks_available", return_value=True
    )
    @patch("services.storm.recovery.safety.SSHMitigationExecutor")
    @patch("services.storm.recovery.safety.get_settings")
    @patch("services.storm.recovery.safety.get_incident")
    @patch("services.storm.recovery.safety._db")
    def test_policy_rejects_mitigation_failed(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_settings,
        mock_ssh,
        _mock_locks,
    ):
        """MITIGATION_FAILED is not recoverable (port was never shut down)."""
        failed_incident = dict(self.incident_doc)
        failed_incident["status"] = "MITIGATION_FAILED"
        mock_get_incident.return_value = failed_incident
        mock_settings.return_value = {
            "cooldownMinutes": 5,
            "reMitigationThreshold": 75.0,
        }
        mock_db_fn.return_value = MagicMock()

        res = validate_recovery_policy(self.incident_id)
        self.assertFalse(res["passed"])
        self.assertEqual(res["failedRule"], "R0")
        mock_ssh.assert_not_called()

    @patch("services.storm.safety.evaluate")
    @patch(
        "services.storm.recovery.safety.recovery_locks_available", return_value=True
    )
    @patch("services.storm.recovery.safety.SSHMitigationExecutor")
    @patch("services.storm.recovery.safety.get_settings")
    @patch("services.storm.recovery.safety.get_incident")
    @patch("services.storm.recovery.safety._db")
    def test_policy_does_not_call_mitigation_safety(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_settings,
        mock_ssh,
        _mock_locks,
        mock_mitigation_safety,
    ):
        """Recovery policy must use Recovery Safety only — never Mitigation Safety."""
        mock_get_incident.return_value = self.incident_doc
        mock_settings.return_value = {
            "cooldownMinutes": 5,
            "reMitigationThreshold": 75.0,
        }
        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        fake_db.storm_mitigation_history.find_one.return_value = {
            "timestamp": datetime.now(timezone.utc) - timedelta(minutes=10),
            "status": "SUCCESS",
        }
        fake_db.storm_confirmation_history.find_one.return_value = {"confirmed": False}
        fake_db.storm_risk_history.find_one.return_value = {"riskScore": 20.0}
        fake_db.storm_incidents.find_one.return_value = None
        fake_db.interfaces.find_one.return_value = {"adminStatus": "down"}
        mock_db_fn.return_value = fake_db
        mock_ssh.return_value.__enter__.return_value.collector = MagicMock()

        with patch(
            "services.storm.diagnostics.snapshots.parse_interface_snapshot",
            return_value={"adminStatus": "down", "available": True},
        ):
            res = validate_recovery_policy(self.incident_id)

        self.assertTrue(res["passed"])
        mock_mitigation_safety.assert_not_called()
        self.assertNotIn("safetyPassed", res["checks"])
        self.assertIn("stormCleared", res["checks"])

    @patch("services.storm.recovery.engine.append_timeline_event")
    @patch("services.storm.mitigation.engine.execute_mitigation")
    @patch("services.storm.orchestrator.prepare")
    @patch("services.storm.safety.evaluate")
    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine._db")
    def test_trigger_re_mitigation_uses_prepared_incident_id(
        self,
        mock_db_fn,
        mock_get_incident,
        mock_safety,
        mock_prepare,
        mock_execute_mitigation,
        mock_append_timeline,
    ):
        mock_get_incident.return_value = dict(self.incident_doc, status="MONITORING")
        mock_db_fn.return_value = MagicMock()
        mock_safety.return_value = MagicMock(
            safe=True,
            reason="All safety checks passed",
            failed_rule=None,
            status="SAFE",
            timestamp=datetime.now(timezone.utc),
            checks={"stormConfirmed": True},
        )
        mock_prepare.return_value = {
            "ready": True,
            "incidentId": "storm-2026-000123",
        }
        mock_execute_mitigation.return_value = {
            "success": True,
            "status": "SUCCESS",
        }

        from services.storm.recovery.engine import trigger_re_mitigation

        res = trigger_re_mitigation(self.incident_id, "Storm confirmed by confirmation engine.")

        self.assertTrue(res["success"])
        self.assertEqual(res["incidentId"], "storm-2026-000123")
        mock_safety.assert_called_once()
        mock_execute_mitigation.assert_called_once_with(
            "storm-2026-000123", "SHUTDOWN", operator="SYSTEM"
        )
        self.assertIn(
            call(
                "storm-2026-000123",
                "Re-Mitigation Started",
                detail=f"Triggered by recovery incident {self.incident_id}",
            ),
            mock_append_timeline.mock_calls,
        )


if __name__ == "__main__":
    unittest.main()
