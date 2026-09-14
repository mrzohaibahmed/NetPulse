"""Parse Cisco NX-OS show command output."""

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
from services.switch_hardware.parsers.cisco_ios import (
    parse_show_logging,
    parse_show_processes_cpu,
)


def parse_show_version(output: str | None) -> dict[str, Any]:
    inv = empty_inventory()
    if not output:
        return inv
    m = re.search(r"Device name:\s*(\S+)", output, re.IGNORECASE)
    if m:
        inv["hostname"] = m.group(1)
    m = re.search(r"system:\s*version\s*(\S+)", output, re.IGNORECASE)
    if m:
        inv["iosVersion"] = m.group(1)
        inv["firmwareVersion"] = m.group(1)
    m = re.search(r"NXOS:\s*version\s*(\S+)", output, re.IGNORECASE)
    if m:
        inv["iosVersion"] = m.group(1)
        inv["firmwareVersion"] = m.group(1)
    m = re.search(r"Kernel uptime is\s*(.+)$", output, re.MULTILINE | re.IGNORECASE)
    if m:
        inv["uptime"] = m.group(1).strip()
    m = re.search(r"Reason:\s*(.+)$", output, re.MULTILINE | re.IGNORECASE)
    if m:
        inv["bootReason"] = m.group(1).strip()
    return inv


def parse_show_inventory(output: str | None) -> dict[str, Any]:
    inv = empty_inventory()
    if not output:
        return inv
    modules: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("NAME:"):
            if current:
                modules.append(current)
            current = {"name": stripped.split(":", 1)[1].strip().strip('"')}
        elif stripped.startswith("PID:") and current:
            current["productId"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("SN:") and current:
            current["serialNumber"] = stripped.split(":", 1)[1].strip()
    if current:
        modules.append(current)
    inv["modules"] = modules
    if modules:
        inv["model"] = modules[0].get("productId")
        inv["productId"] = modules[0].get("productId")
        inv["serialNumber"] = modules[0].get("serialNumber")
    return inv


def parse_show_environment(output: str | None) -> dict[str, Any]:
    temp = empty_temperature()
    fans = empty_fans()
    psus = empty_power_supplies()
    alarms: list[dict[str, Any]] = []
    if not output:
        return {"temperature": temp, "fans": fans, "powerSupplies": psus, "alarms": alarms}

    sensors: list[dict[str, Any]] = []
    fan_items: list[dict[str, Any]] = []
    psu_items: list[dict[str, Any]] = []

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()

        if "module" in lower and "temp" in lower:
            m = re.search(r"(\d+)\s*(?:C|celsius)", stripped, re.IGNORECASE)
            if m:
                sensors.append(
                    build_sensor(
                        name=stripped.split()[0] if stripped.split() else "Temperature",
                        value=float(m.group(1)),
                        unit="C",
                        status="unknown",
                        source="ssh",
                    )
                )

        if "fan" in lower:
            status = "critical" if "fail" in lower else ("healthy" if "ok" in lower else "unknown")
            rpm_match = re.search(r"(\d+)\s*rpm", stripped, re.IGNORECASE)
            fan_items.append(
                build_fan(
                    name=stripped[:40],
                    status=status,
                    rpm=int(rpm_match.group(1)) if rpm_match else None,
                    source="ssh",
                )
            )

        if "power" in lower or "psu" in lower:
            status = "critical" if "fail" in lower else ("healthy" if "ok" in lower else "unknown")
            psu_items.append(
                build_psu(name=stripped[:40], status=status, source="ssh")
            )

        if any(k in lower for k in ("alarm", "failed", "critical")):
            alarms.append({"message": stripped, "severity": "critical", "source": "ssh"})

    if sensors:
        temp["sensors"] = sensors
    if fan_items:
        fans["items"] = fan_items
        fans["count"] = len(fan_items)
    if psu_items:
        psus["items"] = psu_items
        psus["count"] = len(psu_items)

    return {"temperature": temp, "fans": fans, "powerSupplies": psus, "alarms": alarms}


def parse_show_processes_memory(output: str | None) -> dict[str, Any]:
    mem = empty_memory()
    if not output:
        return mem
    m = re.search(r"Total\s+(\d+).*Used\s+(\d+)", output, re.IGNORECASE | re.DOTALL)
    if m:
        total = float(m.group(1))
        used = float(m.group(2))
        if total > 0:
            mem["utilizationPercent"] = round((used / total) * 100, 2)
    return mem


def parse_cisco_nxos_outputs(outputs: dict[str, str | None]) -> dict[str, Any]:
    version = parse_show_version(outputs.get("version"))
    inventory = parse_show_inventory(outputs.get("inventory"))
    env = parse_show_environment(outputs.get("environment") or outputs.get("environment_all"))
    cpu = parse_show_processes_cpu(outputs.get("processes_cpu"))
    memory = parse_show_processes_memory(outputs.get("processes_memory"))
    logs = parse_show_logging(outputs.get("logging"))

    merged_inventory = {**inventory, **{k: v for k, v in version.items() if v}}
    return {
        "inventory": merged_inventory,
        "temperature": env["temperature"],
        "fans": env["fans"],
        "powerSupplies": env["powerSupplies"],
        "cpu": cpu,
        "memory": memory,
        "alarms": env["alarms"],
        "logEvidence": logs,
    }
