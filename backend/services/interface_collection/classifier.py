"""
classifier.py
=============
Port and neighbor classification for Storm-Protection consumers.

This module **never** executes SSH / SNMP commands and **never** parses CLI.
It only receives already-normalised interface dictionaries and returns
classification flags (and neighbor device_type when missing).

Pipeline position
-----------------
SSH Collector → Parser → Normalizer → **Classifier** → MongoDB
"""

from __future__ import annotations

import re
from typing import Any

from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("interface")

# ---------------------------------------------------------------------------
# Neighbor device-type detection (reusable)
# ---------------------------------------------------------------------------

_DEVICE_TYPE_SWITCH = "Switch"
_DEVICE_TYPE_ROUTER = "Router"
_DEVICE_TYPE_FIREWALL = "Firewall"
_DEVICE_TYPE_AP = "Wireless AP"
_DEVICE_TYPE_WLC = "Wireless Controller"
_DEVICE_TYPE_PHONE = "IP Phone"
_DEVICE_TYPE_SERVER = "Server"
_DEVICE_TYPE_UNKNOWN = "Unknown"

_INFRA_NEIGHBOR_TYPES = frozenset({
    _DEVICE_TYPE_SWITCH,
    _DEVICE_TYPE_ROUTER,
    _DEVICE_TYPE_FIREWALL,
    _DEVICE_TYPE_WLC,
    _DEVICE_TYPE_SERVER,
})

_UPLINK_NEIGHBOR_TYPES = frozenset({
    _DEVICE_TYPE_SWITCH,
    _DEVICE_TYPE_ROUTER,
    _DEVICE_TYPE_FIREWALL,
    _DEVICE_TYPE_WLC,
})

# Ordered rules: (compiled pattern against haystack, device_type)
_DEVICE_TYPE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Wireless AP
    (re.compile(r"\b(AIR[-_]?[A-Z0-9]|C9115|C9120|C9130|access\s*point|\bAP\d)", re.I), _DEVICE_TYPE_AP),
    (re.compile(r"cisco\s+aironet|lightweight\s+ap|capwap", re.I), _DEVICE_TYPE_AP),
    # Wireless Controller
    (re.compile(r"\b(WLC|wireless\s+controller|catalyst\s*9800|AIR-CT)", re.I), _DEVICE_TYPE_WLC),
    # IP Phone
    (re.compile(r"\b(IP\s*Phone|Cisco\s+IP\s+Phone|CP-\d|SEP[A-F0-9]{12})", re.I), _DEVICE_TYPE_PHONE),
    (re.compile(r"\b(CIPC|jabber)", re.I), _DEVICE_TYPE_PHONE),
    # Firewall
    (re.compile(r"\b(fortinet|fortigate|fortios|palo\s*alto|pa-\d|asa\d|firepower|ftd|checkpoint|sophos)", re.I), _DEVICE_TYPE_FIREWALL),
    # Server / hypervisor
    (re.compile(r"\b(vmware|esxi|vcenter|hyper-?v|proxmox|nutanix|windows\s+server|linux)", re.I), _DEVICE_TYPE_SERVER),
    # Router (ISR / ASR / edge)
    (re.compile(r"\b(ISR|ASR|CSR1000|catalyst\s*8[0-9]{3}|NCS\d|IOS\s*XRv?)", re.I), _DEVICE_TYPE_ROUTER),
    (re.compile(r"\brouter\b", re.I), _DEVICE_TYPE_ROUTER),
    # Switch (Catalyst / Nexus / general)
    (re.compile(r"\b(catalyst|nexus|c9[2-5]\d{2}|c29\d{2}|c35\d{2}|c37\d{2}|c38\d{2}|ws-c)", re.I), _DEVICE_TYPE_SWITCH),
    (re.compile(r"\bswitch\b", re.I), _DEVICE_TYPE_SWITCH),
)

_CAPABILITY_MAP = (
    (re.compile(r"phone|telephony", re.I), _DEVICE_TYPE_PHONE),
    (re.compile(r"wlan|access\s*point|\bAP\b", re.I), _DEVICE_TYPE_AP),
    (re.compile(r"bridge|switch", re.I), _DEVICE_TYPE_SWITCH),
    (re.compile(r"router", re.I), _DEVICE_TYPE_ROUTER),
)


def classify_neighbor_device_type(
    *,
    platform: str = "",
    capabilities: list[str] | None = None,
    system_description: str = "",
    hostname: str = "",
) -> str:
    """
    Infer a neighbour device type from CDP/LLDP hints.

    Returns one of: Switch, Router, Firewall, Wireless AP, Wireless Controller,
    IP Phone, Server, Unknown.
    """
    caps = capabilities or []
    haystack = " | ".join(
        part for part in (
            platform or "",
            system_description or "",
            hostname or "",
            " ".join(str(c) for c in caps),
        ) if part
    )
    if not haystack.strip():
        return _DEVICE_TYPE_UNKNOWN

    for pattern, device_type in _DEVICE_TYPE_RULES:
        if pattern.search(haystack):
            return device_type

    for pattern, device_type in _CAPABILITY_MAP:
        for cap in caps:
            if pattern.search(str(cap)):
                return device_type

    return _DEVICE_TYPE_UNKNOWN


