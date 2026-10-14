"""Normalize Junos chassis output from EX and QFX switches."""

from __future__ import annotations

import re
from typing import Any

from services.switch_hardware.models import (
    build_fan,
    build_psu,
    build_sensor,
    empty_cpu,
    empty_fans,
    empty_inventory,
    empty_memory,
    empty_power_supplies,
    empty_temperature,
)


def _status(value: str) -> str:
    return {
        "ok": "healthy",
        "online": "healthy",
        "check": "warning",
        "warning": "warning",
        "failed": "critical",
        "failure": "critical",
        "offline": "critical",
        "absent": "not_available",
    }.get(value.strip().lower(), "unknown")


def _field(output: str, name: str) -> str | None:
    match = re.search(rf"(?im)^\s*{re.escape(name)}\s*:\s*(\S[^\r\n]*)", output)
    return match.group(1).strip() if match else None


def parse_show_version(output: str | None) -> dict[str, Any]:
    inventory = empty_inventory()
    if output:
        inventory["hostname"] = _field(output, "Hostname")
        inventory["model"] = _field(output, "Model")
        inventory["firmwareVersion"] = _field(output, "Junos") or _field(output, "JUNOS Base OS boot")
    return inventory


def parse_show_chassis_hardware(output: str | None) -> dict[str, Any]:
    inventory = empty_inventory()
    if not output:
        return inventory
    modules: list[dict[str, Any]] = []
    chassis: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 2 or parts[0].lower() in ("hardware inventory:", "item"):
            continue
        name = parts[0]
        if name.lower() == "chassis":
            # The chassis row has no revision or part number.
            serial = parts[1]
            model = parts[2] if len(parts) > 2 else None
            inventory["serialNumber"] = serial
            inventory["model"] = model
            chassis.append({"name": name, "serialNumber": serial, "productId": model, "descr": model})
            continue
        if not re.match(r"^(FPC|PIC|Routing Engine|Power Supply|Fan Tray|PEM|CB|RE)\b", name, re.I):
            continue
        values = parts[1:]
        if values and re.match(r"^REV\b", values[0], re.I):
            values = values[1:]
        part_number = values[0] if values else None
        serial = values[1] if len(values) > 1 else None
        description = values[2] if len(values) > 2 else None
        modules.append({
            "name": name,
            "productId": part_number if part_number not in ("BUILTIN", "N/A") else None,
            "serialNumber": serial if serial not in ("BUILTIN", "N/A") else None,
            "descr": description,
        })
    inventory["chassis"] = chassis
    inventory["modules"] = modules
    return inventory


_ENV_ROW = re.compile(
    r"^\s*(?:(Power|Temp|Fans?)\s+)?(.+?)\s{2,}"
    r"(OK|Online|Check|Warning|Failed|Failure|Offline|Absent|Testing)"
    r"(?:\s{2,}(.*))?\s*$",
    re.I,
)


def parse_show_chassis_environment(output: str | None) -> dict[str, Any]:
    temperature = empty_temperature()
    fans = empty_fans()
    psus = empty_power_supplies()
    if not output:
        return {"temperature": temperature, "fans": fans, "powerSupplies": psus}

    category = None
    for line in output.splitlines():
        match = _ENV_ROW.match(line)
        if not match:
            continue
        row_category, name, raw_status, measurement = match.groups()
        category = row_category.lower() if row_category else category
        status = _status(raw_status)
        if category == "temp":
            degrees = re.search(r"(-?\d+(?:\.\d+)?)\s+degrees\s+C\b", measurement or "", re.I)
            temperature["sensors"].append(build_sensor(
                name=name.strip(), value=float(degrees.group(1)) if degrees else None,
                unit="C", status=status, source="ssh",
            ))
        elif category in ("fan", "fans"):
            fans["items"].append(build_fan(name=name.strip(), status=status, source="ssh"))
        elif category == "power":
            psus["items"].append(build_psu(name=name.strip(), status=status, source="ssh"))

    for group in (fans, psus):
        if group["items"]:
            group["count"] = len(group["items"])
            group["healthyCount"] = sum(item["status"] == "healthy" for item in group["items"])
            group["failedCount"] = sum(item["status"] == "critical" for item in group["items"])
    return {"temperature": temperature, "fans": fans, "powerSupplies": psus}


def parse_show_routing_engine(output: str | None) -> dict[str, Any]:
    cpu = empty_cpu()
    memory = empty_memory()
    inventory = empty_inventory()
    if not output:
        return {"cpu": cpu, "memory": memory, "inventory": inventory}

    # Prefer the active RE when a Virtual Chassis reports several members.
    sections = re.split(r"(?=^\s*Slot\s+\d+:)", output, flags=re.M)
    section = next((part for part in sections if re.search(r"(?im)^\s*Current state\s+Master\b", part)), None)
    section = section or next((part for part in sections if "Memory utilization" in part), output)
    mem = re.search(r"(?im)^\s*Memory utilization\s+(\d+(?:\.\d+)?)\s+percent", section)
    if mem:
        memory["utilizationPercent"] = float(mem.group(1))
    cpu_section = re.search(r"(?is)5\s*sec\s+CPU\s+utilization:\s*(.*?)(?=\n\s*(?:\d+\s*min|Load averages|Model|Start time|Uptime|Last reboot reason)\b|\Z)", section)
    idle_source = cpu_section.group(1) if cpu_section else section
    idle = re.search(r"(?im)^\s*Idle\s+(\d+(?:\.\d+)?)\s+percent", idle_source)
    if idle:
        cpu["utilizationPercent"] = round(100 - float(idle.group(1)), 2)
    uptime = re.search(r"(?im)^\s*Uptime\s+(.+)$", section)
    reboot = re.search(r"(?im)^\s*Last reboot reason\s+(.+)$", section)
    if uptime:
        inventory["uptime"] = uptime.group(1).strip()
    if reboot:
        inventory["bootReason"] = reboot.group(1).strip()
    return {"cpu": cpu, "memory": memory, "inventory": inventory}


def parse_show_chassis_alarms(output: str | None) -> list[dict[str, Any]]:
    if not output:
        return []
    alarms = []
    for line in output.splitlines():
        match = re.match(r"^\s*\d{4}-\d\d-\d\d\s+\d\d:\d\d:\d\d\s+\S+\s+(Major|Minor)\s+(.+)$", line, re.I)
        if match:
            alarms.append({"message": match.group(2).strip(), "severity": "critical" if match.group(1).lower() == "major" else "warning", "source": "ssh"})
    return alarms


def parse_juniper_junos_outputs(outputs: dict[str, str | None]) -> dict[str, Any]:
    version = parse_show_version(outputs.get("version"))
    hardware = parse_show_chassis_hardware(outputs.get("inventory"))
    environment = parse_show_chassis_environment(outputs.get("environment"))
    routing_engine = parse_show_routing_engine(outputs.get("routing_engine"))
    inventory = {**hardware, **{key: value for key, value in version.items() if value}}
    inventory.update({key: value for key, value in routing_engine["inventory"].items() if value})
    return {
        "inventory": inventory,
        "temperature": environment["temperature"],
        "fans": environment["fans"],
        "powerSupplies": environment["powerSupplies"],
        "cpu": routing_engine["cpu"],
        "memory": routing_engine["memory"],
        "alarms": parse_show_chassis_alarms(outputs.get("alarms")),
        "logEvidence": [],
    }
