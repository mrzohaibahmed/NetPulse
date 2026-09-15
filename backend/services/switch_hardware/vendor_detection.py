"""Cisco platform detection for switch hardware collection."""

from __future__ import annotations

import re
from typing import Any


CISCO_PLATFORMS = frozenset({"cisco_ios", "cisco_xe", "cisco_nxos"})
_CISCO_VENDOR_ALIASES = {
    "cisco": "cisco_ios",
    "ios": "cisco_ios",
    "ios-xe": "cisco_xe",
    "ios_xe": "cisco_xe",
    "iosxe": "cisco_xe",
    "nxos": "cisco_nxos",
    "nx-os": "cisco_nxos",
    "nx_os": "cisco_nxos",
}
NON_CISCO_HINTS = (
    "juniper",
    "aruba",
    "hewlett",
    "procurve",
    "mikrotik",
    "ubiquiti",
    "fortinet",
    "palo alto",
    "paloalto",
    "dell networking",
    "netgear",
    "huawei",
    "extreme networks",
    "brocade",
    "alcatel",
)
UNKNOWN_VENDOR_TOKENS = frozenset({"", "unknown", "n/a", "na", "-", "none", "null"})


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_cisco_vendor(vendor: str) -> str | None:
    key = (vendor or "").strip().lower().replace(" ", "_")
    if key in CISCO_PLATFORMS:
        return key
    mapped = _CISCO_VENDOR_ALIASES.get(key) or _CISCO_VENDOR_ALIASES.get(key.replace("-", "_"))
    if mapped in CISCO_PLATFORMS:
        return mapped
    return None


def _vendor_blob(device: dict) -> str:
    creds = device.get("credentials") or {}
    return f"{_text(device.get('vendor'))} {_text(creds.get('sshVendor'))}".strip()


def is_explicit_non_cisco(device: dict) -> bool:
    blob = _vendor_blob(device)
    if "cisco" in blob:
        return False
    return any(hint in blob for hint in NON_CISCO_HINTS)


def is_cisco_device(device: dict) -> bool:
    vendor = _text(device.get("vendor"))
    if "cisco" in vendor:
        return True
    creds = device.get("credentials") or {}
    ssh_vendor = _text(creds.get("sshVendor"))
    if "cisco" in ssh_vendor or ssh_vendor in CISCO_PLATFORMS:
        return True
    if ssh_vendor and _normalize_cisco_vendor(ssh_vendor):
        return True
    device_type = _text(device.get("deviceType") or device.get("type"))
    return "cisco" in device_type


def _is_switch_like(device: dict) -> bool:
    device_type = _text(device.get("deviceType") or device.get("type"))
    if not device_type:
        return True
    return any(token in device_type for token in ("switch", "router", "firewall"))


def _has_usable_credentials(device: dict) -> bool:
    creds = device.get("credentials") or {}
    if not creds:
        return False
    return bool(
        creds.get("sshPassword")
        or creds.get("sshUsername")
        or creds.get("snmpCommunity")
        or creds.get("sshSecret")
    )


def _vendor_is_unknown(device: dict) -> bool:
    return _text(device.get("vendor")) in UNKNOWN_VENDOR_TOKENS


def is_eligible_switch(device: dict) -> bool:
    """
    Phase 1 eligibility for Cisco chassis hardware monitoring.

    Accepts:
    - Monitored Cisco-marked switches/routers/firewalls
    - Provisional: monitored switch-like devices with unknown vendor and credentials
      (collectors default to Cisco IOS command/OID sets)
    """
    if not device.get("monitor", True):
        return False
    if is_explicit_non_cisco(device):
        return False
    if not _is_switch_like(device):
        return False
    if is_cisco_device(device):
        return True
    # Discovery often leaves vendor blank on real Cisco gear.
    return _vendor_is_unknown(device) and _has_usable_credentials(device)


def get_ineligibility_reason(device: dict) -> str | None:
    """Human-readable reason when ``is_eligible_switch`` is false."""
    if is_eligible_switch(device):
        return None
    if not device.get("monitor", True):
        return "Device monitoring is disabled on this switch"
    if is_explicit_non_cisco(device):
        return "Device vendor is not Cisco"
    if not _is_switch_like(device):
        return "Device type is not a switch, router, or firewall"
    if is_cisco_device(device):
        return "Device is not eligible for hardware monitoring"
    vendor = (device.get("vendor") or "").strip()
    if vendor and not _vendor_is_unknown(device):
        return (
            f"Vendor '{vendor}' is not recognized as Cisco. "
            "Set Vendor to Cisco, or set SSH vendor to cisco_ios / cisco_xe / cisco_nxos."
        )
    if not _has_usable_credentials(device):
        return (
            "No Cisco vendor signal and no SSH/SNMP credentials. "
            "Set Vendor to Cisco, or add credentials so collection can run."
        )
    return "Device is not an eligible Cisco switch"


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
    ssh_vendor = _text(creds.get("sshVendor"))
    if ssh_vendor:
        normalized = _normalize_cisco_vendor(ssh_vendor)
        if normalized:
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
