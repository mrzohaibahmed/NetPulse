"""
Unit tests for the Safety Engine.

Run::

    python -m unittest tests.test_safety -v
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from services.storm.safety import SafetyEngine
from services.storm.safety_history import SafetyContext, is_mitigation_running
from services.storm.safety_rules import SafetyConfig


def _base_ctx(**overrides) -> SafetyContext:
    ctx = SafetyContext(
        device_id="507f1f77bcf86cd799439011",
        interface="Gi1/0/10",
        device={"status": "Online", "hostname": "sw1"},
        iface={"name": "Gi1/0/10", "adminStatus": "up", "operStatus": "up"},
        confirmation={"confirmed": True, "state": "CONFIRMED"},
        risk={"riskScore": 92.0},
        ssh_reachable=True,
        ssh_error=None,
        live_admin_status="up",
        cpu_percent=40.0,
        memory_percent=55.0,
        mitigation_running=False,
        mitigation_attempts=0,
        cooldown_remaining_seconds=0,
        extras={
            "automation_global": True,
            "device_automation": True,
            "interface_automation": True,
            "maintenance_mode": False,
            "device_locked": False,
            "interface_locked": False,
            "manual_override": False,
        },
    )
    for key, value in overrides.items():
        if key in ("extras",) and isinstance(value, dict):
            ctx.extras.update(value)
        else:
            setattr(ctx, key, value)
    return ctx


class SafetyEngineTests(unittest.TestCase):
    def setUp(self):
        self.config = SafetyConfig(
            safety_enabled=True,
            automation_enabled=True,
            cooldown_minutes=30,
            cpu_threshold=90.0,
            memory_threshold=90.0,
            maximum_attempts=3,
            allow_manual_override=False,
            risk_threshold=75.0,
            require_ssh=True,
            fail_open_missing_health=True,
        )
        self.engine = SafetyEngine(config=self.config)

    def _eval(self, ctx: SafetyContext):
        return self.engine.evaluate(
            ctx.device_id,
            ctx.interface,
            context=ctx,
            probe_ssh=False,
            persist=False,
        )

    def test_all_checks_pass(self):
        result = self._eval(_base_ctx())
        self.assertTrue(result.safe)
        self.assertEqual(result.status, "SAFE")
        self.assertIsNone(result.failed_rule)
        self.assertEqual(result.reason, "All safety checks passed")
        self.assertTrue(result.checks.get("stormConfirmed"))
        self.assertFalse(result.checks.get("maintenanceMode"))
        self.assertFalse(result.checks.get("mitigationRunning"))

    def test_device_offline(self):
        result = self._eval(_base_ctx(device={"status": "Offline"}))
        self.assertFalse(result.safe)
        self.assertEqual(result.failed_rule, "RULE_2")

    def test_ssh_failure(self):
        result = self._eval(
            _base_ctx(ssh_reachable=False, ssh_error="Connection timed out")
        )
        self.assertFalse(result.safe)
        self.assertEqual(result.failed_rule, "RULE_3")

    def test_normal_mitigation_still_validates_ssh(self):
        result = self._eval(_base_ctx(ssh_reachable=None))
        self.assertFalse(result.safe)
        self.assertEqual(result.failed_rule, "RULE_3")

    def test_recovery_policy_can_skip_only_ssh_rule(self):
        result = self.engine.evaluate(
            "dev1",
            "Gi1/0/10",
            context=_base_ctx(ssh_reachable=None),
            probe_ssh=False,
            skip_check_codes={"RULE_3"},
            persist=False,
        )
        self.assertTrue(result.safe)
        self.assertIsNone(result.checks.get("sshReachable"))

    def test_maintenance_mode(self):
        result = self._eval(_base_ctx(extras={"maintenance_mode": True}))
        self.assertFalse(result.safe)
        self.assertEqual(result.failed_rule, "RULE_10")
        self.assertIn("Maintenance", result.reason)

    def test_interface_removed(self):
        result = self._eval(_base_ctx(iface=None))
        self.assertFalse(result.safe)
        self.assertEqual(result.failed_rule, "RULE_4")

    def test_interface_already_shutdown(self):
        result = self._eval(
            _base_ctx(
                iface={"name": "Gi1/0/10", "adminStatus": "down"},
                live_admin_status="down",
            )
        )
        self.assertFalse(result.safe)
        self.assertEqual(result.failed_rule, "RULE_5")

    def test_cooldown_active(self):
        result = self._eval(_base_ctx(cooldown_remaining_seconds=600))
        self.assertFalse(result.safe)
        self.assertEqual(result.failed_rule, "RULE_8")
        self.assertEqual(result.status, "WAITING")

    def test_active_mitigation(self):
        result = self._eval(_base_ctx(mitigation_running=True))
        self.assertFalse(result.safe)
        self.assertEqual(result.failed_rule, "RULE_7")
        self.assertTrue(result.checks.get("mitigationRunning"))

    @patch("services.storm.safety_history.LockService.is_mitigation_active")
    def test_safety_uses_shared_mitigation_lock_service(self, mock_is_active):
        mock_is_active.return_value = True
        self.assertTrue(is_mitigation_running("507f1f77bcf86cd799439011", "Gi1/0/10"))
        mock_is_active.assert_called_once()

    def test_automation_disabled(self):
        engine = SafetyEngine(
            config=SafetyConfig(
                automation_enabled=False,
                risk_threshold=75.0,
                require_ssh=True,
                maximum_attempts=3,
                cooldown_minutes=30,
            )
        )
        result = engine.evaluate(
            "dev1",
            "Gi1/0/10",
            context=_base_ctx(),
            probe_ssh=False,
            persist=False,
        )
        self.assertFalse(result.safe)
        self.assertEqual(result.failed_rule, "RULE_9")

    def test_cpu_above_threshold(self):
        result = self._eval(_base_ctx(cpu_percent=95.0))
        self.assertFalse(result.safe)
        self.assertEqual(result.failed_rule, "RULE_14")
        self.assertIn("CPU", result.reason)

    def test_memory_above_threshold(self):
        result = self._eval(_base_ctx(cpu_percent=40.0, memory_percent=97.0))
        self.assertFalse(result.safe)
        self.assertEqual(result.failed_rule, "RULE_14")
        self.assertIn("Memory", result.reason)

    def test_manual_lock_device(self):
        result = self._eval(_base_ctx(extras={"device_locked": True}))
        self.assertFalse(result.safe)
        self.assertEqual(result.failed_rule, "RULE_11")

    def test_manual_lock_interface(self):
        result = self._eval(_base_ctx(extras={"interface_locked": True}))
        self.assertFalse(result.safe)
        self.assertEqual(result.failed_rule, "RULE_12")

    def test_confirmation_false(self):
        result = self._eval(
            _base_ctx(confirmation={"confirmed": False, "state": "PENDING"})
        )
        self.assertFalse(result.safe)
        self.assertEqual(result.failed_rule, "RULE_1")

    def test_risk_below_threshold(self):
        result = self._eval(_base_ctx(risk={"riskScore": 40.0}))
        self.assertFalse(result.safe)
        self.assertEqual(result.failed_rule, "RULE_6")

    def test_max_attempts(self):
        result = self._eval(_base_ctx(mitigation_attempts=3))
        self.assertFalse(result.safe)
        self.assertEqual(result.failed_rule, "RULE_13")


class MitigationAttemptCountTests(unittest.TestCase):
    """Attempt budget must key off successful mitigations, not safety passes."""

    @patch("services.storm.safety_history.load_interface", return_value={})
    @patch("services.storm.safety_history.load_device", return_value={})
    @patch("services.storm.safety_history._db")
    def test_counts_successful_mitigations_only(
        self, mock_db, _device, _iface
    ):
        from services.storm.safety_history import count_mitigation_attempts

        coll = mock_db.return_value.__getitem__.return_value
        coll.count_documents.return_value = 1

        self.assertEqual(
            count_mitigation_attempts("507f1f77bcf86cd799439011", "Gi1/0/9"),
            1,
        )
        query, = coll.count_documents.call_args.args
        self.assertEqual(query["status"], "SUCCESS")
        self.assertEqual(query["interface"], "Gi1/0/9")
        self.assertNotIn("safe", query)


class MitigationCooldownSourceTests(unittest.TestCase):
    """Cooldown must key off successful mitigation, not a safety pass."""

    @patch("services.storm.safety_history._db")
    def test_load_last_successful_mitigation_timestamp(self, mock_db):
        from datetime import datetime, timezone

        from services.storm.safety_history import (
            load_last_successful_mitigation_timestamp,
        )

        ts = datetime(2026, 8, 3, 4, 0, tzinfo=timezone.utc)
        coll = mock_db.return_value.__getitem__.return_value
        coll.find_one.return_value = {"timestamp": ts, "status": "SUCCESS"}

        got = load_last_successful_mitigation_timestamp(
            "507f1f77bcf86cd799439011", "Gi1/0/10"
        )
        self.assertEqual(got, ts)
        query, = coll.find_one.call_args.args
        self.assertEqual(query["status"], "SUCCESS")
        self.assertEqual(query["interface"], "Gi1/0/10")
        self.assertNotIn("safe", query)

    @patch("services.storm.safety_history._db")
    def test_no_mitigation_means_no_cooldown_anchor(self, mock_db):
        from services.storm.safety_history import (
            load_last_successful_mitigation_timestamp,
        )

        coll = mock_db.return_value.__getitem__.return_value
        coll.find_one.return_value = None
        self.assertIsNone(
            load_last_successful_mitigation_timestamp("dev1", "Gi1/0/1")
        )


if __name__ == "__main__":
    unittest.main()
