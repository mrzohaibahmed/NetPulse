"""Unit tests for services.interface_collection.mac_arp_parser."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.interface_collection.mac_arp_parser import (
    parse_cisco_connected_routes,
    parse_cisco_ip_arp,
    parse_cisco_mac_address_table,
)


def test_parse_mac_address_table_single_mac_per_port():
    output = """
          Mac Address Table
-------------------------------------------

Vlan    Mac Address       Type        Ports
----    -----------       --------    -----
  10    0011.2233.4455    DYNAMIC     Gi1/0/1
  20    aabb.ccdd.eeff    DYNAMIC     Gi1/0/2
Total Mac Addresses for this criterion: 2
"""
    result = parse_cisco_mac_address_table(output)
    assert result["gi1/0/1"] == ["0011.2233.4455"]
    assert result["gi1/0/2"] == ["aabb.ccdd.eeff"]


def test_parse_mac_address_table_groups_multiple_macs_on_uplink():
    output = """
Vlan    Mac Address       Type        Ports
----    -----------       --------    -----
  10    0011.2233.4455    DYNAMIC     Gi1/0/24
  10    aabb.ccdd.eeff    DYNAMIC     Gi1/0/24
  20    1122.3344.5566    DYNAMIC     Gi1/0/24
"""
    result = parse_cisco_mac_address_table(output)
    assert set(result["gi1/0/24"]) == {
        "0011.2233.4455", "aabb.ccdd.eeff", "1122.3344.5566",
    }


def test_parse_mac_address_table_skips_cpu_and_router_entries():
    output = """
Vlan    Mac Address       Type        Ports
----    -----------       --------    -----
   1    0011.2233.0001    STATIC      CPU
  10    0011.2233.4455    DYNAMIC     Gi1/0/1
"""
    result = parse_cisco_mac_address_table(output)
    assert "cpu" not in result
    assert result == {"gi1/0/1": ["0011.2233.4455"]}


def test_parse_mac_address_table_empty_output():
    assert parse_cisco_mac_address_table("") == {}
    assert parse_cisco_mac_address_table(None) == {}


def test_parse_ip_arp_extracts_ip_and_mac():
    output = """
Protocol  Address          Age (min)  Hardware Addr   Type   Interface
Internet  192.168.10.1            -   aabb.cc00.0100  ARPA   Vlan10
Internet  192.168.10.55           5   0011.2233.4455  ARPA   Vlan10
"""
    entries = parse_cisco_ip_arp(output)
    assert len(entries) == 2
    assert entries[0] == {"ip": "192.168.10.1", "mac": "aabb.cc00.0100", "interface": "Vlan10"}
    assert entries[1]["ip"] == "192.168.10.55"


def test_parse_ip_arp_skips_incomplete_entries():
    output = """
Protocol  Address          Age (min)  Hardware Addr   Type   Interface
Internet  192.168.10.77           0   Incomplete      ARPA
Internet  192.168.10.55           5   0011.2233.4455  ARPA   Vlan10
"""
    entries = parse_cisco_ip_arp(output)
    assert len(entries) == 1
    assert entries[0]["ip"] == "192.168.10.55"


def test_parse_connected_routes_extracts_subnets():
    output = """
Codes: L - local, C - connected, S - static

C    192.168.10.0/24 is directly connected, Vlan10
L    192.168.10.1/32 is directly connected, Vlan10
C    10.0.0.0/30 is directly connected, GigabitEthernet0/1
"""
    routes = parse_cisco_connected_routes(output)
    assert routes == [
        {"network": "192.168.10.0", "prefixLength": 24, "interface": "Vlan10"},
        {"network": "10.0.0.0", "prefixLength": 30, "interface": "GigabitEthernet0/1"},
    ]


def test_parse_connected_routes_excludes_local_host_routes():
    output = "L    192.168.10.1/32 is directly connected, Vlan10\n"
    assert parse_cisco_connected_routes(output) == []


def test_parse_connected_routes_empty_output():
    assert parse_cisco_connected_routes("") == []
