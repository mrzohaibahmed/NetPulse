"""
Unit tests for Diagnostics Capture + Mitigation Orchestrator.

Run::

    python -m unittest tests.test_diagnostics_orchestrator -v
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from services.storm.diagnostics.collector import capture_diagnostics
from services.storm.diagnostics.snapshots import (
    parse_device_health,
    parse_interface_snapshot,
    parse_mac_table,
    parse_switchport_snapshot,
)
from services.storm.diagnostics.ssh_capture import (
    DiagnosticsSSHError,
    assert_read_only_command,
    build_interface_commands,
)
from services.storm.incident import create_incident_from_diagnostics
from services.storm.mitigation_context import build_mitigation_context
from services.storm.orchestrator import STATUS_BLOCKED, STATUS_READY, prepare


SAMPLE_SHOW_INTERFACE = """
GigabitEthernet1/0/10 is up, line protocol is up
  Hardware is Gigabit Ethernet, address is aabb.cc00.0100
  MTU 1500 bytes, BW 1000000 Kbit
  Full-duplex, 1000Mb/s
  Last input 00:00:01, output 00:00:00
  5 input errors, 2 CRC, 0 frame
  0 output errors, 0 collisions
  3 total output drops
"""

SAMPLE_SWITCHPORT = """
Name: Gi1/0/10
Switchport: Enabled
Administrative Mode: static access
Operational Mode: static access
Access Mode VLAN: 20 (VLAN0020)
Voice VLAN: none
Trunking Native Mode VLAN: 1
Trunking VLANs Enabled: ALL
"""

SAMPLE_MAC = """
          Mac Address Table
-------------------------------------------
Vlan    Mac Address       Type        Ports
----    -----------       --------    -----
  20    aabb.cc00.1111    DYNAMIC     Gi1/0/10
  20    aabb.cc00.2222    DYNAMIC     Gi1/0/10
