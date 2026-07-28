"""
Parse read-only show-command outputs into structured diagnostics snapshots.
"""

from __future__ import annotations

import re
from typing import Any, Optional


def _first(pattern: str, text: str, flags=re.IGNORECASE | re.MULTILINE) -> Optional[str]:
    match = re.search(pattern, text or "", flags)
    if not match:
        return None
    for group in match.groups():
        if group is not None:
            return str(group).strip()
    return match.group(0).strip()


def parse_interface_snapshot(raw: Optional[str], interface: str) -> dict[str, Any]:
    text = raw or ""
    if not text.strip():
        return {
            "interface": interface,
            "available": False,
            "rawPresent": False,
        }

    admin = _first(
        r"Administratively\s+(down|up)|line protocol is (administratively down)",
        text,
    )
    if admin and "down" in admin.lower():
        admin_status = "down"
    else:
        line = _first(r"line protocol is (\w+)", text)
        admin_status = "up" if line else "unknown"
        if re.search(r"Administratively down", text, re.I):
            admin_status = "down"

    oper = _first(r"line protocol is (up|down)", text) or "unknown"
    speed = _first(r"BW\s+(\d+)\s*Kbit", text)
    duplex = _first(r"\b((?:Full|Half)-duplex)\b", text)
    mtu = _first(r"MTU\s+(\d+)", text)
    last_input = _first(r"Last input\s+([^,]+)", text)
    last_output = _first(r"Last output\s+([^,]+)", text)
    input_errors = _first(r"(\d+)\s+input errors", text)
    crc = _first(r"(\d+)\s+CRC", text)
    output_errors = _first(r"(\d+)\s+output errors", text)
    discards = _first(r"(\d+)\s+(?:interface )?resets|(\d+)\s+total output drops", text)

    # Prefer input/output drops if present
    in_discards = _first(r"(\d+)\s+unknown protocol drops", text)
    out_discards = _first(r"(\d+)\s+total output drops", text)

    return {
        "interface": interface,
        "available": True,
        "rawPresent": True,
        "adminStatus": admin_status,
        "operStatus": oper.lower() if oper else "unknown",
        "speed": f"{speed} Kbit" if speed else None,
        "speedKbps": int(speed) if speed and speed.isdigit() else None,
        "duplex": duplex,
        "mtu": int(mtu) if mtu and mtu.isdigit() else None,
        "lastInput": last_input,
        "lastOutput": last_output,
        "errors": {
            "input": int(input_errors) if input_errors and input_errors.isdigit() else None,
            "output": int(output_errors) if output_errors and output_errors.isdigit() else None,
        },
        "crc": int(crc) if crc and crc.isdigit() else None,
        "discards": {
            "input": int(in_discards) if in_discards and in_discards.isdigit() else None,
            "output": int(out_discards) if out_discards and out_discards.isdigit() else None,
            "other": discards,
        },
    }


def parse_switchport_snapshot(raw: Optional[str], interface: str) -> dict[str, Any]:
    text = raw or ""
    if not text.strip():
        return {
            "interface": interface,
            "available": False,
            "supported": False,
            "rawPresent": False,
        }

    mode = _first(
        r"(?:Administrative|Operational)\s+Mode:\s*(.+)$",
        text,
    ) or _first(r"Switchport:\s*(\S+)", text)
    access_vlan = _first(r"Access Mode VLAN:\s*(\d+)", text)
    voice_vlan = _first(r"Voice VLAN:\s*(\d+|none)", text)
    native_vlan = _first(r"Trunking Native Mode VLAN:\s*(\d+)", text)
    allowed = _first(r"Trunking VLANs Enabled:\s*(.+)$", text)

    allowed_list: list[str] = []
    if allowed:
        allowed_list = [part.strip() for part in re.split(r"[,\s]+", allowed) if part.strip()]

    return {
        "interface": interface,
        "available": True,
        "supported": True,
        "rawPresent": True,
        "mode": (mode or "unknown").lower(),
        "accessVlan": int(access_vlan) if access_vlan and access_vlan.isdigit() else None,
        "voiceVlan": None if not voice_vlan or voice_vlan.lower() == "none" else (
            int(voice_vlan) if voice_vlan.isdigit() else voice_vlan
        ),
        "nativeVlan": int(native_vlan) if native_vlan and native_vlan.isdigit() else None,
        "allowedVlans": allowed_list,
    }


def parse_mac_table(raw: Optional[str], interface: str) -> dict[str, Any]:
    text = raw or ""
    if not text.strip():
        return {
            "interface": interface,
            "available": False,
            "supported": False,
            "entries": [],
            "macCount": 0,
            "rawPresent": False,
        }

    entries: list[dict[str, Any]] = []
    # Typical IOS: VLAN  MAC Address  Type  Ports
    for match in re.finditer(
        r"(?P<vlan>\d+)\s+(?P<mac>(?:[0-9a-f]{4}\.){2}[0-9a-f]{4})\s+(?P<type>\S+)\s+(?P<port>\S+)",
        text,
        re.I,
    ):
        entries.append({
            "vlan": int(match.group("vlan")),
            "mac": match.group("mac").lower(),
            "type": match.group("type"),
            "port": match.group("port"),
        })

    # Alternate: xxxx.xxxx.xxxx dynamically on Gi1/0/10
    if not entries:
        for match in re.finditer(
            r"(?P<mac>(?:[0-9a-f]{4}\.){2}[0-9a-f]{4})",
            text,
            re.I,
        ):
            entries.append({
                "vlan": None,
                "mac": match.group("mac").lower(),
                "type": None,
                "port": interface,
            })

    return {
        "interface": interface,
        "available": True,
        "supported": True,
        "rawPresent": True,
        "entries": entries,
        "macCount": len(entries),
        "vlans": sorted({e["vlan"] for e in entries if e.get("vlan") is not None}),
    }


def parse_device_health(
    cpu_raw: Optional[str],
    uptime_raw: Optional[str],
    *,
    mongo_health: Optional[dict] = None,
) -> dict[str, Any]:
    health: dict[str, Any] = {
        "available": False,
        "cpuPercent": None,
        "memoryPercent": None,
        "uptime": None,
    }
    mongo_health = mongo_health or {}

    cpu_text = cpu_raw or ""
    cpu = _first(r"CPU utilization.*?five seconds:\s*(\d+)%", cpu_text)
    if cpu is None:
        cpu = _first(r"(\d+)%", cpu_text)
    if cpu and cpu.isdigit():
        health["cpuPercent"] = float(cpu)

    uptime = None
    if uptime_raw:
        uptime = _first(r"uptime is (.+)$", uptime_raw) or uptime_raw.strip()[:120]
    health["uptime"] = uptime or mongo_health.get("uptime")

    if health["cpuPercent"] is None:
        health["cpuPercent"] = mongo_health.get("cpuPercent")
    if health["memoryPercent"] is None:
        health["memoryPercent"] = mongo_health.get("memoryPercent")

    health["available"] = any(
        health.get(k) is not None for k in ("cpuPercent", "memoryPercent", "uptime")
    )
    return health
