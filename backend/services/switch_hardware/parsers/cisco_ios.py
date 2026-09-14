"""Parse Cisco IOS / IOS-XE show command output."""

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


def _status_from_text(text: str) -> str:
    lower = text.lower()
    if any(k in lower for k in ("fail", "fault", "bad", "shutdown", "critical")):
        return "critical"
    if any(k in lower for k in ("warn", "alert")):
        return "warning"
    if any(k in lower for k in ("ok", "normal", "good", "up")):
        return "healthy"
    return "unknown"


def parse_show_version(output: str | None) -> dict[str, Any]:
    inv = empty_inventory()
    if not output:
        return inv
    text = output

    m = re.search(r"^(.+?) uptime is (.+)$", text, re.MULTILINE | re.IGNORECASE)
    if m:
        inv["hostname"] = m.group(1).strip()
        inv["uptime"] = m.group(2).strip()

    for pattern in (
        r"cisco ios software.*?Version\s+([^\s,]+)",
        r"ios-xe software.*?Version\s+([^\s,]+)",
        r"system image file is.*?Version\s+([^\s,]+)",
        r"Version\s+([0-9][^\s,]+)",
    ):
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            inv["iosVersion"] = m.group(1).strip()
            inv["firmwareVersion"] = inv["iosVersion"]
            break

    m = re.search(r"Model number\s+:\s+(\S+)", text, re.IGNORECASE)
    if m:
        inv["model"] = m.group(1).strip()
        inv["productId"] = inv["model"]

    m = re.search(r"System serial number\s+:\s+(\S+)", text, re.IGNORECASE)
    if m:
        inv["serialNumber"] = m.group(1).strip()

    m = re.search(r"Last reload reason\s*:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    if m:
        inv["bootReason"] = m.group(1).strip()

    return inv


def parse_show_inventory(output: str | None) -> dict[str, Any]:
    inv = empty_inventory()
    if not output:
        return inv
    modules: list[dict[str, Any]] = []
    chassis: list[dict[str, Any]] = []
    current: dict[str, Any] = {}

    for line in output.splitlines():
        line = line.strip()
        if line.startswith("NAME:"):
            if current:
                label = f"{current.get('name', '')} {current.get('descr', '')}".lower()
                if "chassis" in label:
                    chassis.append(current)
                else:
                    modules.append(current)
            name_part = line.split(":", 1)[1].strip()
            descr_inline = None
            if ", DESCR:" in name_part:
                name_part, descr_inline = name_part.split(", DESCR:", 1)
            current = {"name": name_part.strip().strip('"')}
            if descr_inline:
                current["descr"] = descr_inline.strip().strip('"')
        elif line.startswith("DESCR:") and current is not None:
            current["descr"] = line.split(":", 1)[1].strip().strip('"')
        elif line.startswith("PID:") and current is not None:
            tail = line.split(":", 1)[1].strip()
            current["productId"] = tail.split(",")[0].strip()
            sn_match = re.search(r"SN:\s*(\S+)", line, re.IGNORECASE)
            if sn_match:
                current["serialNumber"] = sn_match.group(1).strip()
        elif line.startswith("VID:") and current is not None:
            current["vid"] = line.split(":", 1)[1].strip()
        elif line.startswith("SN:") and current is not None:
            current["serialNumber"] = line.split(":", 1)[1].strip()

    if current:
        label = f"{current.get('name', '')} {current.get('descr', '')}".lower()
        if "chassis" in label:
            chassis.append(current)
        else:
            modules.append(current)

    inv["chassis"] = chassis
    inv["modules"] = modules
    if chassis and not inv.get("serialNumber"):
        inv["serialNumber"] = chassis[0].get("serialNumber")
    if chassis and not inv.get("model"):
        inv["model"] = chassis[0].get("productId")
    if chassis and not inv.get("productId"):
        inv["productId"] = chassis[0].get("productId")
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

        temp_match = re.search(
            r"(?P<name>.+?)\s+(?:Temperature|Temp)\s*(?:Value|is|:)?\s*"
            r"(?P<value>\d+)\s*(?:C|celsius)?",
            stripped,
            re.IGNORECASE,
        )
        if not temp_match:
            temp_match = re.search(
                r"(?P<name>Inlet Temperature)\s+Value:\s*(?P<value>\d+)\s*Celsius",
                stripped,
                re.IGNORECASE,
            )
        if temp_match:
            sensors.append(
                build_sensor(
                    name=temp_match.group("name").strip(),
                    value=float(temp_match.group("value")),
                    unit="C",
                    status=_status_from_text(stripped),
                    source="ssh",
                )
            )
            continue

        fan_match = re.search(
            r"(?P<name>Fan(?:\s+\d+)?|\S+\s+Fan)\s+(?:is|:)?\s*(?P<status>\w+)",
            stripped,
            re.IGNORECASE,
        )
        if "fan" in lower and fan_match:
            rpm_match = re.search(r"(\d+)\s*rpm", stripped, re.IGNORECASE)
            fan_items.append(
                build_fan(
                    name=fan_match.group("name").strip(),
                    status=_status_from_text(fan_match.group("status")),
                    rpm=int(rpm_match.group(1)) if rpm_match else None,
                    source="ssh",
                )
            )
            continue

        psu_match = re.search(
            r"(?P<name>Power\s*Supply\s*\d+|PS\s*\d+)\s+(?:is|:)?\s*(?P<status>\w+)",
            stripped,
            re.IGNORECASE,
        )
        if ("power" in lower or "psu" in lower or "supply" in lower) and psu_match:
            psu_items.append(
                build_psu(
                    name=psu_match.group("name").strip(),
                    status=_status_from_text(psu_match.group("status")),
                    source="ssh",
                )
            )
            continue

        if any(k in lower for k in ("alarm", "alert", "failed", "critical")):
            alarms.append(
                {
                    "message": stripped,
                    "severity": "warning" if "warn" in lower else "critical",
                    "source": "ssh",
                }
            )

    if sensors:
        temp["sensors"] = sensors
        temp["status"] = max((s["status"] for s in sensors), key=_severity_rank, default="unknown")
    if fan_items:
        fans["items"] = fan_items
        fans["count"] = len(fan_items)
        fans["failedCount"] = sum(1 for f in fan_items if f["status"] == "critical")
        fans["healthyCount"] = sum(1 for f in fan_items if f["status"] == "healthy")
    if psu_items:
        psus["items"] = psu_items
        psus["count"] = len(psu_items)
        psus["failedCount"] = sum(1 for p in psu_items if p["status"] == "critical")
        psus["healthyCount"] = sum(1 for p in psu_items if p["status"] == "healthy")
        if psus["count"] and psus["count"] > 1:
            psus["redundancy"] = "redundant" if (psus["failedCount"] or 0) < psus["count"] else None

    return {"temperature": temp, "fans": fans, "powerSupplies": psus, "alarms": alarms}


def _severity_rank(status: str) -> int:
    return {"unknown": 0, "healthy": 1, "warning": 2, "critical": 3}.get(status, 0)


def parse_show_processes_cpu(output: str | None) -> dict[str, Any]:
    cpu = empty_cpu()
    if not output:
        return cpu
    m = re.search(
        r"CPU utilization for five seconds:\s*(\d+)%",
        output,
        re.IGNORECASE,
    )
    if not m:
        m = re.search(r"five minutes:\s*(\d+)%", output, re.IGNORECASE)
    if m:
        cpu["utilizationPercent"] = float(m.group(1))
    return cpu


def parse_show_processes_memory(output: str | None) -> dict[str, Any]:
    mem = empty_memory()
    if not output:
        return mem
    m = re.search(
        r"Processor Pool Total:\s*(\d+).*Used:\s*(\d+)",
        output,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        total = float(m.group(1))
        used = float(m.group(2))
        if total > 0:
            mem["utilizationPercent"] = round((used / total) * 100, 2)
        return mem
    m = re.search(r"Used:\s*(\d+).*Free:\s*(\d+)", output, re.IGNORECASE | re.DOTALL)
    if m:
        used = float(m.group(1))
        free = float(m.group(2))
        total = used + free
        if total > 0:
            mem["utilizationPercent"] = round((used / total) * 100, 2)
    return mem


def parse_show_logging(output: str | None, *, max_lines: int = 50) -> list[dict[str, Any]]:
    if not output:
        return []
    keywords = (
        "thermal",
        "temperature",
        "overheat",
        "power",
        "psu",
        "fan",
        "reload",
        "crash",
        "watchdog",
        "failure",
        "environment",
    )
    events: list[dict[str, Any]] = []
    for line in output.splitlines():
        lower = line.lower()
        if any(k in lower for k in keywords):
            events.append({"message": line.strip(), "source": "ssh"})
        if len(events) >= max_lines:
            break
    return events


def parse_cisco_ios_outputs(outputs: dict[str, str | None]) -> dict[str, Any]:
    version = parse_show_version(outputs.get("version"))
    inventory = parse_show_inventory(outputs.get("inventory"))
    env = parse_show_environment(outputs.get("environment") or outputs.get("environment_all"))
    cpu = parse_show_processes_cpu(outputs.get("processes_cpu"))
    memory = parse_show_processes_memory(outputs.get("processes_memory"))
    logs = parse_show_logging(outputs.get("logging"))

    merged_inventory = {**inventory, **{k: v for k, v in version.items() if v}}
    for key in ("model", "serialNumber", "productId", "iosVersion", "firmwareVersion"):
        if not merged_inventory.get(key) and inventory.get(key):
            merged_inventory[key] = inventory[key]

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