# ---------------------------------------------------------------------------
# Port classification
# ---------------------------------------------------------------------------

_UPLINK_DESC = re.compile(
    r"\b(UPLINK|CORE|DIST|DISTRIBUTION|BACKBONE|STACK|PORT[\s\-]?CHANNEL|PCHANNEL|ETHERCHANNEL)\b",
    re.I,
)
_INFRA_DESC = re.compile(
    r"\b(SERVER|CORE|DIST|DISTRIBUTION|FW|FIREWALL|RTR|ROUTER|HYPERVISOR|ESXI|STORAGE)\b",
    re.I,
)
_MGMT_DESC = re.compile(r"\b(MGMT|MANAGEMENT|OOB|OUT[\s\-]?OF[\s\-]?BAND)\b", re.I)
_MGMT_NAME = re.compile(
    r"^(Ma|Mgmt|Management|Lo|Loopback|Vlan1$|Vl1$)",
    re.I,
)


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _neighbor_dict(iface: dict) -> dict:
    neighbor = iface.get("neighbor")
    return neighbor if isinstance(neighbor, dict) else {}


def _neighbor_device_type(iface: dict) -> str:
    neighbor = _neighbor_dict(iface)
    existing = _safe_str(
        neighbor.get("deviceType") or neighbor.get("device_type")
    )
    if existing and existing != _DEVICE_TYPE_UNKNOWN:
        return existing

    detected = classify_neighbor_device_type(
        platform=_safe_str(neighbor.get("platform")),
        capabilities=list(neighbor.get("capabilities") or []),
        system_description=_safe_str(
            neighbor.get("systemDescription") or neighbor.get("system_description")
        ),
        hostname=_safe_str(neighbor.get("hostname")),
    )
    return detected


def classify_interface(iface: dict) -> dict:
    """
    Apply port classification flags onto a normalised interface dict.

    Mutates and returns ``iface`` for chaining. Preserves an existing
    ``isProtected`` value (admin override) when present.
    """
    if not isinstance(iface, dict):
        raise TypeError("classify_interface expects a dict")

    port_mode = _safe_str(iface.get("portMode") or iface.get("mode")).lower()
    description = _safe_str(iface.get("description"))
    name = _safe_str(iface.get("name"))
    admin_status = _safe_str(iface.get("adminStatus")).lower()

    neighbor = _neighbor_dict(iface)
    has_neighbor = bool(
        neighbor.get("hostname") or neighbor.get("interface") or neighbor.get("port")
    )
    device_type = _neighbor_device_type(iface) if has_neighbor or neighbor else _DEVICE_TYPE_UNKNOWN

    # Enrich neighbor.deviceType when we have a neighbor record
    if neighbor:
        neighbor = dict(neighbor)
        neighbor["deviceType"] = device_type
        iface["neighbor"] = neighbor

    is_access = port_mode == "access"
    is_trunk = port_mode == "trunk"

    is_uplink = False
    if has_neighbor and device_type in _UPLINK_NEIGHBOR_TYPES:
        is_uplink = True
    elif _UPLINK_DESC.search(description):
        is_uplink = True

    is_infrastructure = False
    if has_neighbor and device_type in _INFRA_NEIGHBOR_TYPES:
        is_infrastructure = True
    elif _INFRA_DESC.search(description):
        is_infrastructure = True

    is_management = bool(_MGMT_NAME.match(name) or _MGMT_DESC.search(description))

    # Preserve manual protection; default false for newly classified ports
    if "isProtected" in iface and iface.get("isProtected") is not None:
        is_protected = bool(iface.get("isProtected"))
    else:
        is_protected = False

    # monitoring_enabled: default true unless admin-down or explicitly disabled
    if "monitoringEnabled" in iface and iface.get("monitoringEnabled") is not None:
        # Honour explicit prior value only when not admin-down (admin-down forces off)
        monitoring_enabled = bool(iface.get("monitoringEnabled"))
        if admin_status == "down":
            monitoring_enabled = False
    else:
        monitoring_enabled = admin_status != "down"

    iface["isAccess"] = is_access
    iface["isTrunk"] = is_trunk
    iface["isUplink"] = is_uplink
    iface["isInfrastructure"] = is_infrastructure
    iface["isManagement"] = is_management
    iface["isProtected"] = is_protected
    iface["monitoringEnabled"] = monitoring_enabled

    logger.debug(
        "[IFACE] Classified %s | mode=%s uplink=%s infra=%s mgmt=%s neighbor=%s",
        name,
        port_mode,
        is_uplink,
        is_infrastructure,
        is_management,
        device_type if has_neighbor else "-",
    )
    return iface
