"""
Unit tests for the Port Eligibility Engine.

Run from the backend directory::

    python -m unittest tests.test_eligibility -v
"""

from __future__ import annotations

import unittest

from services.storm.config import StormConfig
from services.storm.eligibility import EligibilityEngine
from services.storm.exceptions import InvalidInterfaceDataError, MissingInterfaceError


def _access_port(**overrides):
    base = {
        "device_id": "507f1f77bcf86cd799439011",
        "interface": "Gi1/0/5",
        "admin_status": "up",
        "oper_status": "up",
        "is_access": True,
        "is_trunk": False,
        "is_uplink": False,
        "is_infrastructure": False,
        "is_management": False,
        "is_protected": False,
        "monitoring_enabled": True,
        "neighbor": {},
        "port_mode": "access",
    }
    base.update(overrides)
    return base


class EligibilityEngineTests(unittest.TestCase):
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

    def test_access_port_eligible(self):
        result = self.engine.evaluate(_access_port())
        self.assertTrue(result.eligible)
        self.assertEqual(result.reason, "Access Port")
        self.assertIsNone(result.failed_rule)
        self.assertEqual(result.confidence, 100)
        self.assertTrue(result.checks.monitoring)
        self.assertTrue(result.checks.admin)
        self.assertTrue(result.checks.oper)
        self.assertTrue(result.checks.access)
        self.assertTrue(result.checks.trunk)
        self.assertTrue(result.checks.uplink)
        self.assertTrue(result.checks.infrastructure)
        self.assertTrue(result.checks.management)
        self.assertTrue(result.checks.protected)

    def test_trunk_port_not_eligible(self):
        result = self.engine.evaluate(
            _access_port(is_access=False, is_trunk=True, port_mode="trunk")
        )
        self.assertFalse(result.eligible)
        # Fails Rule 4 (access) before Rule 5 (trunk)
        self.assertEqual(result.failed_rule, "RULE_4")
        self.assertEqual(result.reason, "Not an Access Port")

    def test_trunk_flag_with_access_false_path(self):
        """When is_access is true but is_trunk is also true, Rule 5 fails."""
        result = self.engine.evaluate(
            _access_port(is_access=True, is_trunk=True, port_mode="trunk")
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.failed_rule, "RULE_5")
        self.assertEqual(result.reason, "Trunk Port")

    def test_uplink_not_eligible(self):
        result = self.engine.evaluate(_access_port(is_uplink=True))
        self.assertFalse(result.eligible)
        self.assertEqual(result.failed_rule, "RULE_6")
        self.assertEqual(result.reason, "Uplink Port")

    def test_infrastructure_not_eligible(self):
        result = self.engine.evaluate(_access_port(is_infrastructure=True))
        self.assertFalse(result.eligible)
        self.assertEqual(result.failed_rule, "RULE_7")
        self.assertEqual(result.reason, "Infrastructure Port")

    def test_management_not_eligible(self):
        result = self.engine.evaluate(_access_port(is_management=True))
        self.assertFalse(result.eligible)
        self.assertEqual(result.failed_rule, "RULE_8")
        self.assertEqual(result.reason, "Management Port")

    def test_protected_not_eligible(self):
        result = self.engine.evaluate(_access_port(is_protected=True))
        self.assertFalse(result.eligible)
        self.assertEqual(result.failed_rule, "RULE_9")
        self.assertEqual(result.reason, "Protected Port")

    def test_monitoring_disabled(self):
        result = self.engine.evaluate(_access_port(monitoring_enabled=False))
        self.assertFalse(result.eligible)
        self.assertEqual(result.failed_rule, "RULE_1")
        self.assertEqual(result.reason, "Monitoring Disabled")

    def test_admin_down(self):
        result = self.engine.evaluate(_access_port(admin_status="down"))
        self.assertFalse(result.eligible)
        self.assertEqual(result.failed_rule, "RULE_2")
        self.assertEqual(result.reason, "Administrative Down")

    def test_operational_down(self):
        result = self.engine.evaluate(_access_port(oper_status="down"))
        self.assertFalse(result.eligible)
        self.assertEqual(result.failed_rule, "RULE_3")
        self.assertEqual(result.reason, "Operational Down")

    def test_camel_case_mongo_document(self):
        result = self.engine.evaluate({
            "deviceId": "507f1f77bcf86cd799439011",
            "name": "Gi1/0/10",
            "adminStatus": "up",
            "operStatus": "up",
            "isAccess": True,
            "isTrunk": False,
            "isUplink": False,
            "isInfrastructure": False,
            "isManagement": False,
            "isProtected": False,
            "monitoringEnabled": True,
            "portMode": "access",
            "neighbor": {},
        })
        self.assertTrue(result.eligible)
        self.assertEqual(result.interface, "Gi1/0/10")

    def test_allow_trunks_config(self):
        engine = EligibilityEngine(
            config=StormConfig(allow_trunks=True, confidence=100)
        )
        result = engine.evaluate(
            _access_port(is_access=True, is_trunk=True, port_mode="trunk")
        )
        self.assertTrue(result.eligible)

    def test_allow_management_ports_config(self):
        engine = EligibilityEngine(
            config=StormConfig(allow_management_ports=True, confidence=100)
        )
        result = engine.evaluate(_access_port(is_management=True))
        self.assertTrue(result.eligible)

    def test_missing_interface_raises(self):
        with self.assertRaises(MissingInterfaceError):
            self.engine.evaluate(None)  # type: ignore[arg-type]

    def test_invalid_interface_raises(self):
        with self.assertRaises(InvalidInterfaceDataError):
            self.engine.evaluate({"admin_status": "up"})

    def test_eligibility_disabled(self):
        engine = EligibilityEngine(
            config=StormConfig(enable_eligibility=False, confidence=100)
        )
        result = engine.evaluate(_access_port())
        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "Eligibility Disabled")

    def test_to_dict_contract(self):
        result = self.engine.evaluate(_access_port())
        payload = result.to_dict()
        self.assertIn("failed_rule", payload)
        self.assertIn("checks", payload)
        self.assertTrue(payload["eligible"])


if __name__ == "__main__":
    unittest.main()
