from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from bson import ObjectId

from services.interface_collection.ssh_collector import SSHCredentials
from services.switch_hardware.collector import collect_device_hardware
from services.switch_hardware.parsers import parse_platform_outputs
from services.switch_hardware.snmp_collector import SwitchHardwareSNMPCollector
from services.switch_hardware.ssh_collector import collect_ssh_hardware
from services.switch_hardware.vendor_detection import detect_platform_from_show_version


JUNOS_OUTPUTS = {
    "version": """Hostname: ex-sw1
Model: ex4300-48p
Junos: 22.4R3-S2.7
""",
    "inventory": """Hardware inventory:
Item             Version  Part number  Serial number     Description
Chassis                                PD3718110170      EX4300-48P
  FPC 0          REV 18   650-045005   PD3718110170      EX4300-48P
    PIC 0        BUILTIN  BUILTIN       BUILTIN           48x 10/100/1000 Base-T
Power Supply 0   REV 03   740-046051   1EDL7010001      JPSU-350-AC-AFO
""",
    "environment": """Class Item                           Status     Measurement
Power FPC 0 Power Supply 0           OK
      FPC 0 Power Supply 1           Absent
Temp  FPC 0 CPU                      OK         41 degrees C / 105 degrees F
      FPC 0 EX-PFE1                  OK         42 degrees C / 107 degrees F
Fans  FPC 0 Fan 1                    OK         Spinning at normal speed
      FPC 0 Fan 2                    Failed     Stopped
""",
    "routing_engine": """Routing Engine status:
  Slot 0:
    Current state                  Master
    Memory utilization           34 percent
    5 sec CPU utilization:
      User                       8 percent
      Idle                      90 percent
    Uptime                        10 days, 2 hours
    Last reboot reason            power cycle/failure
  Slot 1:
    Current state                  Backup
    Memory utilization           70 percent
    5 sec CPU utilization:
      Idle                      50 percent
""",
    "alarms": """1 alarms currently active
Alarm time               Class  Description
2026-10-14 07:00:00 UTC  Major  Fan Tray 0 Failure
""",
}


