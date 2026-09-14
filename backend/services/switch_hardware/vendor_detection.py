"""Cisco platform detection for switch hardware collection."""

from __future__ import annotations

import re
from typing import Any

from services.interface_collection.ssh_collector import _normalize_vendor

CISCO_PLATFORMS = frozenset({"cisco_ios", "cisco_xe", "cisco_nxos"})


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def is_cisco_device(device: dict) -> bool:
    vendor = _text(device.get("vendor"))
    if "cisco" in vendor:
        return True
    creds = device.get("credentials") or {}
    ssh_vendor = _text(creds.get("sshVendor"))
    if "cisco" in ssh_vendor or ssh_vendor in CISCO_PLATFORMS:
        return True
    device_type = _text(device.get("deviceType") or device.get("type"))
    return "cisco" in device_type


def is_eligible_switch(device: dict) -> bool:
    if not device.get("monitor", True):
        return False
    if not is_cisco_device(device):
        return False
    device_type = _text(device.get("deviceType") or device.get("type"))
    if not device_type:
        return bool(device.get("credentials"))
    if any(token in device_type for token in ("switch", "router", "firewall")):
        return True
    return bool(device.get("credentials"))


def detect_platform_from_show_version(output: str | None) -> str | None:
    text = output or ""
    lower = text.lower()
    if "nx-os" in lower or "nexus" in lower:
        return "cisco_nxos"
    if "ios-xe" in lower or "xe software" in lower:
        return "cisco_xe"
    if "cisco ios" in lower or "ios software" in lower:
        return "cisco_ios"
    if re.search(r"catalyst\s+\d", lower):
        return "cisco_ios"
    return None


def detect_platform(
    device: dict,
    *,
    show_version_output: str | None = None,
    sys_descr: str | None = None,
) -> str | None:
    creds = device.get("credentials") or {}
    normalized = _normalize_vendor(creds.get("sshVendor") or "")
    if normalized in CISCO_PLATFORMS:
        return normalized

    from_version = detect_platform_from_show_version(show_version_output)
    if from_version:
        return from_version

    descr = _text(sys_descr)
    if "nx-os" in descr:
        return "cisco_nxos"
    if "ios-xe" in descr:
        return "cisco_xe"
    if "cisco" in descr:
        return "cisco_ios"
    return None


def platform_label(platform: str | None) -> str | None:
    if not platform:
        return None
    mapping = {
        "cisco_ios": "IOS",
        "cisco_xe": "IOS-XE",
        "cisco_nxos": "NX-OS",
    }
    return mapping.get(platform, platform)
