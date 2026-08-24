"""
Canonical device-type vocabulary and identification payload helpers.

Inventory still stores human-readable ``deviceType`` values
(e.g. ``Managed Switch``, ``Windows PC``) so existing APIs, filters,
and storm/switch matching keep working. Canonical categories live on
``identification.deviceType`` and can be derived from any stored label.
"""

from __future__ import annotations

from typing import Any, Iterable

# Canonical categories (Phase 1 vocabulary).
CANONICAL_PC = "PC"
CANONICAL_LAPTOP = "LAPTOP"
CANONICAL_SERVER = "SERVER"
CANONICAL_PRINTER = "PRINTER"
CANONICAL_CAMERA = "CAMERA"
CANONICAL_SWITCH = "SWITCH"
CANONICAL_ROUTER = "ROUTER"
CANONICAL_FIREWALL = "FIREWALL"
CANONICAL_ACCESS_POINT = "ACCESS_POINT"
CANONICAL_PHONE = "PHONE"
CANONICAL_IOT = "IOT"
CANONICAL_NETWORK_DEVICE = "NETWORK_DEVICE"
CANONICAL_UNKNOWN = "UNKNOWN"

CANONICAL_TYPES = (
    CANONICAL_PC,
    CANONICAL_LAPTOP,
    CANONICAL_SERVER,
    CANONICAL_PRINTER,
    CANONICAL_CAMERA,
    CANONICAL_SWITCH,
    CANONICAL_ROUTER,
    CANONICAL_FIREWALL,
    CANONICAL_ACCESS_POINT,
    CANONICAL_PHONE,
    CANONICAL_IOT,
    CANONICAL_NETWORK_DEVICE,
    CANONICAL_UNKNOWN,
)

# Display labels already used in MongoDB / UI (do not rename stored values).
DISPLAY_WINDOWS_PC = "Windows PC"
DISPLAY_PC = "PC"
DISPLAY_LAPTOP = "Laptop"
DISPLAY_WORKSTATION = "Workstation"
DISPLAY_LINUX_SERVER = "Linux Server"
DISPLAY_SERVER = "Server"
DISPLAY_HYPERVISOR = "Hypervisor"
DISPLAY_NAS = "NAS"
DISPLAY_PRINTER = "Printer"
DISPLAY_CAMERA = "IP Camera"
DISPLAY_SWITCH = "Switch"
DISPLAY_MANAGED_SWITCH = "Managed Switch"
DISPLAY_ROUTER = "Router"
DISPLAY_FIREWALL = "Firewall"
DISPLAY_ACCESS_POINT = "Access Point"
DISPLAY_PHONE = "IP Phone"
DISPLAY_IOT = "IoT"
DISPLAY_NETWORK_DEVICE = "Network Device"
DISPLAY_UNKNOWN = "Unknown Device"

_DISPLAY_TO_CANONICAL: dict[str, str] = {
    DISPLAY_WINDOWS_PC.lower(): CANONICAL_PC,
    DISPLAY_PC.lower(): CANONICAL_PC,
    DISPLAY_LAPTOP.lower(): CANONICAL_LAPTOP,
    DISPLAY_WORKSTATION.lower(): CANONICAL_PC,
    DISPLAY_LINUX_SERVER.lower(): CANONICAL_SERVER,
    DISPLAY_SERVER.lower(): CANONICAL_SERVER,
    "esxi server": CANONICAL_SERVER,
    DISPLAY_HYPERVISOR.lower(): CANONICAL_SERVER,
    DISPLAY_NAS.lower(): CANONICAL_SERVER,
    DISPLAY_PRINTER.lower(): CANONICAL_PRINTER,
    DISPLAY_CAMERA.lower(): CANONICAL_CAMERA,
    "wifi camera": CANONICAL_CAMERA,
    DISPLAY_SWITCH.lower(): CANONICAL_SWITCH,
    DISPLAY_MANAGED_SWITCH.lower(): CANONICAL_SWITCH,
    "l3 switch": CANONICAL_SWITCH,
    DISPLAY_ROUTER.lower(): CANONICAL_ROUTER,
    "wifi router": CANONICAL_ROUTER,
    DISPLAY_FIREWALL.lower(): CANONICAL_FIREWALL,
    DISPLAY_ACCESS_POINT.lower(): CANONICAL_ACCESS_POINT,
    DISPLAY_PHONE.lower(): CANONICAL_PHONE,
    DISPLAY_IOT.lower(): CANONICAL_IOT,
    DISPLAY_NETWORK_DEVICE.lower(): CANONICAL_NETWORK_DEVICE,
    DISPLAY_UNKNOWN.lower(): CANONICAL_UNKNOWN,
    "unknown": CANONICAL_UNKNOWN,
    "other": CANONICAL_UNKNOWN,
}

# Direct canonical tokens (already normalized).
for _token in CANONICAL_TYPES:
    _DISPLAY_TO_CANONICAL[_token.lower()] = _token


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def canonical_device_type(display_or_canonical: str | None) -> str:
    """Map any stored/display/canonical label to a Phase 1 category."""
    raw = _norm(display_or_canonical)
    if not raw:
        return CANONICAL_UNKNOWN
    key = raw.lower()
    mapped = _DISPLAY_TO_CANONICAL.get(key)
    if mapped:
        return mapped
    if "laptop" in key:
        return CANONICAL_LAPTOP
    if "camera" in key:
        return CANONICAL_CAMERA
    if "phone" in key:
        return CANONICAL_PHONE
    if "access point" in key or key == "ap":
        return CANONICAL_ACCESS_POINT
    if "firewall" in key:
        return CANONICAL_FIREWALL
    if "router" in key:
        return CANONICAL_ROUTER
    if "switch" in key:
        return CANONICAL_SWITCH
    if "printer" in key:
        return CANONICAL_PRINTER
    if "hypervisor" in key or "esxi" in key:
        return CANONICAL_SERVER
    if "server" in key or "nas" in key:
        return CANONICAL_SERVER
    if "windows pc" in key or key in {"pc", "workstation"}:
        return CANONICAL_PC
    if "iot" in key:
        return CANONICAL_IOT
    if "network device" in key:
        return CANONICAL_NETWORK_DEVICE
    return CANONICAL_UNKNOWN


