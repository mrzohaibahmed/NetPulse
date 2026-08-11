"""
port_resolution.py
===================
Resolve the IP address of whatever is plugged into a switch port.

A switch cannot report "what IP is on this port" directly. It is derived
through a chain:

    port -> MAC(s) learned on that port (bridge/forwarding table)
         -> MAC looked up in an ARP table (read from whichever device
            does Layer-3 routing for that VLAN)
         -> IP address

``resolve_port_device_ip`` is the pure decision function for that chain,
given already-collected data (no SSH/SNMP/Mongo access here — this module
never talks to the network or the database; callers assemble the lookup
dicts and pass them in). Keeping it pure makes the exact "must never guess"
rules independently testable.

Decision rules
--------------
- 0 MACs learned on the port  -> unresolved (nothing plugged in / link down).
- Exactly 1 MAC learned       -> resolved via the shared ARP cache if that
  MAC has a known IP; otherwise unresolved (not yet ARP'd — never guessed).
- 2+ MACs learned (trunk/uplink carrying many devices) -> a single MAC's IP
  is NEVER shown, since that would misleadingly imply one device sits on
  that port. If the port's CDP/LLDP neighbor identity matches a device
  already known to this system, that known device's own management IP is
  shown instead (a true statement: "this port connects to that device").
  Otherwise, unresolved. There is no fallback that picks an arbitrary MAC
  among several — that is the exact bug this module exists to avoid.

``attach_resolved_ips`` is the batch wrapper that loads Mongo data once for
a whole list of interfaces and calls the pure function per interface —
callers (routes) use this; tests exercise ``resolve_port_device_ip`` and
``normalize_mac`` directly with plain dicts, no database required.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

RESOLUTION_VIA_ARP = "arp"
RESOLUTION_VIA_NEIGHBOR = "neighbor"

_HEX_ONLY = re.compile(r"[^0-9a-fA-F]")


def normalize_mac(raw: str | None) -> str | None:
    """
    Normalise any MAC representation to a bare lowercase 12-hex-digit string.

    Accepts Cisco dotted-quad (``0011.2233.4455``), colon-separated
    (``00:11:22:33:44:55``), hyphen-separated, or already-bare forms.
    Returns ``None`` for anything that doesn't reduce to exactly 12 hex
    digits (malformed / empty input never silently becomes a wrong key).
    """
    if not raw:
        return None
    digits = _HEX_ONLY.sub("", str(raw)).lower()
    if len(digits) != 12:
        return None
    return digits


def resolve_port_device_ip(
    mac_addresses: Iterable[str | None],
    arp_cache: dict[str, dict[str, Any]],
    *,
    neighbor: dict[str, Any] | None = None,
    known_devices_by_ip: dict[str, dict[str, Any]] | None = None,
    known_devices_by_hostname: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """
    Decide the resolved IP (if any) for one switch port.

    Parameters
    ----------
    mac_addresses : the MACs currently learned on this port (any format).
    arp_cache : shared MAC -> {"ipAddress": ..., ...} map, keyed by
        ``normalize_mac`` output, aggregated from every L3-routing device
        polled (not per-switch).
    neighbor : this port's CDP/LLDP neighbor record, if any
        (``{"ip":..., "hostname":..., "managementAddress":...}``).
    known_devices_by_ip / known_devices_by_hostname : lookup maps of devices
        already tracked by this system, keyed by lowercase ipAddress /
        hostname, values are device dicts containing at least ``ipAddress``.

    Returns
    -------
    ``{"ip": str, "via": "arp" | "neighbor"}`` or ``None`` when the port
    should show nothing.
    """
    macs = sorted({m for m in (normalize_mac(m) for m in mac_addresses) if m})

    if len(macs) == 0:
        return None

    if len(macs) == 1:
        entry = arp_cache.get(macs[0])
        if entry and entry.get("ipAddress"):
            return {"ip": entry["ipAddress"], "via": RESOLUTION_VIA_ARP}
        return None

    # Multi-MAC (trunk/uplink) port: never guess one of several MACs.
    # Only show something when the neighbor's identity matches a device
    # this system already knows about.
    if not neighbor:
        return None

    known_by_ip = known_devices_by_ip or {}
    known_by_hostname = known_devices_by_hostname or {}

    n_ip = str(
        neighbor.get("ip") or neighbor.get("managementAddress") or ""
    ).strip().lower()
    n_hostname = str(neighbor.get("hostname") or "").strip().lower()

    matched = None
    if n_ip and n_ip in known_by_ip:
        matched = known_by_ip[n_ip]
    elif n_hostname and n_hostname in known_by_hostname:
        matched = known_by_hostname[n_hostname]

    if matched and matched.get("ipAddress"):
        return {"ip": matched["ipAddress"], "via": RESOLUTION_VIA_NEIGHBOR}

    return None


# ---------------------------------------------------------------------------
# Batch enrichment (Mongo-backed) — used by routes, not by unit tests above.
# ---------------------------------------------------------------------------

def attach_resolved_ips(interfaces: list[dict]) -> list[dict]:
    """
    Compute ``resolvedDeviceIp`` / ``resolvedDeviceIpVia`` for a batch of
    interface documents in-place (mutates and returns the same list).

    Loads the port-MAC table, shared ARP cache, and known-device index once
    for the whole batch rather than per interface.
    """
    from config.database import db  # noqa: PLC0415
    from services.interface_collection.naming import (  # noqa: PLC0415
        canonicalize_interface_name,
    )

    if not interfaces:
        return interfaces

    device_ids = {iface["deviceId"] for iface in interfaces if iface.get("deviceId")}
    if not device_ids:
        for iface in interfaces:
            iface["resolvedDeviceIp"] = None
            iface["resolvedDeviceIpVia"] = None
        return interfaces

    # Port -> learned MACs, keyed by (deviceId, canonical interface name).
    mac_by_port: dict[tuple[Any, str], list[str]] = {}
    for doc in db.port_mac_table.find({"deviceId": {"$in": list(device_ids)}}):
        key = (doc.get("deviceId"), canonicalize_interface_name(doc.get("interfaceName")))
        mac_by_port[key] = list(doc.get("macAddresses") or [])

    # Shared ARP cache, keyed by normalized MAC.
    all_macs = {m for macs in mac_by_port.values() for m in macs}
    normalized_needed = {normalize_mac(m) for m in all_macs if normalize_mac(m)}
    arp_cache: dict[str, dict[str, Any]] = {}
    if normalized_needed:
        for doc in db.arp_cache.find({"macAddress": {"$in": list(normalized_needed)}}):
            arp_cache[doc["macAddress"]] = doc

    # Known devices, for multi-MAC neighbor-identity matching.
    known_devices_by_ip: dict[str, dict[str, Any]] = {}
    known_devices_by_hostname: dict[str, dict[str, Any]] = {}
    for doc in db.devices.find({}, {"ipAddress": 1, "hostname": 1}):
        ip = (doc.get("ipAddress") or "").strip().lower()
        hostname = (doc.get("hostname") or "").strip().lower()
        entry = {"ipAddress": doc.get("ipAddress")}
        if ip:
            known_devices_by_ip[ip] = entry
        if hostname:
            known_devices_by_hostname[hostname] = entry

    for iface in interfaces:
        key = (iface.get("deviceId"), canonicalize_interface_name(iface.get("name")))
        macs = mac_by_port.get(key, [])
        resolution = resolve_port_device_ip(
            macs,
            arp_cache,
            neighbor=iface.get("neighbor") if isinstance(iface.get("neighbor"), dict) else None,
            known_devices_by_ip=known_devices_by_ip,
            known_devices_by_hostname=known_devices_by_hostname,
        )
        iface["resolvedDeviceIp"] = resolution["ip"] if resolution else None
        iface["resolvedDeviceIpVia"] = resolution["via"] if resolution else None

    return interfaces
