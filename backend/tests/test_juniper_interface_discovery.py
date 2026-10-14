from __future__ import annotations

import unittest
from unittest.mock import patch

from services.interface_collection.normalizer import normalize_raw_interface
from services.interface_collection.parser import parse_interface_outputs, parse_juniper_terse
from services.interface_collection.ssh_collector import get_command_set, resolve_ssh_credentials


TERSE = """Interface               Admin Link Proto    Local
ge-0/0/1                up    up
ge-0/0/1.0              up    up   eth-switch
ge-0/0/2                up    down
ge-0/0/2.0              up    down eth-switch
xe-0/0/0                up    up
ae0                     up    up
ae0.0                   up    up   eth-switch
me0                     up    up
me0.0                   up    up   inet 192.0.2.2/24
"""


class JuniperInterfaceDiscoveryTests(unittest.TestCase):
    def test_command_set_runs_terse_once_and_collects_optional_details(self):
        commands = get_command_set("juniper_junos")
        self.assertEqual(list(commands.values()).count("show interfaces terse"), 1)
        self.assertEqual(commands["lldp"], "show lldp neighbors detail")
        self.assertIn("show interfaces media", commands.values())

    def test_vendor_inferred_from_device_unless_ssh_vendor_explicit(self):
        device = {"ipAddress": "192.0.2.1", "vendor": "Juniper Networks"}
        with patch.dict("os.environ", {
            "SSH_DEFAULT_USERNAME": "test", "SSH_DEFAULT_PASSWORD": "test",
            "SSH_DEFAULT_VENDOR": "cisco_ios",
        }):
            self.assertEqual(resolve_ssh_credentials(device).vendor, "juniper_junos")
            device["credentials"] = {"sshVendor": "cisco_xe"}
            self.assertEqual(resolve_ssh_credentials(device).vendor, "cisco_xe")

    def test_ex_detail_description_media_lldp_and_vlan_ids(self):
        outputs = {
            "status": TERSE,
            "description": "Interface Admin Link Description\nge-0/0/1 up up Floor 1 user\nge-0/0/2 up down Core uplink\n",
            "switching": """Interface: ge-0/0/1.0, Index: 65, State: up, Port mode: Access
VLAN membership:
    users, 802.1Q Tag: 120, untagged, unblocked
Interface: ge-0/0/2.0, Index: 66, State: down, Interface mode: Trunk
VLAN membership:
    users, 802.1Q Tag: 120, tagged, unblocked
    voice, 802.1Q Tag: 130, tagged, unblocked
""",
            "interface_config": "set interfaces ge-0/0/2 native-vlan-id 120\n",
            "lldp": """LLDP Neighbor Information:
Local Information:
Local Interface    : ge-0/0/2.0
Neighbour Information:
Port ID            : 880
Port description   : xe-0/0/0
System name        : core-1
System Description : Juniper EX switch
System capabilities
    Supported: Bridge Router
    Enabled: Bridge Router
Management address
    Address: 192.0.2.10
""",
            "media": """Physical interface: ge-0/0/1, Enabled, Physical link is Up
  Interface index: 10, SNMP ifIndex: 501
  Link-level type: Ethernet, MTU: 1514, Speed: 1Gbps
  Current address: 00:11:22:33:44:55, Hardware address: 00:11:22:33:44:55
Physical interface: ge-0/0/2, Enabled, Physical link is Down
  Link-level type: Ethernet, Speed: 10Gbps
""",
        }
        rows = {row["name"]: normalize_raw_interface(row) for row in parse_interface_outputs(outputs, "juniper_junos")}
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows["ge-0/0/1"]["description"], "Floor 1 user")
        self.assertEqual(rows["ge-0/0/1"]["portMode"], "access")
        self.assertEqual(rows["ge-0/0/1"]["accessVlan"], 120)
        self.assertEqual(rows["ge-0/0/1"]["speedMbps"], 1000)
        self.assertEqual(rows["ge-0/0/1"]["ifIndex"], 501)
        self.assertEqual(rows["ge-0/0/1"]["macAddress"], "00:11:22:33:44:55")
        self.assertEqual(rows["ge-0/0/2"]["portMode"], "trunk")
        self.assertEqual(rows["ge-0/0/2"]["allowedVlans"], [120, 130])
        self.assertEqual(rows["ge-0/0/2"]["nativeVlan"], 120)
        self.assertEqual(rows["ge-0/0/2"]["neighbor"]["hostname"], "core-1")
        self.assertEqual(rows["ge-0/0/2"]["neighbor"]["interface"], "xe-0/0/0")
        self.assertEqual(rows["ge-0/0/2"]["neighbor"]["managementAddress"], "192.0.2.10")
        self.assertEqual(rows["me0"]["portMode"], "unknown")

    def test_els_config_resolves_vlan_names_and_routed_port(self):
        outputs = {
            "status": TERSE,
            "vlan_config": "set vlans staff vlan-id 42\nset vlans guest vlan-id 77\n",
            "interface_config": """set interfaces ge-0/0/1 unit 0 family ethernet-switching interface-mode access
set interfaces ge-0/0/1 unit 0 family ethernet-switching vlan members staff
set interfaces ge-0/0/2 unit 0 family ethernet-switching interface-mode trunk
set interfaces ge-0/0/2 unit 0 family ethernet-switching vlan members [ staff guest ]
set interfaces xe-0/0/0 unit 0 family inet address 192.0.2.3/31
""",
        }
        rows = {row["name"]: normalize_raw_interface(row) for row in parse_interface_outputs(outputs, "junos")}
        self.assertEqual(rows["ge-0/0/1"]["accessVlan"], 42)
        self.assertEqual(rows["ge-0/0/2"]["allowedVlans"], [42, 77])
        self.assertEqual(rows["xe-0/0/0"]["portMode"], "routed")

    def test_missing_optional_outputs_leave_unknowns_and_skip_logical_units(self):
        rows = parse_juniper_terse(TERSE)
        self.assertEqual(len(rows), 5)
        self.assertFalse(any("." in row["name"] for row in rows))
        normalized = normalize_raw_interface(parse_interface_outputs({"status": TERSE}, "juniper")[0])
        self.assertEqual(normalized["portMode"], "unknown")
        self.assertIsNone(normalized["neighbor"])

    def test_qfx_vlan_names_without_explicit_mode_do_not_guess_access(self):
        rows = {row["name"]: normalize_raw_interface(row) for row in parse_interface_outputs({
            "status": TERSE,
            "switching": "Interface: ge-0/0/1.0 Index: 65\n  State: up\n  Vlans: staff(untagged)\n",
            "vlan_config": "set vlans staff vlan-id 42\n",
        }, "juniper_junos")}
        self.assertEqual(rows["ge-0/0/1"]["portMode"], "unknown")
        self.assertIsNone(rows["ge-0/0/1"]["accessVlan"])


if __name__ == "__main__":
    unittest.main()
