"""
normalizer.py
=============
Map vendor-specific interface fields onto a single vendor-independent schema.

This normalised shape is the contract for Storm Protection / Port Eligibility
consumers — they must not re-parse CLI output.

Classification flags (isUplink, isInfrastructure, …) are applied afterwards by
``classifier.classify_interface`` — this module only maps fields.
"""

from __future__ import annotations

import re
from typing import Any

from services.interface_collection.naming import normalize_storage_interface_name


_ADMIN_UP = frozenset({
    "up", "enabled", "enable", "admin-up", "administratively up",
})
_ADMIN_DOWN = frozenset({
    "down", "disabled", "disable", "admin-down", "administratively down",
    "shutdown",
})

_OPER_UP = frozenset({
    "up", "connected", "active", "link-up", "ready",
})
_OPER_DOWN = frozenset({
    "down", "notconnect", "not connected", "inactive", "link-down",
    "err-disabled", "errdisabled", "error-disabled", "suspended",
    "monitoring", "sfpabsent", "sfp-absent", "no-link",
})

_MODE_ACCESS = frozenset({"access", "static access", "static-access"})
_MODE_TRUNK = frozenset({
    "trunk", "dynamic desirable", "dynamic auto", "dot1q-tunnel",
    "trunking",
})
_MODE_ROUTED = frozenset({"routed", "layer3", "l3", "no switchport", "routed port"})


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip() or default


def normalize_admin_status(raw: Any) -> str:
    text = _safe_str(raw).lower()
    if not text:
        return "unknown"
    if text in _ADMIN_UP or text.startswith("up"):
        return "up"
    if text in _ADMIN_DOWN or ("admin" in text and "down" in text) or text == "disabled":
        return "down"
    if text in ("connected", "notconnect", "err-disabled", "errdisabled"):
        return "up" if text == "connected" else ("down" if text == "disabled" else "up")
    return "unknown"


def normalize_oper_status(raw: Any) -> str:
    text = _safe_str(raw).lower()
    if not text:
        return "unknown"
    if text in _OPER_UP or text == "up":
        return "up"
    if text in _OPER_DOWN or text.startswith("down") or "err" in text:
        return "down"
    if text == "disabled":
        return "down"
    return "unknown"


def normalize_mode(raw: Any) -> str:
    """Return ``access``, ``trunk``, ``routed``, or ``unknown``."""
    text = _safe_str(raw).lower()
    if not text:
        return "unknown"
    if text in _MODE_ROUTED or text == "routed" or "routed" in text:
        return "routed"
    if text in _MODE_TRUNK or "trunk" in text:
        return "trunk"
    if text in _MODE_ACCESS or "access" in text:
        return "access"
    if text.isdigit():
        return "access"
    return "unknown"


def normalize_vlan(raw: Any, mode: str = "") -> str:
    text = _safe_str(raw)
    mode_norm = (mode or "").lower()

    if mode_norm == "routed":
        return ""
    if not text:
        return "trunk" if mode_norm == "trunk" else ""

    lower = text.lower()
    if lower == "trunk":
        return "trunk"
    if lower in ("n/a", "na", "-", "none", "routed"):
        return ""

    text = re.sub(r"(?i)^vlan\s*", "", text).strip()
    return text


def normalize_speed(raw: Any) -> str:
    text = _safe_str(raw)
    if not text:
        return ""

    lower = text.lower().replace(" ", "")
    if lower in ("auto", "a-auto", "unknown", "-", "n/a"):
        return "auto"

    lower = re.sub(r"^a-", "", lower)
    match = re.match(
        r"^(\d+(?:\.\d+)?)\s*(g|gbps|gb|m|mbps|mb|k|kbps)?$",
        lower,
        re.IGNORECASE,
    )
    if match:
        value, unit = match.group(1), (match.group(2) or "").lower()
        if unit in ("g", "gbps", "gb"):
            return f"{value}G"
        if unit in ("m", "mbps", "mb", ""):
            num = value.rstrip("0").rstrip(".") if "." in value else value
            return num
        if unit in ("k", "kbps"):
            return f"{value}K"

    return text


