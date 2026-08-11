"""
mac_arp_parser.py
==================
Parse vendor CLI output for MAC (bridge/forwarding) tables, ARP tables, and
directly-connected routes. Transport-agnostic (accepts raw command-output
strings from SSH) and vendor-neutral in its return shape, matching the
existing ``parser.py`` convention: parse only, never classify, never run SSH.
"""

from __future__ import annotations

import re
from typing import Any

from services.interface_collection.naming import canonicalize_interface_name

# Cisco dotted-quad (0011.2233.4455) or colon/hyphen separated MAC token.
_MAC_TOKEN = re.compile(
    r"\b([0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}|"
    r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2})\b"
)

# Ports/interfaces that are never a "connected device" — the switch's own
# control-plane entries, not a real port with a device plugged into it.
_NON_PORT_TOKENS = frozenset({"cpu", "router", "switch", "-", "n/a", "vlan"})


def parse_cisco_mac_address_table(output: str) -> dict[str, list[str]]:
    """
    Parse ``show mac address-table`` (IOS/IOS-XE/NX-OS).

    Returns ``{canonicalInterfaceName: [mac, mac, ...]}``. A port with
    several learned MACs (trunk/uplink) legitimately maps to a list with
    more than one entry — callers decide what, if anything, to show for
    that; this parser only reports what the switch reported.
    """
    result: dict[str, list[str]] = {}
    if not output:
        return result

    for raw_line in output.replace("\r", "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("-") or line.startswith("Total"):
            continue
        if re.match(r"^(vlan|mac address table)\b", line, re.I):
            continue

        mac_match = _MAC_TOKEN.search(line)
        if not mac_match:
            continue
        mac = mac_match.group(1)

        # Port is the last whitespace-separated token on the row (works for
        # both IOS' "Vlan Mac Type Ports" and NX-OS' extra "age/secure/ntfy"
        # columns — the physical/logical port is always last).
        tokens = line.replace(",", " ").split()
        if not tokens:
            continue
        port_token = tokens[-1].strip()
        if not port_token or port_token.lower() in _NON_PORT_TOKENS:
            continue
        # Multiple ports can share one MAC row on some platforms
        # (e.g. "Gi1/0/1,Gi1/0/2" for a port-channel member listing) —
        # only take the first, canonical single-port form; port-channel
        # aggregate names aren't real ports in the `interfaces` collection
        # and are intentionally skipped by canonicalization returning None.
        canon = canonicalize_interface_name(port_token)
        if not canon:
            continue

        result.setdefault(canon, [])
        if mac not in result[canon]:
            result[canon].append(mac)

    return result


def parse_cisco_ip_arp(output: str) -> list[dict[str, Any]]:
    """
    Parse ``show ip arp`` (Cisco) into ``[{"ip", "mac", "interface"}, ...]``.

    Only rows with a real hardware address are returned — incomplete ARP
    entries (hardware address shown as ``Incomplete``) are skipped since
    there is no MAC to key the shared cache by.
    """
    entries: list[dict[str, Any]] = []
    if not output:
        return entries

    for raw_line in output.replace("\r", "").splitlines():
        line = raw_line.strip()
        if not line or not line.lower().startswith("internet"):
            continue

        mac_match = _MAC_TOKEN.search(line)
        if not mac_match:
            continue  # "Incomplete" hardware address — nothing to key by.

        ip_match = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", line)
        if not ip_match:
            continue

        tokens = line.split()
        interface = tokens[-1].strip() if tokens else ""

        entries.append({
            "ip": ip_match.group(1),
            "mac": mac_match.group(1),
            "interface": interface,
        })

    return entries


def parse_cisco_connected_routes(output: str) -> list[dict[str, Any]]:
    """
    Parse ``show ip route connected`` into
    ``[{"network": "192.168.10.0", "prefixLength": 24, "interface": "Vlan10"}, ...]``.

    Only ``C`` (connected subnet) rows are returned — ``L`` (local /32 host
    route for the router's own interface address) rows are excluded since
    they describe the router itself, not a sweepable subnet.
    """
    routes: list[dict[str, Any]] = []
    if not output:
        return routes

    row_re = re.compile(
        r"^C\s+(\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})\s+is\s+directly\s+connected,\s*(\S+)",
        re.I,
    )

    for raw_line in output.replace("\r", "").splitlines():
        line = raw_line.strip()
        match = row_re.match(line)
        if not match:
            continue
        network, prefix_len, interface = match.groups()
        routes.append({
            "network": network,
            "prefixLength": int(prefix_len),
            "interface": interface.strip(),
        })

    return routes
