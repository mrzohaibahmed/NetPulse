from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bson import ObjectId

from services.switch_hardware.collector import (
    _has_hardware_payload,
    _merge_hardware,
    _ssh_connection_error,
)
from services.switch_hardware.models import (
    empty_cpu,
    empty_fans,
    empty_inventory,
    empty_memory,
    empty_power_supplies,
    empty_temperature,
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

    def _run_with_mode(self, mode: str):
        """Helper: run collect_device_hardware in a given mode with both
        protocols enabled, and return the (snmp_mock, ssh_mock) call spies."""
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

        with patch("services.switch_hardware.collector.db") as mock_db, patch(
            "services.switch_hardware.collector.hw_config.is_hardware_monitoring_enabled",
            return_value=True,
        ), patch(
            "services.switch_hardware.collector.hw_config.is_snmp_enabled", return_value=True
        ), patch(
            "services.switch_hardware.collector.hw_config.is_ssh_enabled", return_value=True
        ), patch(
            "services.switch_hardware.collector.snmp_available", return_value=True
        ), patch(
            "services.switch_hardware.collector.collect_snmp_hardware"
        ) as mock_snmp, patch(
            "services.switch_hardware.collector.collect_ssh_hardware"
        ) as mock_ssh, patch(
            "services.switch_hardware.collector.detect_hardware_events"
        ), patch("services.switch_hardware.collector.evaluate_hardware_alerts"):
            mock_db.devices.find_one.return_value = device
            mock_db.switch_hardware_current.find_one.return_value = None
            mock_db.switch_hardware_current.update_one.return_value = MagicMock(acknowledged=True)
            mock_db.switch_hardware_history.insert_one.return_value = MagicMock()

            mock_snmp.return_value = {
                "inventory": {"model": "WS-C3850-24T"},
                "temperature": empty_temperature(),
                "fans": empty_fans(),
                "powerSupplies": empty_power_supplies(),
                "availability": {"snmp": "available"},
            }
            mock_ssh.return_value = {
                "parsed": {
                    "inventory": {"model": "WS-C3850-24T"},
                    "temperature": empty_temperature(),
                    "fans": empty_fans(),
                    "powerSupplies": empty_power_supplies(),
                    "cpu": {"utilizationPercent": 10.0},
                    "memory": {"utilizationPercent": 20.0},
                    "alarms": [],
                },
                "outputs": {},
                "errors": {},
                "platform": "cisco_ios",
                "availability": {"ssh": "available"},
            }

            collect_device_hardware(device_id, mode=mode, source_label="test")
            return mock_snmp, mock_ssh

    def test_poll_mode_is_snmp_only(self):
        """The 60s scheduled poll job must not also run SSH (redundant with
        the dedicated 10-minute SSH job)."""
        mock_snmp, mock_ssh = self._run_with_mode("poll")
        self.assertTrue(mock_snmp.called)
        self.assertFalse(mock_ssh.called)

    def test_ssh_mode_is_ssh_only_without_inventory_commands(self):
        mock_snmp, mock_ssh = self._run_with_mode("ssh")
        self.assertFalse(mock_snmp.called)
        self.assertTrue(mock_ssh.called)
        self.assertFalse(mock_ssh.call_args.kwargs.get("include_inventory"))

    def test_inventory_mode_is_ssh_only_with_inventory_commands(self):
        mock_snmp, mock_ssh = self._run_with_mode("inventory")
        self.assertFalse(mock_snmp.called)
        self.assertTrue(mock_ssh.called)
        self.assertTrue(mock_ssh.call_args.kwargs.get("include_inventory"))

    def test_full_mode_still_runs_both_protocols(self):
        """Manual "Collect Now" (mode="full") must be unaffected by the poll-mode change."""
        mock_snmp, mock_ssh = self._run_with_mode("full")
        self.assertTrue(mock_snmp.called)
        self.assertTrue(mock_ssh.called)


class SwitchHardwareProtocolAwarePreservationTests(unittest.TestCase):
    """
    Regression tests for the snapshot-overwrite bug: an SNMP-only (or SSH-only)
    cycle used to blank out fields it doesn't collect (e.g. the 60s SNMP-only
    poll wiping CPU/memory/IOS version every cycle, since only the SSH jobs
    collect those). Fields whose protocol did not run this cycle must now keep
    their value from the previous switch_hardware_current document instead.
    """

    DEVICE = {
        "vendor": "Cisco",
        "deviceType": "Managed Switch",
        "monitor": True,
        "hostname": "sw1",
        "ipAddress": "10.0.0.1",
    }

    def _run(
        self,
        *,
        mode: str,
        previous_doc: dict | None,
        snmp_return: dict | None = None,
        ssh_return: dict | None = None,
    ):
        """Run collect_device_hardware with controlled SNMP/SSH responses and
        a given previous current-document. Returns (result, mock_db)."""
        from services.switch_hardware.collector import collect_device_hardware

        device_id = ObjectId()
        device = {"_id": device_id, **self.DEVICE}

        with patch("services.switch_hardware.collector.db") as mock_db, patch(
            "services.switch_hardware.collector.hw_config.is_hardware_monitoring_enabled",
            return_value=True,
        ), patch(
            "services.switch_hardware.collector.hw_config.is_snmp_enabled", return_value=True
        ), patch(
            "services.switch_hardware.collector.hw_config.is_ssh_enabled", return_value=True
        ), patch(
            "services.switch_hardware.collector.snmp_available", return_value=True
        ), patch(
            "services.switch_hardware.collector.collect_snmp_hardware"
        ) as mock_snmp, patch(
            "services.switch_hardware.collector.collect_ssh_hardware"
        ) as mock_ssh, patch(
            "services.switch_hardware.collector.detect_hardware_events"
        ), patch("services.switch_hardware.collector.evaluate_hardware_alerts"):
            mock_db.devices.find_one.return_value = device
            mock_db.switch_hardware_current.find_one.return_value = previous_doc
            mock_db.switch_hardware_current.update_one.return_value = MagicMock(acknowledged=True)
            mock_db.switch_hardware_history.insert_one.return_value = MagicMock()

            if snmp_return is not None:
                mock_snmp.return_value = snmp_return
            if ssh_return is not None:
                mock_ssh.return_value = ssh_return

            result = collect_device_hardware(device_id, mode=mode, source_label="test")
            return result, mock_db

    def _sensor(self, name="Sensor1", value=40.0, status="healthy"):
        return {
            "name": name,
            "sensorType": "temperature",
            "value": value,
            "unit": "C",
            "status": status,
            "warningThreshold": None,
            "criticalThreshold": None,
            "thresholdSource": None,
            "source": "snmp",
            "available": True,
        }

    # 1 & 3. SNMP-only success preserves SSH-owned CPU/memory and SSH availability.
    def test_snmp_only_preserves_ssh_cpu_memory_and_availability(self):
        previous_doc = {
            "cpu": {"utilizationPercent": 12.0, "status": "healthy"},
            "memory": {"utilizationPercent": 30.0, "status": "healthy"},
            "inventory": empty_inventory(),
            "temperature": empty_temperature(),
            "fans": empty_fans(),
            "powerSupplies": empty_power_supplies(),
            "hardwareAlarms": [],
            "availability": {"snmp": "unavailable", "ssh": "available"},
        }
        snmp_return = {
            "inventory": {},
            "temperature": {"sensors": [self._sensor()]},
            "fans": empty_fans(),
            "powerSupplies": empty_power_supplies(),
            "availability": {"snmp": "available"},
        }

        result, _ = self._run(mode="poll", previous_doc=previous_doc, snmp_return=snmp_return)
        doc = result["document"]

        self.assertEqual(doc["collectionStatus"], "success")
        # SSH-owned fields, not attempted this cycle: preserved.
        self.assertEqual(doc["cpu"]["utilizationPercent"], 12.0)
        self.assertEqual(doc["memory"]["utilizationPercent"], 30.0)
        self.assertEqual(doc["availability"]["ssh"], "available")
        # SNMP-owned fields: fresh this cycle.
        self.assertEqual(doc["temperature"]["sensors"][0]["name"], "Sensor1")
        self.assertEqual(doc["availability"]["snmp"], "available")

    # 2. SNMP-only success preserves IOS version, hostname, uptime, boot reason.
    def test_snmp_only_preserves_ssh_inventory_fields(self):
        previous_doc = {
            "cpu": empty_cpu(),
            "memory": empty_memory(),
            "inventory": {
                "model": "WS-C3850-24T",
                "serialNumber": "FDO123",
                "productId": "WS-C3850-24T",
                "firmwareVersion": "15.2",
                "iosVersion": "15.2",
                "hostname": "sw1",
                "uptime": "10 weeks, 2 days",
                "bootReason": "Reload Command",
                "chassis": [],
                "modules": [],
            },
            "temperature": empty_temperature(),
            "fans": empty_fans(),
            "powerSupplies": empty_power_supplies(),
            "hardwareAlarms": [],
            "availability": {"snmp": "available", "ssh": "available"},
        }
        snmp_return = {
            "inventory": {},  # SNMP this cycle has nothing new for inventory
            "temperature": {"sensors": [self._sensor()]},
            "fans": empty_fans(),
            "powerSupplies": empty_power_supplies(),
            "availability": {"snmp": "available"},
        }

        result, _ = self._run(mode="poll", previous_doc=previous_doc, snmp_return=snmp_return)
        inv = result["document"]["inventory"]

        self.assertEqual(inv["iosVersion"], "15.2")
        self.assertEqual(inv["hostname"], "sw1")
        self.assertEqual(inv["uptime"], "10 weeks, 2 days")
        self.assertEqual(inv["bootReason"], "Reload Command")
        # SSH/SNMP-shared fields also untouched this cycle: preserved too.
        self.assertEqual(inv["model"], "WS-C3850-24T")
        self.assertEqual(inv["serialNumber"], "FDO123")

    # 4. SSH-only success preserves SNMP fields (temp/fans/PSU) when SSH doesn't provide them.
    def test_ssh_only_preserves_snmp_fields_when_ssh_has_none(self):
        previous_doc = {
            "cpu": empty_cpu(),
            "memory": empty_memory(),
            "inventory": empty_inventory(),
            "temperature": {"status": "healthy", "sensors": [self._sensor()]},
            "fans": {
                "count": 1,
                "healthyCount": 1,
                "failedCount": 0,
                "items": [{"name": "Fan1", "status": "healthy", "speedRpm": 3000, "source": "snmp", "available": True}],
            },
            "powerSupplies": {
                "count": 1,
                "healthyCount": 1,
                "failedCount": 0,
                "redundancy": None,
                "items": [{"name": "PSU1", "status": "healthy", "inputStatus": None, "outputStatus": None, "source": "snmp", "available": True}],
            },
            "hardwareAlarms": [],
            "availability": {"snmp": "available", "ssh": "available"},
        }
        ssh_return = {
            "parsed": {
                "inventory": {},
                "temperature": {"sensors": []},
                "fans": {"items": []},
                "powerSupplies": {"items": []},
                "cpu": {"utilizationPercent": 15.0},
                "memory": {"utilizationPercent": 42.0},
                "alarms": [],
            },
            "outputs": {},
            "errors": {},
            "platform": "cisco_ios",
            "availability": {"ssh": "available"},
        }

        result, _ = self._run(mode="ssh", previous_doc=previous_doc, ssh_return=ssh_return)
        doc = result["document"]

        self.assertEqual(doc["collectionStatus"], "success")
        # SNMP-owned data, not attempted this cycle: preserved.
        self.assertEqual(doc["temperature"]["sensors"][0]["name"], "Sensor1")
        self.assertEqual(doc["fans"]["items"][0]["name"], "Fan1")
        self.assertEqual(doc["powerSupplies"]["items"][0]["name"], "PSU1")
        self.assertEqual(doc["availability"]["snmp"], "available")
        # SSH-owned fields: fresh this cycle.
        self.assertEqual(doc["cpu"]["utilizationPercent"], 15.0)
        self.assertEqual(doc["memory"]["utilizationPercent"], 42.0)

    # 5. Inventory cycle updates inventory fields without clearing unrelated values.
    def test_inventory_cycle_updates_inventory_without_clearing_unrelated(self):
        previous_doc = {
            "cpu": {"utilizationPercent": 9.0, "status": "healthy"},
            "memory": {"utilizationPercent": 22.0, "status": "healthy"},
            "inventory": empty_inventory(),
            "temperature": {"status": "healthy", "sensors": [self._sensor()]},
            "fans": empty_fans(),
            "powerSupplies": empty_power_supplies(),
            "hardwareAlarms": [],
            "availability": {"snmp": "available", "ssh": "available"},
        }
        ssh_return = {
            "parsed": {
                "inventory": {
                    "model": "WS-C3850-24T",
                    "serialNumber": "FDO456",
                    "productId": "WS-C3850-24T",
                    "iosVersion": "16.9",
                    "firmwareVersion": "16.9",
                    "hostname": "sw1",
                    "uptime": "1 week",
                    "bootReason": "power-on",
                },
                "temperature": {"sensors": []},
                "fans": {"items": []},
                "powerSupplies": {"items": []},
                "cpu": {"utilizationPercent": 11.0},
                "memory": {"utilizationPercent": 25.0},
                "alarms": [],
            },
            "outputs": {},
            "errors": {},
            "platform": "cisco_ios",
            "availability": {"ssh": "available"},
        }

        result, _ = self._run(mode="inventory", previous_doc=previous_doc, ssh_return=ssh_return)
        doc = result["document"]

        # Inventory job's own fields: fresh this cycle.
        self.assertEqual(doc["inventory"]["model"], "WS-C3850-24T")
        self.assertEqual(doc["inventory"]["iosVersion"], "16.9")
        self.assertEqual(doc["cpu"]["utilizationPercent"], 11.0)
        # Temperature: SNMP not attempted on the inventory cycle — preserved.
        self.assertEqual(doc["temperature"]["sensors"][0]["name"], "Sensor1")
        self.assertEqual(doc["availability"]["snmp"], "available")

    # 6. A genuinely failed cycle follows existing preservation behavior:
    #    status stays "failed" (unchanged semantics) and data is preserved.
    def test_fully_failed_cycle_preserves_previous_snapshot(self):
        previous_doc = {
            "cpu": {"utilizationPercent": 12.0, "status": "healthy"},
            "memory": {"utilizationPercent": 30.0, "status": "healthy"},
            "inventory": {
                "model": "WS-C3850-24T",
                "serialNumber": "FDO123",
                "productId": None,
                "firmwareVersion": "15.2",
                "iosVersion": "15.2",
                "hostname": "sw1",
                "uptime": "1 day",
                "bootReason": "reload",
                "chassis": [],
                "modules": [],
            },
            "temperature": {"status": "healthy", "sensors": [self._sensor()]},
            "fans": empty_fans(),
            "powerSupplies": empty_power_supplies(),
            "hardwareAlarms": [],
            "overallHealth": "healthy",
            "availability": {"snmp": "available", "ssh": "available"},
        }
        ssh_return = {
            "parsed": {},
            "outputs": {},
            "errors": {"connection": "SSH authentication failed for 10.0.0.1"},
            "platform": None,
            "availability": {"ssh": "unavailable"},
        }

        result, mock_db = self._run(mode="ssh", previous_doc=previous_doc, ssh_return=ssh_return)
        doc = result["document"]

        self.assertFalse(result["success"])
        # collection_status semantics are unchanged: SSH was the only protocol
        # attempted this cycle and it produced nothing new, so this cycle
        # itself is "failed" — even though the document still displays the
        # last known-good data.
        self.assertEqual(doc["collectionStatus"], "failed")
        self.assertIsNotNone(doc["lastError"])
        self.assertEqual(doc["cpu"]["utilizationPercent"], 12.0)
        self.assertEqual(doc["inventory"]["model"], "WS-C3850-24T")
        self.assertEqual(doc["temperature"]["sensors"][0]["name"], "Sensor1")
        self.assertEqual(doc["overallHealth"], "healthy")
        # Failed cycles never append to history (existing behavior, unchanged).
        mock_db.switch_hardware_history.insert_one.assert_not_called()

    # 7. A successful cycle with missing optional fields (e.g. no PSU sensors
    #    reported this time) does not erase other valid previous data.
    def test_success_with_missing_optional_field_does_not_erase_other_data(self):
        previous_doc = {
            "cpu": empty_cpu(),
            "memory": empty_memory(),
            "inventory": empty_inventory(),
            "temperature": {"status": "healthy", "sensors": [self._sensor()]},
            "fans": empty_fans(),
            "powerSupplies": {
                "count": 1,
                "healthyCount": 1,
                "failedCount": 0,
                "redundancy": None,
                "items": [{"name": "PSU1", "status": "healthy", "inputStatus": None, "outputStatus": None, "source": "snmp", "available": True}],
            },
            "hardwareAlarms": [],
            "availability": {"snmp": "available", "ssh": "unknown"},
        }
        # This cycle's SNMP walk reports temperature again but nothing for PSUs
        # (e.g. transient gap in ENTITY-MIB rows for that OID this poll).
        snmp_return = {
            "inventory": {},
            "temperature": {"sensors": [self._sensor(value=41.0)]},
            "fans": empty_fans(),
            "powerSupplies": empty_power_supplies(),
            "availability": {"snmp": "available"},
        }

        result, _ = self._run(mode="poll", previous_doc=previous_doc, snmp_return=snmp_return)
        doc = result["document"]

        self.assertEqual(doc["collectionStatus"], "success")
        self.assertEqual(doc["temperature"]["sensors"][0]["value"], 41.0)
        # PSU wasn't in this cycle's SNMP response: previous PSU data survives.
        self.assertEqual(doc["powerSupplies"]["items"][0]["name"], "PSU1")

    # 8. History receives the same merged (preserved + fresh) snapshot as current.
    def test_history_receives_merged_snapshot(self):
        previous_doc = {
            "cpu": {"utilizationPercent": 12.0, "status": "healthy"},
            "memory": {"utilizationPercent": 30.0, "status": "healthy"},
            "inventory": empty_inventory(),
            "temperature": empty_temperature(),
            "fans": empty_fans(),
            "powerSupplies": empty_power_supplies(),
            "hardwareAlarms": [],
            "availability": {"snmp": "available", "ssh": "available"},
        }
        snmp_return = {
            "inventory": {},
            "temperature": {"sensors": [self._sensor()]},
            "fans": empty_fans(),
            "powerSupplies": empty_power_supplies(),
            "availability": {"snmp": "available"},
        }

        result, mock_db = self._run(mode="poll", previous_doc=previous_doc, snmp_return=snmp_return)

        mock_db.switch_hardware_history.insert_one.assert_called_once()
        history_doc = mock_db.switch_hardware_history.insert_one.call_args[0][0]
        self.assertEqual(history_doc["cpu"]["utilizationPercent"], 12.0)
        self.assertEqual(history_doc["memory"]["utilizationPercent"], 30.0)
        self.assertEqual(history_doc["temperature"]["sensors"][0]["name"], "Sensor1")
        self.assertEqual(history_doc["collectionStatus"], result["document"]["collectionStatus"])


if __name__ == "__main__":
    unittest.main()