def normalize_speed_mbps(raw: Any) -> int | None:
    """Return link speed in Mbps as int, or None when unknown/auto."""
    text = normalize_speed(raw)
    if not text or text == "auto":
        return None
    match = re.match(r"^(\d+(?:\.\d+)?)(G)?$", text, re.I)
    if not match:
        return None
    value = float(match.group(1))
    if match.group(2):
        return int(value * 1000)
    return int(value)


def normalize_duplex(raw: Any) -> str:
    text = _safe_str(raw).lower().replace(" ", "")
    if not text or text in ("-", "n/a", "unknown"):
        return ""
    text = re.sub(r"^a-", "", text)
    if "full" in text:
        return "full"
    if "half" in text:
        return "half"
    if "auto" in text:
        return "auto"
    return text


def normalize_interface_name(raw: Any) -> str:
    """Stable short-form name for storage (Gi1/0/1 style)."""
    return normalize_storage_interface_name(_safe_str(raw))


def _as_vlan_id(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        vlan_id = int(value)
    except (TypeError, ValueError):
        text = _safe_str(value)
        if text.isdigit():
            vlan_id = int(text)
        else:
            return None
    if 1 <= vlan_id <= 4094:
        return vlan_id
    return None


def _as_vlan_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, list):
        out: list[int] = []
        for item in value:
            vlan_id = _as_vlan_id(item)
            if vlan_id is not None:
                out.append(vlan_id)
        return sorted(set(out))
    if isinstance(value, str):
        vlans: set[int] = set()
        for part in re.split(r"[,\s]+", value.strip()):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                ends = part.split("-", 1)
                if len(ends) == 2 and ends[0].isdigit() and ends[1].isdigit():
                    start, end = int(ends[0]), int(ends[1])
                    if start > end:
                        start, end = end, start
                    if end - start <= 4094:
                        vlans.update(range(start, end + 1))
                continue
            if part.isdigit():
                vlans.add(int(part))
        return sorted(v for v in vlans if 1 <= v <= 4094)
    return []