def _unique_keep_order(values: Iterable[Any], *, limit: int) -> list:
    seen: set[str] = set()
    out: list = []
    for item in values:
        if item is None or item == "":
            continue
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def compact_identification_evidence(
    *,
    os_name: str = "",
    os_family: str = "",
    os_generation: str = "",
    os_accuracy: str = "",
    vendor: str = "",
    hostname: str = "",
    ports: Iterable[int] | None = None,
    services: Iterable[str] | None = None,
    nmap_device_type: str = "",
    signals: Iterable[str] | None = None,
    # WMI / hardware identification (Phase 3)
    manufacturer: str = "",
    model: str = "",
    serial_number: str = "",
    os_version: str = "",
    os_build: str = "",
    system_uuid: str = "",
    cpu: str = "",
    total_ram_gb: float | None = None,
    # SNMP identification (Phase 4)
    sys_descr: str = "",
    sys_object_id: str = "",
    sys_name: str = "",
    sys_uptime: str = "",
    firmware_rev: str = "",
    software_rev: str = "",
    # ONVIF identification (Phase 5)
    device_name: str = "",
    onvif_hardware_id: str = "",
) -> dict[str, Any]:
    """
    Compact evidence for MongoDB ``identification.evidence``.

    Intentionally small: no raw Nmap XML, no banners dump, no secrets.
    """
    payload: dict[str, Any] = {}
    if _norm(os_name):
        payload["os"] = _norm(os_name)
    if _norm(os_family):
        payload["osFamily"] = _norm(os_family)
    if _norm(os_generation):
        payload["osGeneration"] = _norm(os_generation)
    if _norm(os_accuracy):
        payload["osAccuracy"] = _norm(os_accuracy)
    if _norm(vendor):
        payload["vendor"] = _norm(vendor)
    host = _norm(hostname)
    if host and host.lower() not in {"unknown", "unknown device"}:
        payload["hostname"] = host
    port_list = _unique_keep_order(list(ports or []), limit=20)
    if port_list:
        payload["ports"] = port_list
    svc_list = _unique_keep_order(
        [_norm(s) for s in (services or []) if _norm(s)],
        limit=15,
    )
    if svc_list:
        payload["services"] = svc_list
    if _norm(nmap_device_type):
        payload["nmapDeviceType"] = _norm(nmap_device_type)
    sig_list = _unique_keep_order(
        [_norm(s) for s in (signals or []) if _norm(s)],
        limit=20,
    )
    if sig_list:
        payload["signals"] = sig_list
    # WMI / hardware identification (Phase 3) — only set when present.
    if _norm(manufacturer):
        payload["manufacturer"] = _norm(manufacturer)
    if _norm(model):
        payload["model"] = _norm(model)
    if _norm(serial_number):
        payload["serialNumber"] = _norm(serial_number)
    if _norm(os_version):
        payload["osVersion"] = _norm(os_version)
    if _norm(os_build):
        payload["osBuild"] = _norm(os_build)
    if _norm(system_uuid):
        payload["systemUuid"] = _norm(system_uuid)
    if _norm(cpu):
        payload["cpu"] = _norm(cpu)
    if total_ram_gb is not None and total_ram_gb > 0:
        payload["totalRamGb"] = total_ram_gb
    # SNMP identification (Phase 4) — only set when present.
    if _norm(sys_descr):
        payload["sysDescr"] = _norm(sys_descr)
    if _norm(sys_object_id):
        payload["sysObjectID"] = _norm(sys_object_id)
    if _norm(sys_name):
        payload["sysName"] = _norm(sys_name)
    if _norm(sys_uptime):
        payload["sysUpTime"] = _norm(sys_uptime)
    if _norm(firmware_rev):
        payload["firmwareRev"] = _norm(firmware_rev)
    if _norm(software_rev):
        payload["softwareRev"] = _norm(software_rev)
    # ONVIF identification (Phase 5) — only set when present.
    if _norm(device_name):
        payload["deviceName"] = _norm(device_name)
    if _norm(onvif_hardware_id):
        payload["onvifHardwareId"] = _norm(onvif_hardware_id)
    return payload


def build_identification(
    *,
    display_type: str,
    canonical_type: str | None = None,
    method: str,
    confidence: int,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    canonical = _norm(canonical_type)
    mapped = canonical_device_type(display_type)
    if not canonical or (
        canonical == CANONICAL_UNKNOWN and mapped != CANONICAL_UNKNOWN
    ):
        canonical = mapped
    if canonical not in CANONICAL_TYPES:
        canonical = CANONICAL_UNKNOWN
    try:
        conf = int(confidence)
    except (TypeError, ValueError):
        conf = 0
    conf = max(0, min(99, conf))
    return {
        "deviceType": canonical,
        "displayType": _norm(display_type) or DISPLAY_UNKNOWN,
        "method": _norm(method) or "unknown",
        "confidence": conf,
        "evidence": dict(evidence or {}),
    }
