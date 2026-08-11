"""
Unit tests for services.interface_collection.port_resolution.

Focus: the port -> connected-device-IP decision function must never guess.
The two cases the feature spec calls out explicitly as required-blank are
tested directly (and pinned so a future change can't silently regress
them): a down port with no learned MAC, and a multi-MAC uplink to a device
this system doesn't recognize.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.interface_collection.port_resolution import (
    RESOLUTION_VIA_ARP,
    RESOLUTION_VIA_NEIGHBOR,
    normalize_mac,
    resolve_port_device_ip,
)


# ---------------------------------------------------------------------------
# normalize_mac
# ---------------------------------------------------------------------------

def test_normalize_mac_accepts_cisco_dotted_quad():
    assert normalize_mac("0011.2233.4455") == "001122334455"


def test_normalize_mac_accepts_colon_and_hyphen_forms():
    assert normalize_mac("00:11:22:33:44:55") == "001122334455"
    assert normalize_mac("00-11-22-33-44-55") == "001122334455"


def test_normalize_mac_is_case_insensitive():
    assert normalize_mac("AA:BB:CC:DD:EE:FF") == normalize_mac("aa:bb:cc:dd:ee:ff")


def test_normalize_mac_rejects_malformed_input():
    assert normalize_mac("") is None
    assert normalize_mac(None) is None
    assert normalize_mac("not-a-mac") is None
    assert normalize_mac("00:11:22:33:44") is None  # too short


# ---------------------------------------------------------------------------
# Required-blank cases — pinned so they can never silently regress.
# ---------------------------------------------------------------------------

def test_down_port_with_zero_learned_macs_shows_nothing():
    """Nothing plugged in / link down: zero MACs -> unresolved, no guess."""
    result = resolve_port_device_ip(
        mac_addresses=[],
        arp_cache={
            "001122334455": {"ipAddress": "192.168.10.55"},
        },
        neighbor=None,
        known_devices_by_ip={},
        known_devices_by_hostname={},
    )
    assert result is None


def test_multi_mac_uplink_to_unrecognized_device_shows_nothing():
    """
    A trunk/uplink port with several learned MACs must never show an
    arbitrary one of those MACs' IPs as "the" device on that port — and
    with no CDP/LLDP neighbor identifying a device this system already
    knows, there is nothing true left to say.
    """
    result = resolve_port_device_ip(
        mac_addresses=["0011.2233.4455", "aabb.ccdd.eeff", "1122.3344.5566"],
        arp_cache={
            # Even though every one of these MACs happens to have a known
            # IP, none may be surfaced — this is exactly the "arbitrary
            # pick among several" bug the spec calls out.
            "001122334455": {"ipAddress": "192.168.10.10"},
            "aabbccddeeff": {"ipAddress": "192.168.10.11"},
            "112233445566": {"ipAddress": "192.168.10.12"},
        },
        neighbor=None,
        known_devices_by_ip={},
        known_devices_by_hostname={},
    )
    assert result is None


def test_multi_mac_uplink_with_neighbor_present_but_unmatched_shows_nothing():
    """A neighbor record exists but doesn't match any known device — still
    a guess if we showed anything, so still nothing."""
    result = resolve_port_device_ip(
        mac_addresses=["0011.2233.4455", "aabb.ccdd.eeff"],
        arp_cache={},
        neighbor={"ip": "10.99.99.99", "hostname": "unknown-ap"},
        known_devices_by_ip={"192.168.10.1": {"ipAddress": "192.168.10.1"}},
        known_devices_by_hostname={"core01": {"ipAddress": "192.168.10.1"}},
    )
    assert result is None


# ---------------------------------------------------------------------------
# Positive paths
# ---------------------------------------------------------------------------

def test_single_mac_resolves_via_arp_cache():
    result = resolve_port_device_ip(
        mac_addresses=["0011.2233.4455"],
        arp_cache={"001122334455": {"ipAddress": "192.168.10.55"}},
        neighbor=None,
        known_devices_by_ip={},
        known_devices_by_hostname={},
    )
    assert result == {"ip": "192.168.10.55", "via": RESOLUTION_VIA_ARP}


def test_single_mac_not_yet_in_arp_cache_shows_nothing():
    """Passive-miss / active sweep hasn't caught it yet — unresolved, not guessed."""
    result = resolve_port_device_ip(
        mac_addresses=["0011.2233.4455"],
        arp_cache={},
        neighbor=None,
        known_devices_by_ip={},
        known_devices_by_hostname={},
    )
    assert result is None


def test_multi_mac_uplink_matching_known_device_by_neighbor_ip_resolves():
    """A true statement is fine: 'this port connects to that known switch'."""
    result = resolve_port_device_ip(
        mac_addresses=["0011.2233.4455", "aabb.ccdd.eeff", "1122.3344.5566"],
        arp_cache={},
        neighbor={"ip": "192.168.10.1", "hostname": "CORE01"},
        known_devices_by_ip={"192.168.10.1": {"ipAddress": "192.168.10.1"}},
        known_devices_by_hostname={},
    )
    assert result == {"ip": "192.168.10.1", "via": RESOLUTION_VIA_NEIGHBOR}


def test_multi_mac_uplink_matching_known_device_by_neighbor_hostname_resolves():
    result = resolve_port_device_ip(
        mac_addresses=["0011.2233.4455", "aabb.ccdd.eeff"],
        arp_cache={},
        neighbor={"hostname": "CORE01", "managementAddress": ""},
        known_devices_by_ip={},
        known_devices_by_hostname={"core01": {"ipAddress": "192.168.10.1"}},
    )
    assert result == {"ip": "192.168.10.1", "via": RESOLUTION_VIA_NEIGHBOR}


def test_mac_addresses_deduplicated_before_counting():
    """The same MAC reported twice (e.g. seen in two VLANs) is one device,
    not treated as a multi-device port."""
    result = resolve_port_device_ip(
        mac_addresses=["0011.2233.4455", "00:11:22:33:44:55"],
        arp_cache={"001122334455": {"ipAddress": "192.168.10.55"}},
        neighbor=None,
        known_devices_by_ip={},
        known_devices_by_hostname={},
    )
    assert result == {"ip": "192.168.10.55", "via": RESOLUTION_VIA_ARP}


def test_malformed_macs_are_ignored_not_counted():
    """A garbage entry shouldn't push a single real MAC into 'multi-MAC'."""
    result = resolve_port_device_ip(
        mac_addresses=["0011.2233.4455", "", "not-a-mac"],
        arp_cache={"001122334455": {"ipAddress": "192.168.10.55"}},
        neighbor=None,
        known_devices_by_ip={},
        known_devices_by_hostname={},
    )
    assert result == {"ip": "192.168.10.55", "via": RESOLUTION_VIA_ARP}