"""


def _diag_package(**overrides):
    base = {
        "capturedAt": datetime.now(timezone.utc),
        "deviceId": "507f1f77bcf86cd799439011",
        "interface": "Gi1/0/10",
        "hostname": "sw1",
        "ipAddress": "10.0.0.1",
        "interfaceSnapshot": {"available": True, "operStatus": "up"},
        "switchportSnapshot": {"available": True, "mode": "access", "accessVlan": 20},
        "macTable": {"available": True, "macCount": 2, "entries": []},
        "statistics": {
            "broadcastRate": 1200,
            "multicastRate": 50,
            "unknownUnicastRate": 10,
            "utilization": 40,
            "errorRate": 1,
            "crcRate": 0,
            "discardRate": 2,
        },
        "neighbor": {
            "hostname": "ap1",
            "platform": "AP",
            "managementAddress": "10.0.0.50",
            "deviceType": "access-point",
            "protocol": "cdp",
        },
        "deviceHealth": {"available": True, "cpuPercent": 22.0, "memoryPercent": 40.0},
        "eligibility": {"eligible": True},
        "risk": {"riskScore": 96.0, "severity": "CRITICAL"},
        "confirmation": {"confirmed": True, "state": "CONFIRMED"},
        "safety": {"safe": True, "reason": "All safety checks passed", "status": "SAFE"},
        "diagnosticsMeta": {"sshSuccess": True, "sshErrors": {}},
    }
    base.update(overrides)
    return base


class ReadOnlySSHTests(unittest.TestCase):
    def test_allows_show_commands(self):
        cmd = assert_read_only_command("show interfaces Gi1/0/10")
        self.assertTrue(cmd.startswith("show"))

    def test_blocks_shutdown(self):
        with self.assertRaises(DiagnosticsSSHError):
            assert_read_only_command("shutdown")
        with self.assertRaises(DiagnosticsSSHError):
            assert_read_only_command("interface Gi1/0/10\nshutdown")

    def test_blocks_configure(self):
        with self.assertRaises(DiagnosticsSSHError):
            assert_read_only_command("configure terminal")
        with self.assertRaises(DiagnosticsSSHError):
            assert_read_only_command("conf t")

    def test_blocks_write(self):
        with self.assertRaises(DiagnosticsSSHError):
            assert_read_only_command("write memory")

    def test_build_commands_are_show_only(self):
        cmds = build_interface_commands("Gi1/0/10")
        for command in cmds.values():
            assert_read_only_command(command)


class SnapshotParseTests(unittest.TestCase):
    def test_interface_snapshot(self):
        snap = parse_interface_snapshot(SAMPLE_SHOW_INTERFACE, "Gi1/0/10")
        self.assertTrue(snap["available"])
        self.assertEqual(snap["operStatus"], "up")
        self.assertEqual(snap["mtu"], 1500)
        self.assertEqual(snap["crc"], 2)
        self.assertIsNotNone(snap["lastInput"])

    def test_missing_switchport_support(self):
        snap = parse_switchport_snapshot(None, "Gi1/0/10")
        self.assertFalse(snap["available"])
        self.assertFalse(snap["supported"])

    def test_switchport_snapshot(self):
        snap = parse_switchport_snapshot(SAMPLE_SWITCHPORT, "Gi1/0/10")
        self.assertTrue(snap["available"])
        self.assertEqual(snap["accessVlan"], 20)
        self.assertIn("access", snap["mode"])

    def test_missing_mac_table(self):
        mac = parse_mac_table("", "Gi1/0/10")
        self.assertFalse(mac["available"])
        self.assertEqual(mac["macCount"], 0)

    def test_mac_table(self):
        mac = parse_mac_table(SAMPLE_MAC, "Gi1/0/10")
        self.assertEqual(mac["macCount"], 2)
        self.assertEqual(mac["vlans"], [20])

    def test_missing_cpu_data(self):
        health = parse_device_health(None, None, mongo_health={})
        self.assertFalse(health["available"])
        self.assertIsNone(health["cpuPercent"])


class IncidentCreationTests(unittest.TestCase):
    def test_incident_creation_in_memory(self):
        incident = create_incident_from_diagnostics(_diag_package(), persist=False)
        self.assertTrue(str(incident["incidentId"]).startswith("storm-"))
        self.assertEqual(incident["status"], "OPEN")
        self.assertEqual(incident["severity"], "CRITICAL")
        self.assertTrue(incident["trigger"]["confirmation"])
        self.assertTrue(incident["trigger"]["safety"])
        self.assertEqual(incident["trigger"]["risk"], 96.0)

    def test_incident_timeline(self):
        incident = create_incident_from_diagnostics(_diag_package(), persist=False)
        events = [item["event"] for item in incident["timeline"]]
        self.assertIn("Risk Calculated", events)
        self.assertIn("Storm Confirmed", events)
        self.assertIn("Safety Passed", events)
        self.assertIn("Diagnostics Captured", events)
        self.assertIn("Incident Created", events)

    def test_evidence_present(self):
        incident = create_incident_from_diagnostics(_diag_package(), persist=False)
        self.assertTrue(incident["interfaceSnapshot"])
        self.assertTrue(incident["switchportSnapshot"])
        self.assertTrue(incident["macTable"])
        self.assertTrue(incident["statistics"])
        self.assertTrue(incident["neighbor"])
        self.assertTrue(incident["deviceHealth"])


class DiagnosticsCollectionTests(unittest.TestCase):
    @patch("services.storm.diagnostics.collector._latest_stats", return_value=None)
    @patch("services.storm.diagnostics.collector._latest", return_value=None)
    @patch("services.storm.diagnostics.collector._load_interface", return_value=None)
    @patch("services.storm.diagnostics.collector._load_device", return_value=None)
    def test_diagnostics_collection_without_mongo(self, *_mocks):
        package = capture_diagnostics(
            "507f1f77bcf86cd799439011",
            "Gi1/0/10",
            probe_ssh=False,
            ssh_bundle={
                "success": True,
                "outputs": {
                    "interface": SAMPLE_SHOW_INTERFACE,
                    "switchport": SAMPLE_SWITCHPORT,
                    "mac": SAMPLE_MAC,
                    "cpu": None,
                    "uptime": None,
                },
                "errors": {},
                "vendor": "cisco_ios",
            },
        )
        self.assertEqual(package["interface"], "Gi1/0/10")
        self.assertTrue(package["interfaceSnapshot"]["available"])
        self.assertTrue(package["switchportSnapshot"]["available"])
        self.assertEqual(package["macTable"]["macCount"], 2)
        self.assertFalse(package["deviceHealth"]["available"])

    @patch("services.storm.diagnostics.collector._latest_stats", return_value=None)
    @patch("services.storm.diagnostics.collector._latest", return_value=None)
    @patch("services.storm.diagnostics.collector._load_interface", return_value=None)
    @patch("services.storm.diagnostics.collector._load_device", return_value=None)
    def test_soft_fail_missing_sections(self, *_mocks):
        package = capture_diagnostics(
            "507f1f77bcf86cd799439011",
            "Gi1/0/10",
            probe_ssh=False,
            ssh_bundle={
                "success": False,
                "outputs": {},
                "errors": {"interface": "timeout"},
                "vendor": "cisco_ios",
            },
        )
        self.assertFalse(package["interfaceSnapshot"]["available"])
        self.assertFalse(package["switchportSnapshot"]["available"])
        self.assertEqual(package["macTable"]["macCount"], 0)


class OrchestratorPrepareTests(unittest.TestCase):
    def test_prepare_blocked_without_safety(self):
        result = prepare(
            "507f1f77bcf86cd799439011",
            "Gi1/0/10",
            require_safety=True,
            persist=False,
            safety={"safe": False, "reason": "Cooldown active"},
            diagnostics=_diag_package(),
        )
        self.assertFalse(result["ready"])
        self.assertEqual(result["status"], STATUS_BLOCKED)

    def test_prepare_blocked_when_storm_not_confirmed(self):
        now = datetime.now(timezone.utc)
        with patch(
            "services.storm.orchestrator._latest_confirmation",
            return_value={
                "confirmed": False,
                "state": "NOT_CONFIRMED",
                "timestamp": now,
            },
        ), patch(
            "services.storm.orchestrator._latest_risk",
            return_value={"riskScore": 90.0, "timestamp": now},
        ):
            result = prepare(
                "507f1f77bcf86cd799439011",
                "Gi1/0/10",
                require_safety=True,
                persist=False,
                safety={"safe": True, "reason": "ok", "timestamp": now},
                diagnostics=_diag_package(),
            )
        self.assertFalse(result["ready"])
        self.assertIn("not currently confirmed", result["reason"].lower())

    def test_prepare_blocked_when_safety_stale_vs_confirmation(self):
        confirm_ts = datetime.now(timezone.utc)
        stale_safety_ts = confirm_ts.replace(year=confirm_ts.year - 1)
        with patch(
            "services.storm.orchestrator._latest_confirmation",
            return_value={
                "confirmed": True,
                "state": "CONFIRMED",
                "timestamp": confirm_ts,
            },
        ), patch(
            "services.storm.orchestrator._latest_risk",
            return_value={"riskScore": 90.0, "timestamp": confirm_ts},
        ), patch(
            "services.storm.orchestrator.get_settings",
            return_value={"reMitigationThreshold": 75},
        ):
            result = prepare(
                "507f1f77bcf86cd799439011",
                "Gi1/0/10",
                require_safety=True,
                persist=False,
                safety={"safe": True, "reason": "ok", "timestamp": stale_safety_ts},
                diagnostics=_diag_package(),
            )
        self.assertFalse(result["ready"])
        self.assertIn("stale", result["reason"].lower())

    def test_prepare_allows_safety_slightly_before_confirmation_heartbeat(self):
        """Concurrent confirmation heartbeats must not invalidate a fresh SAFE."""
        confirm_ts = datetime.now(timezone.utc)
        safety_ts = confirm_ts - timedelta(seconds=5)
        with patch(
            "services.storm.orchestrator.find_open_incident",
            return_value=None,
        ), patch(
            "services.storm.orchestrator.append_timeline_event",
            return_value=None,
        ), patch(
            "services.storm.orchestrator._latest_confirmation",
            return_value={
                "confirmed": True,
                "state": "CONFIRMED",
                "timestamp": confirm_ts,
            },
        ), patch(
            "services.storm.orchestrator._latest_risk",
            return_value={"riskScore": 96.0, "timestamp": confirm_ts},
        ), patch(
            "services.storm.orchestrator.get_settings",
            return_value={"reMitigationThreshold": 75},
        ):
            result = prepare(
                "507f1f77bcf86cd799439011",
                "Gi1/0/10",
                require_safety=True,
                persist=False,
                probe_ssh=False,
                safety={"safe": True, "reason": "ok", "timestamp": safety_ts},
                diagnostics=_diag_package(),
            )
        self.assertTrue(result["ready"])
        self.assertEqual(result["status"], STATUS_READY)

    def test_prepare_workflow_ready(self):
        now = datetime.now(timezone.utc)
        with patch(
            "services.storm.orchestrator.find_open_incident",
            return_value=None,
        ), patch(
            "services.storm.orchestrator.append_timeline_event",
            return_value=None,
        ), patch(
            "services.storm.orchestrator._latest_confirmation",
            return_value={
                "confirmed": True,
                "state": "CONFIRMED",
                "timestamp": now,
            },
        ), patch(
            "services.storm.orchestrator._latest_risk",
            return_value={"riskScore": 96.0, "timestamp": now},
        ), patch(
            "services.storm.orchestrator.get_settings",
            return_value={"reMitigationThreshold": 75},
        ):
            result = prepare(
                "507f1f77bcf86cd799439011",
                "Gi1/0/10",
                require_safety=True,
                persist=False,
                probe_ssh=False,
                safety={"safe": True, "reason": "ok", "timestamp": now},
                diagnostics=_diag_package(),
            )
        self.assertTrue(result["ready"])
        self.assertEqual(result["status"], STATUS_READY)
        self.assertTrue(result["incidentId"])
        self.assertIn("context", result)
        self.assertTrue(result["context"]["mitigationAllowed"])
        # Orchestrator must not invent config commands
        self.assertEqual(result["context"]["actionsPending"], [])
        notes = result["context"]["notes"].lower()
        self.assertIn("no configuration", notes)

    def test_mitigation_context_has_no_shutdown(self):
        incident = create_incident_from_diagnostics(_diag_package(), persist=False)
        ctx = build_mitigation_context(
            device_id="507f1f77bcf86cd799439011",
            interface="Gi1/0/10",
            incident=incident,
            safety={"safe": True},
        )
        blob = str(ctx).lower()
        self.assertNotIn("shutdown", blob)
        self.assertNotIn("configure", blob)

    def test_mongodb_failure_on_incident_create(self):
        """Persist path soft-fails insert and still returns a document."""
        fake_db = MagicMock()
        fake_coll = MagicMock()
        fake_coll.insert_one.side_effect = RuntimeError("mongo down")
        fake_coll.find_one.return_value = None
        fake_db.__getitem__.return_value = fake_coll
        fake_db.counters.find_one_and_update.side_effect = RuntimeError("mongo down")

        with patch("services.storm.incident._db", return_value=fake_db):
            incident = create_incident_from_diagnostics(
                _diag_package(), persist=True, force_new=True,
            )
        self.assertIn("incidentId", incident)
        self.assertIn("_persistError", incident)


if __name__ == "__main__":
    unittest.main()
