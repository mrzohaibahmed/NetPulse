from __future__ import annotations

import unittest
from unittest.mock import patch

from bson import ObjectId

from services.switch_hardware.collector import (
    _has_hardware_payload,
    _merge_hardware,
    _ssh_connection_error,
)


class SwitchHardwareCollectorTests(unittest.TestCase):
    def test_merge_snmp_and_ssh(self):
        snmp = {
            "inventory": {"serialNumber": "SNMP123"},
            "temperature": {"sensors": [{"name": "T1", "value": 40, "unit": "C", "status": "healthy"}]},
            "fans": {"items": []},
            "powerSupplies": {"items": []},
            "availability": {"snmp": "available"},
        }
        ssh = {
            "parsed": {
                "inventory": {"iosVersion": "15.2"},
                "cpu": {"utilizationPercent": 10.0},
                "memory": {},
                "temperature": {"sensors": []},
                "fans": {"items": []},
                "powerSupplies": {"items": []},
                "alarms": [],
            },
            "availability": {"ssh": "available"},
        }
        merged = _merge_hardware(snmp, ssh)
        self.assertEqual(merged["inventory"]["serialNumber"], "SNMP123")
        self.assertEqual(merged["inventory"]["iosVersion"], "15.2")
        self.assertEqual(merged["cpu"]["utilizationPercent"], 10.0)

    def test_ssh_connection_error_from_soft_fail(self):
        ssh = {
            "parsed": {},
            "errors": {"connection": "SSH authentication failed for 10.0.0.1: auth"},
            "availability": {"ssh": "unavailable"},
        }
        self.assertIn("authentication failed", _ssh_connection_error(ssh) or "")

    def test_empty_snapshot_has_no_payload(self):
        merged = _merge_hardware(None, {"parsed": {}, "availability": {"ssh": "unavailable"}})
        self.assertFalse(_has_hardware_payload(merged))

    @patch("services.switch_hardware.collector.hw_config.is_hardware_monitoring_enabled", return_value=False)
    def test_disabled_returns_early(self, _mock):
        from services.switch_hardware.collector import collect_device_hardware

        result = collect_device_hardware(ObjectId())
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "disabled")
        self.assertTrue(result["errors"])


if __name__ == "__main__":
    unittest.main()
