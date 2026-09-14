"""Deterministic hardware health evaluation."""

from __future__ import annotations

from typing import Any

from services.switch_hardware import config as hw_config

STATUS_HEALTHY = "healthy"
STATUS_WARNING = "warning"
STATUS_CRITICAL = "critical"
STATUS_UNKNOWN = "unknown"
STATUS_NOT_AVAILABLE = "not_available"

_SEVERITY_ORDER = {
    STATUS_UNKNOWN: 0,
    STATUS_NOT_AVAILABLE: 1,
    STATUS_HEALTHY: 2,
    STATUS_WARNING: 3,
    STATUS_CRITICAL: 4,
}


def _threshold_status(value: float | None, warning: float, critical: float) -> str:
    if value is None:
        return STATUS_UNKNOWN
    if value >= critical:
        return STATUS_CRITICAL
    if value >= warning:
        return STATUS_WARNING
    return STATUS_HEALTHY


def evaluate_temperature(temperature: dict[str, Any]) -> dict[str, Any]:
    sensors = temperature.get("sensors") or []
    if not sensors:
        temperature["status"] = STATUS_UNKNOWN
        return temperature

    for sensor in sensors:
        status = sensor.get("status") or STATUS_UNKNOWN
        if status == STATUS_UNKNOWN and sensor.get("value") is not None:
            warning = sensor.get("warningThreshold")
            critical = sensor.get("criticalThreshold")
            if warning is not None or critical is not None:
                status = _threshold_status(
                    sensor.get("value"),
                    float(warning or 9999),
                    float(critical or 9999),
                )
                sensor["status"] = status
                sensor["thresholdSource"] = sensor.get("thresholdSource") or "device"

    temperature["status"] = _worst([s.get("status", STATUS_UNKNOWN) for s in sensors])
    return temperature


def evaluate_cpu(cpu: dict[str, Any]) -> dict[str, Any]:
    value = cpu.get("utilizationPercent")
    if value is None:
        cpu["status"] = STATUS_UNKNOWN
        return cpu
    cpu["status"] = _threshold_status(
        float(value),
        hw_config.cpu_warning_percent(),
        hw_config.cpu_critical_percent(),
    )
    return cpu


def evaluate_memory(memory: dict[str, Any]) -> dict[str, Any]:
    value = memory.get("utilizationPercent")
    if value is None:
        memory["status"] = STATUS_UNKNOWN
        return memory
    memory["status"] = _threshold_status(
        float(value),
        hw_config.memory_warning_percent(),
        hw_config.memory_critical_percent(),
    )
    return memory


def evaluate_fans(fans: dict[str, Any]) -> dict[str, Any]:
    items = fans.get("items") or []
    if not items:
        return fans
    for item in items:
        if not item.get("status") or item["status"] == STATUS_UNKNOWN:
            item["status"] = STATUS_UNKNOWN
    fans["failedCount"] = sum(1 for f in items if f.get("status") == STATUS_CRITICAL)
    fans["healthyCount"] = sum(1 for f in items if f.get("status") == STATUS_HEALTHY)
    fans["count"] = len(items)
    return fans


def evaluate_power_supplies(psus: dict[str, Any]) -> dict[str, Any]:
    items = psus.get("items") or []
    if not items:
        return psus
    psus["failedCount"] = sum(1 for p in items if p.get("status") == STATUS_CRITICAL)
    psus["healthyCount"] = sum(1 for p in items if p.get("status") == STATUS_HEALTHY)
    psus["count"] = len(items)
    return psus


def evaluate_overall_health(snapshot: dict[str, Any]) -> str:
    parts = [
        snapshot.get("temperature", {}).get("status"),
        snapshot.get("cpu", {}).get("status"),
        snapshot.get("memory", {}).get("status"),
    ]
    fans = snapshot.get("fans", {})
    if fans.get("failedCount"):
        parts.append(STATUS_CRITICAL)
    psus = snapshot.get("powerSupplies", {})
    if psus.get("failedCount"):
        parts.append(STATUS_WARNING if psus.get("count", 0) > 1 else STATUS_CRITICAL)
    alarms = snapshot.get("hardwareAlarms") or []
    if alarms:
        parts.append(STATUS_WARNING)
    return _worst([p or STATUS_UNKNOWN for p in parts])


def evaluate_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    snapshot["temperature"] = evaluate_temperature(snapshot.get("temperature") or {})
    snapshot["cpu"] = evaluate_cpu(snapshot.get("cpu") or {})
    snapshot["memory"] = evaluate_memory(snapshot.get("memory") or {})
    snapshot["fans"] = evaluate_fans(snapshot.get("fans") or {})
    snapshot["powerSupplies"] = evaluate_power_supplies(snapshot.get("powerSupplies") or {})
    snapshot["overallHealth"] = evaluate_overall_health(snapshot)
    return snapshot


def _worst(statuses: list[str]) -> str:
    best = STATUS_UNKNOWN
    for status in statuses:
        if _SEVERITY_ORDER.get(status, 0) > _SEVERITY_ORDER.get(best, 0):
            best = status
    return best
