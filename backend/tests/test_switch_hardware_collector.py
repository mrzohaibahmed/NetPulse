from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bson import ObjectId

from services.switch_hardware.collector import (
    _has_hardware_payload,
    _merge_hardware,
    _ssh_connection_error,
)
from services.switch_hardware.models import empty_fans, empty_power_supplies, empty_temperature


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

    @patch("services.switch_hardware.collector.evaluate_hardware_alerts")
    @patch("services.switch_hardware.collector.detect_hardware_events")
    @patch("services.switch_hardware.collector.collect_ssh_hardware")
    @patch("services.switch_hardware.collector.hw_config.is_ssh_enabled", return_value=True)
    @patch("services.switch_hardware.collector.hw_config.is_snmp_enabled", return_value=False)
    @patch("services.switch_hardware.collector.hw_config.is_hardware_monitoring_enabled", return_value=True)
    @patch("services.switch_hardware.collector.db")
    def test_collect_device_hardware_builds_document_without_raising(
        self,
        mock_db,
        _mock_enabled,
        _mock_snmp_enabled,
        _mock_ssh_enabled,
        mock_collect_ssh,
        _mock_events,
        _mock_alerts,
    ):
        """
        Regression test for the build_current_document() keyword-argument mismatch
        (``powerSupplies`` vs the model's ``power_supplies``) that made every real
        collection raise TypeError before a document could ever be persisted.
        """
        from services.switch_hardware.collector import collect_device_hardware

        device_id = ObjectId()
        device = {
            "_id": device_id,
            "vendor": "Cisco",
            "deviceType": "Managed Switch",
            "monitor": True,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
        }
        mock_db.devices.find_one.return_value = device
        mock_db.switch_hardware_current.find_one.return_value = None
        mock_db.switch_hardware_current.update_one.return_value = MagicMock(acknowledged=True)
        mock_db.switch_hardware_history.insert_one.return_value = MagicMock()

        mock_collect_ssh.return_value = {
            "parsed": {
                "inventory": {
                    "model": "WS-C3850-24T",
                    "serialNumber": "FDO123",
                    "hostname": "sw1",
                },
                "temperature": empty_temperature(),
                "fans": empty_fans(),
                "powerSupplies": empty_power_supplies(),
                "cpu": {"utilizationPercent": 12.0},
                "memory": {"utilizationPercent": 30.0},
                "alarms": [],
            },
            "outputs": {},
            "errors": {},
            "platform": "cisco_ios",
            "availability": {"ssh": "available"},
        }

        result = collect_device_hardware(device_id, mode="full", source_label="test")

        self.assertTrue(result["success"])
        self.assertEqual(result["document"]["collectionStatus"], "success")
        self.assertEqual(result["document"]["inventory"]["model"], "WS-C3850-24T")
        mock_db.switch_hardware_current.update_one.assert_called_once()
        set_doc = mock_db.switch_hardware_current.update_one.call_args[0][1]["$set"]
        self.assertEqual(set_doc["inventory"]["model"], "WS-C3850-24T")

    @patch("services.switch_hardware.collector.evaluate_hardware_alerts")
    @patch("services.switch_hardware.collector.detect_hardware_events")
    @patch("services.switch_hardware.collector.collect_ssh_hardware")
    @patch("services.switch_hardware.collector.hw_config.is_ssh_enabled", return_value=True)
    @patch("services.switch_hardware.collector.hw_config.is_snmp_enabled", return_value=False)
    @patch("services.switch_hardware.collector.hw_config.is_hardware_monitoring_enabled", return_value=True)
    @patch("services.switch_hardware.collector.db")
    def test_failed_collection_preserves_last_known_good_hardware(
        self,
        mock_db,
        _mock_enabled,
        _mock_snmp_enabled,
        _mock_ssh_enabled,
        mock_collect_ssh,
        _mock_events,
        _mock_alerts,
    ):
        """A failed cycle must not blank out previously persisted component data."""
        from services.switch_hardware.collector import collect_device_hardware

        device_id = ObjectId()
        device = {
            "_id": device_id,
            "vendor": "Cisco",
            "deviceType": "Managed Switch",
            "monitor": True,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
        }
        previous_doc = {
            "deviceId": device_id,
            "inventory": {"model": "WS-C3850-24T", "serialNumber": "FDO123"},
            "temperature": {"status": "healthy", "sensors": []},
            "fans": empty_fans(),
            "powerSupplies": empty_power_supplies(),
            "cpu": {"utilizationPercent": 12.0, "status": "healthy"},
            "memory": {"utilizationPercent": 30.0, "status": "healthy"},
            "hardwareAlarms": [],
            "overallHealth": "healthy",
            "lastSuccessfulCollectionAt": "2026-09-01T00:00:00Z",
        }
        mock_db.devices.find_one.return_value = device
        mock_db.switch_hardware_current.find_one.return_value = previous_doc
        mock_db.switch_hardware_current.update_one.return_value = MagicMock(acknowledged=True)

        # This cycle's SSH attempt fails outright with no parsed data.
        mock_collect_ssh.return_value = {
            "parsed": {},
            "outputs": {},
            "errors": {"connection": "SSH authentication failed for 10.0.0.1"},
            "platform": None,
            "availability": {"ssh": "unavailable"},
        }

        result = collect_device_hardware(device_id, mode="full", source_label="test")

        self.assertFalse(result["success"])
        self.assertEqual(result["document"]["collectionStatus"], "failed")
        # Component data from the previous good snapshot must survive the failed cycle.
        self.assertEqual(result["document"]["inventory"]["model"], "WS-C3850-24T")
        self.assertEqual(result["document"]["cpu"]["utilizationPercent"], 12.0)
        self.assertEqual(result["document"]["overallHealth"], "healthy")
        # But the failure itself must still be visible.
        self.assertIsNotNone(result["document"]["lastError"])


if __name__ == "__main__":
    unittest.main()
