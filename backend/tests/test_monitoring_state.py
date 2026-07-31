"""
Tests for enterprise interface monitoring state management.

Run from the backend directory::

    python -m unittest tests.test_monitoring_state tests.test_eligibility -v
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from models.interface import create_interface
from services.interface_collection.classifier import classify_interface
from services.interface_collection.monitoring_state import (
    MONITORING_MODE_AUTO,
    MONITORING_MODE_DISABLED_BY_USER,
    compute_monitoring_view,
    migrate_interface_monitoring_state,
    preference_enabled_for_mode,
    resolve_monitoring_mode,
)
from services.storm.config import StormConfig
from services.storm.confirmation import ConfirmationEngine
from services.storm.confirmation_rules import ConfirmationConfig
from services.storm.eligibility import EligibilityEngine, evaluate
from services.storm.risk_engine import RiskScoreEngine
from services.storm.thresholds import RiskConfig


def _iface(**overrides):
    base = {
        "name": "Gi1/0/5",
        "description": "",
        "adminStatus": "up",
        "operStatus": "up",
        "portMode": "access",
        "mode": "access",
        "neighbor": {},
    }
    base.update(overrides)
    return base


class MonitoringStateUnitTests(unittest.TestCase):
    def test_new_interface_starts_monitored(self):
        classified = classify_interface(_iface())
        self.assertEqual(classified["monitoringMode"], MONITORING_MODE_AUTO)
        self.assertTrue(classified["monitoringEnabled"])

    def test_admin_down_does_not_latch_monitoring_preference(self):
        classified = classify_interface(_iface(adminStatus="down", operStatus="down"))
        self.assertEqual(classified["monitoringMode"], MONITORING_MODE_AUTO)
        self.assertTrue(classified["monitoringEnabled"])

    def test_user_disabled_persists_through_classifier(self):
        classified = classify_interface(
            _iface(
                monitoringMode=MONITORING_MODE_DISABLED_BY_USER,
                monitoringEnabled=False,
                adminStatus="up",
            )
        )
        self.assertEqual(
            classified["monitoringMode"], MONITORING_MODE_DISABLED_BY_USER
        )
        self.assertFalse(classified["monitoringEnabled"])

    def test_resolve_mode_prefers_existing_user_disable(self):
        mode = resolve_monitoring_mode(
            {},
            existing={"monitoringMode": MONITORING_MODE_DISABLED_BY_USER},
        )
        self.assertEqual(mode, MONITORING_MODE_DISABLED_BY_USER)

    def test_resolve_mode_does_not_preserve_bare_false(self):
        """Legacy sticky latch must not be treated as administrator intent."""
        mode = resolve_monitoring_mode(
            {},
            existing={"monitoringEnabled": False},
        )
        self.assertEqual(mode, MONITORING_MODE_AUTO)

    def test_create_interface_writes_mode(self):
        doc = create_interface(
            device_id="x",
            hostname="sw1",
            ip_address="1.1.1.1",
            name="Gi1/0/1",
            admin_status="down",
            oper_status="down",
        )
        self.assertEqual(doc["monitoringMode"], MONITORING_MODE_AUTO)
        self.assertTrue(doc["monitoringEnabled"])

    def test_compute_view_distinguishes_reasons(self):
        disabled = compute_monitoring_view(
            monitoring_mode=MONITORING_MODE_DISABLED_BY_USER,
            admin_status="up",
            oper_status="up",
        )
        self.assertTrue(disabled["administratorDisabled"])
        self.assertFalse(disabled["effectiveMonitoring"])
        self.assertEqual(disabled["monitoringReason"], "administrator_disabled")

        down = compute_monitoring_view(
            monitoring_mode=MONITORING_MODE_AUTO,
            admin_status="down",
            oper_status="down",
        )
        self.assertFalse(down["administratorDisabled"])
        self.assertTrue(down["monitoringEnabled"])
        self.assertFalse(down["effectiveMonitoring"])
        self.assertEqual(down["monitoringReason"], "administrative_down")

        active = compute_monitoring_view(
            monitoring_mode=MONITORING_MODE_AUTO,
            admin_status="up",
            oper_status="up",
        )
        self.assertTrue(active["effectiveMonitoring"])
        self.assertEqual(active["monitoringReason"], "active")


class EligibilityMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.engine = EligibilityEngine(
            config=StormConfig(
                enable_eligibility=True,
                allow_management_ports=False,
                allow_trunks=False,
                allow_infrastructure_ports=False,
                allow_protected_ports=False,
                confidence=100,
            )
        )

    def _port(self, **overrides):
        base = {
            "deviceId": "507f1f77bcf86cd799439011",
            "name": "Gi1/0/5",
            "adminStatus": "up",
            "operStatus": "up",
            "isAccess": True,
            "isTrunk": False,
            "isUplink": False,
            "isInfrastructure": False,
            "isManagement": False,
            "isProtected": False,
            "monitoringMode": MONITORING_MODE_AUTO,
            "monitoringEnabled": True,
            "portMode": "access",
            "neighbor": {},
        }
        base.update(overrides)
        return base

    def test_admin_down_temporarily_ineligible_not_monitoring_rule(self):
        result = self.engine.evaluate(self._port(adminStatus="down"))
        self.assertFalse(result.eligible)
        self.assertEqual(result.failed_rule, "RULE_2")
        self.assertEqual(result.reason, "Administrative Down")
        self.assertTrue(result.checks.monitoring)

    def test_admin_up_restores_eligibility_automatically(self):
        down = self.engine.evaluate(self._port(adminStatus="down"))
        self.assertFalse(down.eligible)
        up = self.engine.evaluate(self._port(adminStatus="up", operStatus="up"))
        self.assertTrue(up.eligible)
        self.assertEqual(up.reason, "Access Port")

    def test_user_disabled_fails_rule_1(self):
        result = self.engine.evaluate(
            self._port(
                monitoringMode=MONITORING_MODE_DISABLED_BY_USER,
                monitoringEnabled=False,
            )
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.failed_rule, "RULE_1")
        self.assertEqual(result.reason, "Monitoring Disabled")

    def test_legacy_monitoring_enabled_false_still_fails_rule_1(self):
        """Backward compatible callers that only set monitoringEnabled=false. """
        result = self.engine.evaluate(
            {
                "deviceId": "507f1f77bcf86cd799439011",
                "name": "Gi1/0/5",
                "adminStatus": "up",
                "operStatus": "up",
                "isAccess": True,
                "monitoringEnabled": False,
                "portMode": "access",
            }
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.failed_rule, "RULE_1")


class RediscoveryPreserveTests(unittest.TestCase):
    @patch("services.interface_collection.collector.db")
    def test_rediscovery_preserves_administrator_disable(self, mock_db):
        from bson import ObjectId
        from services.interface_collection.collector import persist_interfaces

        device_id = ObjectId()
        existing = {
            "_id": ObjectId(),
            "deviceId": device_id,
            "name": "Gi1/0/5",
            "isProtected": False,
            "monitoringMode": MONITORING_MODE_DISABLED_BY_USER,
            "monitoringEnabled": False,
        }
        mock_db.interfaces.find.return_value = [existing]
        mock_db.interfaces.update_one.return_value = MagicMock(acknowledged=True)
        mock_db.interfaces.delete_one.return_value = MagicMock()
        mock_db.interfaces.delete_many.return_value = MagicMock(deleted_count=0)

        written = persist_interfaces(
            device_id=device_id,
            hostname="sw1",
            ip_address="10.0.0.1",
            raw_interfaces=[
                {
                    "name": "Gi1/0/5",
                    "description": "",
                    "admin_status": "up",
                    "oper_status": "up",
                    "mode": "access",
                    "speed": "1000",
                    "duplex": "full",
                }
            ],
        )
        self.assertEqual(written, 1)
        args, kwargs = mock_db.interfaces.update_one.call_args
        set_fields = kwargs["$set"] if False else args[1]["$set"]
        self.assertEqual(set_fields["monitoringMode"], MONITORING_MODE_DISABLED_BY_USER)
        self.assertFalse(set_fields["monitoringEnabled"])

    @patch("services.interface_collection.collector.db")
    def test_rediscovery_clears_sticky_false_without_user_mode(self, mock_db):
        from bson import ObjectId
        from services.interface_collection.collector import persist_interfaces

        device_id = ObjectId()
        existing = {
            "_id": ObjectId(),
            "deviceId": device_id,
            "name": "Gi1/0/5",
            "monitoringEnabled": False,  # sticky latch, no monitoringMode
        }
        mock_db.interfaces.find.return_value = [existing]
        mock_db.interfaces.update_one.return_value = MagicMock(acknowledged=True)
        mock_db.interfaces.delete_one.return_value = MagicMock()
        mock_db.interfaces.delete_many.return_value = MagicMock(deleted_count=0)

        persist_interfaces(
            device_id=device_id,
            hostname="sw1",
            ip_address="10.0.0.1",
            raw_interfaces=[
                {
                    "name": "Gi1/0/5",
                    "description": "",
                    "admin_status": "up",
                    "oper_status": "up",
                    "mode": "access",
                    "speed": "1000",
                    "duplex": "full",
                }
            ],
        )
        args, _kwargs = mock_db.interfaces.update_one.call_args
        set_fields = args[1]["$set"]
        self.assertEqual(set_fields["monitoringMode"], MONITORING_MODE_AUTO)
        self.assertTrue(set_fields["monitoringEnabled"])


class MigrationTests(unittest.TestCase):
    @patch("services.interface_collection.monitoring_state.logger")
    def test_migration_restores_sticky_and_preserves_user(self, _logger):
        sticky = {
            "_id": "1",
            "name": "Gi1/0/5",
            "adminStatus": "up",
            "operStatus": "up",
            "monitoringEnabled": False,
        }
        user = {
            "_id": "2",
            "name": "Gi1/0/6",
            "adminStatus": "up",
            "operStatus": "up",
            "monitoringMode": MONITORING_MODE_DISABLED_BY_USER,
            "monitoringEnabled": False,
        }
        admin_down = {
            "_id": "3",
            "name": "Gi1/0/7",
            "adminStatus": "down",
            "operStatus": "down",
            "monitoringEnabled": False,
        }

        mock_db = MagicMock()
        mock_db.interfaces.find.return_value = [sticky, user, admin_down]

        with patch(
            "services.interface_collection.monitoring_state.db",
            mock_db,
            create=True,
        ):
            # Import path uses config.database.db inside the function
            with patch(
                "config.database.db",
                mock_db,
            ):
                summary = migrate_interface_monitoring_state(apply=True)

        self.assertEqual(summary["restoredSticky"], 1)
        self.assertEqual(summary["preservedUserDisabled"], 1)
        self.assertEqual(summary["upgradedLegacy"], 1)
        self.assertEqual(mock_db.interfaces.update_one.call_count, 2)

        # Idempotent second pass
        sticky_fixed = {
            **sticky,
            "monitoringMode": MONITORING_MODE_AUTO,
            "monitoringEnabled": True,
        }
        user_ok = dict(user)
        down_fixed = {
            **admin_down,
            "monitoringMode": MONITORING_MODE_AUTO,
            "monitoringEnabled": True,
        }
        mock_db.interfaces.find.return_value = [sticky_fixed, user_ok, down_fixed]
        mock_db.interfaces.update_one.reset_mock()
        with patch("config.database.db", mock_db):
            summary2 = migrate_interface_monitoring_state(apply=True)
        self.assertEqual(summary2["preservedUserDisabled"], 1)
        self.assertEqual(summary2["unchanged"], 2)
        self.assertEqual(mock_db.interfaces.update_one.call_count, 0)


class PipelineResumeTests(unittest.TestCase):
    """Risk / confirmation resume once preference is restored."""

    def test_risk_engine_evaluates_restored_eligible_interface(self):
        engine = RiskScoreEngine(
            config=RiskConfig(enable_risk=True),
            analyzers=(),
        )
        skipped = engine.calculate(
            device_id="507f1f77bcf86cd799439011",
            interface="Gi1/0/5",
            eligible=False,
            persist=False,
        )
        self.assertEqual(skipped.skipped_reason, "Interface not eligible")

        with patch.object(RiskScoreEngine, "_load_interface_context", return_value=None):
            restored = engine.calculate(
                device_id="507f1f77bcf86cd799439011",
                interface="Gi1/0/5",
                eligible=True,
                current_stats={
                    "rxBytes": 1000,
                    "txBytes": 1000,
                    "rxPackets": 10,
                    "txPackets": 10,
                    "broadcastPackets": 0,
                    "multicastPackets": 0,
                    "inputErrors": 0,
                    "outputErrors": 0,
                    "utilization": 1.0,
                },
                previous_stats={
                    "rxBytes": 500,
                    "txBytes": 500,
                    "rxPackets": 5,
                    "txPackets": 5,
                    "broadcastPackets": 0,
                    "multicastPackets": 0,
                    "inputErrors": 0,
                    "outputErrors": 0,
                    "utilization": 0.5,
                    "timestamp": None,
                },
                persist=False,
            )
        self.assertTrue(restored.eligible)
        self.assertNotEqual(restored.skipped_reason, "Interface not eligible")

    def test_confirmation_resumes_when_eligible(self):
        engine = ConfirmationEngine(
            config=ConfirmationConfig(
                confirmation_enabled=True,
                required_confirmations=2,
                risk_threshold=50.0,
                reset_on_ineligible=True,
                reset_on_low_risk=True,
                reset_on_poll_failure=False,
            )
        )
        reset = engine.evaluate(
            "507f1f77bcf86cd799439011",
            "Gi1/0/5",
            eligible=False,
            current_risk=90.0,
            risk_rows=[{"riskScore": 90.0, "eligible": False}],
            poll_failed=False,
            persist=False,
        )
        self.assertTrue(reset.reset)
        self.assertEqual(reset.reset_reason, "Interface not eligible")

        confirmed = engine.evaluate(
            "507f1f77bcf86cd799439011",
            "Gi1/0/5",
            eligible=True,
            current_risk=90.0,
            risk_rows=[
                {"riskScore": 90.0, "eligible": True},
                {"riskScore": 88.0, "eligible": True},
            ],
            poll_failed=False,
            previous_confirmation=None,
            persist=False,
        )
        self.assertFalse(confirmed.reset)
        self.assertTrue(confirmed.confirmed)

    def test_eligibility_gate_opens_incident_path(self):
        """Once eligible, storm pipeline gates past RULE_1."""
        result = evaluate(
            {
                "deviceId": "507f1f77bcf86cd799439011",
                "name": "Gi1/0/5",
                "adminStatus": "up",
                "operStatus": "up",
                "isAccess": True,
                "isTrunk": False,
                "isUplink": False,
                "isInfrastructure": False,
                "isManagement": False,
                "isProtected": False,
                "monitoringMode": MONITORING_MODE_AUTO,
                "monitoringEnabled": True,
                "portMode": "access",
            }
        )
        self.assertTrue(result.eligible)
        # Safety/prepare/mitigation only select CONFIRMED+SAFE ports; eligibility
        # being True is the prerequisite that sticky-latch previously blocked.
        self.assertIsNone(result.failed_rule)


class PreferenceHelperTests(unittest.TestCase):
    def test_preference_mirror(self):
        self.assertTrue(preference_enabled_for_mode(MONITORING_MODE_AUTO))
        self.assertFalse(
            preference_enabled_for_mode(MONITORING_MODE_DISABLED_BY_USER)
        )


if __name__ == "__main__":
    unittest.main()