class JuniperHardwareTests(unittest.TestCase):
    def test_junos_cli_normalization(self):
        parsed = parse_platform_outputs("juniper_junos", JUNOS_OUTPUTS)
        self.assertEqual(parsed["inventory"]["model"], "ex4300-48p")
        self.assertEqual(parsed["inventory"]["serialNumber"], "PD3718110170")
        self.assertEqual(parsed["inventory"]["firmwareVersion"], "22.4R3-S2.7")
        self.assertEqual(parsed["inventory"]["hostname"], "ex-sw1")
        self.assertEqual(parsed["inventory"]["uptime"], "10 days, 2 hours")
        self.assertEqual(len(parsed["inventory"]["modules"]), 3)
        self.assertEqual(parsed["temperature"]["sensors"][0]["value"], 41.0)
        self.assertEqual(parsed["fans"]["failedCount"], 1)
        self.assertEqual(parsed["powerSupplies"]["count"], 2)
        self.assertEqual(parsed["cpu"]["utilizationPercent"], 10.0)
        self.assertEqual(parsed["memory"]["utilizationPercent"], 34.0)
        self.assertEqual(parsed["alarms"][0]["severity"], "critical")

    def test_empty_junos_sections_do_not_fabricate_values(self):
        parsed = parse_platform_outputs("juniper_junos", {"version": JUNOS_OUTPUTS["version"]})
        self.assertIsNone(parsed["inventory"]["serialNumber"])
        self.assertEqual(parsed["fans"]["items"], [])
        self.assertIsNone(parsed["cpu"]["utilizationPercent"])
        self.assertEqual(parsed["alarms"], [])

    def test_platform_detected_from_version(self):
        self.assertEqual(detect_platform_from_show_version(JUNOS_OUTPUTS["version"]), "juniper_junos")

    @patch("services.switch_hardware.ssh_collector.resolve_ssh_credentials")
    @patch("services.switch_hardware.ssh_collector.SSHInterfaceCollector")
    def test_juniper_vendor_overrides_default_cisco_ssh_session(self, collector_cls, resolve):
        resolve.return_value = SSHCredentials(host="192.0.2.1", username="admin", password="x", vendor="cisco_ios")
        session = collector_cls.return_value.__enter__.return_value
        session.run_command.side_effect = lambda command: {
            "show version": JUNOS_OUTPUTS["version"],
            "show chassis hardware": JUNOS_OUTPUTS["inventory"],
            "show chassis environment": JUNOS_OUTPUTS["environment"],
            "show chassis routing-engine": JUNOS_OUTPUTS["routing_engine"],
            "show chassis alarms": JUNOS_OUTPUTS["alarms"],
        }[command]
        result = collect_ssh_hardware({"vendor": "Juniper", "deviceType": "switch"})
        self.assertEqual(collector_cls.call_args.args[0].vendor, "juniper_junos")
        self.assertEqual(result["parsed"]["inventory"]["serialNumber"], "PD3718110170")

    @patch("services.switch_hardware.ssh_collector.resolve_ssh_credentials")
    @patch("services.switch_hardware.ssh_collector.SSHInterfaceCollector")
    def test_health_only_ssh_is_available_without_version_command(self, collector_cls, resolve):
        resolve.return_value = SSHCredentials(host="192.0.2.1", username="admin", password="x", vendor="juniper_junos")
        session = collector_cls.return_value.__enter__.return_value
        session.run_command.side_effect = lambda command: {
            "show chassis environment": JUNOS_OUTPUTS["environment"],
            "show chassis routing-engine": JUNOS_OUTPUTS["routing_engine"],
            "show chassis alarms": "No alarms currently active",
        }[command]
        result = collect_ssh_hardware(
            {"vendor": "Juniper", "deviceType": "switch"}, include_inventory=False,
        )
        self.assertEqual(result["availability"]["ssh"], "available")
        self.assertIn("alarms", result["observed"])
        self.assertEqual(result["parsed"]["alarms"], [])

    @patch("services.switch_hardware.ssh_collector.resolve_ssh_credentials")
    @patch("services.switch_hardware.ssh_collector.SSHInterfaceCollector")
    def test_rejected_ssh_command_is_not_treated_as_observed(self, collector_cls, resolve):
        resolve.return_value = SSHCredentials(host="192.0.2.1", username="admin", password="x", vendor="juniper_junos")
        session = collector_cls.return_value.__enter__.return_value
        session.run_command.side_effect = lambda command: (
            "syntax error, expecting <command>" if command == "show chassis environment"
            else JUNOS_OUTPUTS["routing_engine"] if command == "show chassis routing-engine"
            else "No alarms currently active"
        )
        result = collect_ssh_hardware(
            {"vendor": "Juniper", "deviceType": "switch"}, include_inventory=False,
        )
        self.assertEqual(result["availability"]["ssh"], "partial")
        self.assertIn("environment", result["errors"])
        self.assertNotIn("environment", result["observed"])

    def test_juniper_snmp_uses_standard_entity_sensor_scaling(self):
        collector = object.__new__(SwitchHardwareSNMPCollector)
        collector.platform = "juniper_junos"
        parsed = collector._normalize({
            "ent_descr": {1: "EX4300 chassis", 2: "CPU temperature"},
            "ent_name": {1: "Chassis", 2: "CPU"},
            "ent_class": {1: "3", 2: "8"},
            "ent_serial": {1: "PD3718110170"},
            "ent_model": {1: "EX4300-48P"},
            "sensor_type": {2: "8"},
            "sensor_scale": {2: "9"},
            "sensor_precision": {2: "1"},
            "sensor_value": {2: "415"},
            "sensor_status": {2: "1"},
        })
        self.assertEqual(parsed["inventory"]["serialNumber"], "PD3718110170")
        self.assertEqual(parsed["temperature"]["sensors"][0]["value"], 41.5)
        self.assertEqual(parsed["temperature"]["sensors"][0]["name"], "CPU")

    def test_juniper_snmp_does_not_walk_cisco_enterprise_oids(self):
        collector = object.__new__(SwitchHardwareSNMPCollector)
        collector.platform = "juniper_junos"
        walked = []

        async def walk(oid, max_rows=500):
            walked.append(oid)
            return {}

        async def scalar(_oid):
            return None

        collector._walk_oid = walk
        collector._get_scalar = scalar
        asyncio.run(collector._walk_tables())
        self.assertNotIn("1.3.6.1.4.1.9.9.13.1.3.1.3", walked)
        self.assertIn("1.3.6.1.2.1.99.1.1.1.2", walked)

    @patch("services.switch_hardware.collector.evaluate_hardware_alerts")
    @patch("services.switch_hardware.collector.detect_hardware_events")
    @patch("services.switch_hardware.collector.collect_ssh_hardware")
    @patch("services.switch_hardware.collector.hw_config.is_ssh_enabled", return_value=True)
    @patch("services.switch_hardware.collector.hw_config.is_snmp_enabled", return_value=False)
    @patch("services.switch_hardware.collector.hw_config.is_hardware_monitoring_enabled", return_value=True)
    @patch("services.switch_hardware.collector.db")
    def test_juniper_snapshot_persists_vendor_and_platform(
        self, db, _enabled, _snmp_enabled, _ssh_enabled, collect_ssh, _events, _alerts
    ):
        device_id = ObjectId()
        db.devices.find_one.return_value = {
            "_id": device_id, "vendor": "Juniper Networks", "deviceType": "Managed Switch", "monitor": True
        }
        db.switch_hardware_current.find_one.return_value = None
        collect_ssh.return_value = {
            "parsed": parse_platform_outputs("juniper_junos", JUNOS_OUTPUTS),
            "platform": "juniper_junos",
            "availability": {"ssh": "available"},
            "errors": {},
        }
        result = collect_device_hardware(device_id, mode="ssh")
        self.assertTrue(result["success"])
        self.assertEqual(result["document"]["vendor"], "Juniper")
        self.assertEqual(result["document"]["platform"], "Junos")
        self.assertEqual(result["document"]["inventory"]["serialNumber"], "PD3718110170")


if __name__ == "__main__":
    unittest.main()