def _as_capabilities(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return sorted({_safe_str(item) for item in value if _safe_str(item)})
    text = _safe_str(value)
    if not text:
        return []
    # CDP: "Switch Router IGMP" / LLDP: "B,R" / "Bridge, Router"
    parts = re.split(r"[,/\s]+", text)
    return sorted({p.strip() for p in parts if p.strip() and p.strip().lower() not in ("none", "n/a", "-")})


def normalize_neighbor(raw: Any) -> dict | None:
    """
    Normalise a CDP/LLDP neighbor into the vendor-independent schema.

    CamelCase is canonical for the API; ``port`` is retained as an alias of
    ``interface`` for backward compatibility with existing clients.
    """
    if not isinstance(raw, dict):
        return None

    hostname = _safe_str(
        raw.get("hostname") or raw.get("deviceId") or raw.get("system_name")
    )
    interface = _safe_str(
        raw.get("interface")
        or raw.get("port")
        or raw.get("remote_port")
        or raw.get("port_id")
    )
    if not hostname and not interface:
        return None

    ip = _safe_str(raw.get("ip") or raw.get("ipAddress") or raw.get("ip_address"))
    management_address = _safe_str(
        raw.get("managementAddress")
        or raw.get("management_address")
        or raw.get("mgmt_address")
        or ip
    )
    platform = _safe_str(raw.get("platform"))
    system_description = _safe_str(
        raw.get("systemDescription")
        or raw.get("system_description")
        or raw.get("version")
    )
    capabilities = _as_capabilities(
        raw.get("capabilities") or raw.get("capability")
    )
    protocol = _safe_str(raw.get("protocol")).lower()
    device_type = _safe_str(
        raw.get("deviceType") or raw.get("device_type")
    )

    neighbor = {
        "hostname": hostname,
        "ip": ip,
        "platform": platform,
        "deviceType": device_type,
        "interface": interface,
        # Backward-compatible alias
        "port": interface,
        "protocol": protocol,
        "managementAddress": management_address,
        "systemDescription": system_description,
        "capabilities": capabilities,
    }
    return neighbor


def normalize_raw_interface(raw: dict) -> dict:
    """
    Convert a loosely-parsed interface dict into the inventory schema.

    Canonical keys (camelCase)
    --------------------------
    name, description, adminStatus, operStatus,
    portMode, accessVlan, voiceVlan, nativeVlan, allowedVlans, vlan,
    speed, speedMbps, duplex,
    neighbor, ifIndex, macAddress, vendor

    Classification flags are applied by ``classifier.classify_interface``.
    """
    if not isinstance(raw, dict):
        raise TypeError("raw interface must be a dict")

    name = normalize_interface_name(raw.get("name") or raw.get("interface"))
    if not name:
        raise ValueError("Interface name is required for normalisation")

    admin_raw = raw.get("admin_status", raw.get("adminStatus", raw.get("status", "")))
    oper_raw = raw.get(
        "oper_status",
        raw.get("operStatus", raw.get("protocol", raw.get("status", ""))),
    )
    mode_raw = raw.get("mode", raw.get("switchport_mode", raw.get("port_mode", "")))
    vlan_raw = raw.get("vlan", raw.get("access_vlan", raw.get("vlan_id", "")))

    status_only = _safe_str(raw.get("status")).lower()
    if status_only and not raw.get("admin_status") and not raw.get("adminStatus"):
        if status_only == "disabled":
            admin_status, oper_status = "down", "down"
        elif status_only == "connected":
            admin_status, oper_status = "up", "up"
        elif status_only in ("notconnect", "err-disabled", "errdisabled", "monitoring"):
            admin_status, oper_status = "up", "down"
        else:
            admin_status = normalize_admin_status(admin_raw)
            oper_status = normalize_oper_status(oper_raw)
    else:
        admin_status = normalize_admin_status(admin_raw)
        oper_status = normalize_oper_status(oper_raw)

    port_mode = normalize_mode(mode_raw)
    vlan_text = _safe_str(vlan_raw).lower()
    if port_mode == "unknown":
        if vlan_text == "trunk":
            port_mode = "trunk"
        elif vlan_text == "routed":
            port_mode = "routed"
        elif vlan_text.isdigit() or re.match(r"^\d", vlan_text):
            port_mode = "access"

    access_vlan = _as_vlan_id(
        raw.get("access_vlan", raw.get("accessVlan"))
    )
    native_vlan = _as_vlan_id(
        raw.get("native_vlan", raw.get("nativeVlan"))
    )
    voice_vlan = _as_vlan_id(
        raw.get("voice_vlan", raw.get("voiceVlan"))
    )
    allowed_vlans = _as_vlan_list(
        raw.get("allowed_vlans", raw.get("allowedVlans"))
    )

    # Enforce access / trunk field rules for Storm Protection consumers.
    if port_mode == "access":
        if access_vlan is None and vlan_text.isdigit():
            access_vlan = int(vlan_text)
        native_vlan = None
        allowed_vlans = []
        vlan = str(access_vlan) if access_vlan is not None else normalize_vlan(vlan_raw, "access")
    elif port_mode == "trunk":
        access_vlan = None
        vlan = "trunk"
    elif port_mode == "routed":
        access_vlan = None
        native_vlan = None
        allowed_vlans = []
        voice_vlan = None
        vlan = ""
    else:
        vlan = normalize_vlan(vlan_raw, port_mode)

    if_index = raw.get("if_index", raw.get("ifIndex"))
    if if_index is not None:
        try:
            if_index = int(if_index)
        except (TypeError, ValueError):
            if_index = None

    speed_raw = raw.get("speed")
    speed_str = normalize_speed(speed_raw)
    speed_mbps = normalize_speed_mbps(speed_raw)

    return {
        "name": name,
        "description": _safe_str(raw.get("description")),
        "adminStatus": admin_status,
        "operStatus": oper_status,
        # Legacy alias kept for existing UI filters
        "mode": port_mode,
        "portMode": port_mode,
        "accessVlan": access_vlan,
        "voiceVlan": voice_vlan,
        "nativeVlan": native_vlan,
        "allowedVlans": allowed_vlans,
        "vlan": vlan,
        "speed": speed_str,
        "speedMbps": speed_mbps,
        "duplex": normalize_duplex(raw.get("duplex")),
        "neighbor": normalize_neighbor(raw.get("neighbor")),
        "ifIndex": if_index,
        "macAddress": _safe_str(raw.get("mac_address", raw.get("macAddress"))),
        "vendor": _safe_str(raw.get("vendor")),
    }
