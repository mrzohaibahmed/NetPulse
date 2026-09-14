"""Hardware event detection with deduplication."""

from __future__ import annotations

from typing import Any

from bson import ObjectId

from config.database import db
from services.switch_hardware.health_evaluator import (
    STATUS_CRITICAL,
    STATUS_HEALTHY,
    STATUS_WARNING,
)
from services.switch_hardware.models import build_event_document
from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("switch_hardware.events")

ACTIVE_EVENTS: dict[str, tuple[str, str]] = {
    "temperature_warning": ("temperature_warning", "temperature_recovered"),
    "temperature_critical": ("temperature_critical", "temperature_recovered"),
    "fan_failed": ("fan_failed", "fan_recovered"),
    "psu_failed": ("psu_failed", "psu_recovered"),
    "cpu_warning": ("cpu_warning", "cpu_recovered"),
    "cpu_critical": ("cpu_critical", "cpu_recovered"),
    "memory_warning": ("memory_warning", "memory_recovered"),
    "memory_critical": ("memory_critical", "memory_recovered"),
    "hardware_alarm": ("hardware_alarm", "hardware_alarm_recovered"),
}


def _active_key(device_id, event_type: str, component: str) -> dict[str, Any]:
    return {
        "deviceId": device_id,
        "eventType": event_type,
        "component": component,
        "resolved": False,
    }


def _insert_event(device_id, **kwargs) -> None:
    doc = build_event_document(device_id, **kwargs)
    db.switch_hardware_events.insert_one(doc)


def _resolve_active(device_id, event_type: str, component: str) -> None:
    db.switch_hardware_events.update_many(
        _active_key(device_id, event_type, component),
        {"$set": {"resolved": True, "resolvedAt": utc_now()}},
    )


def _ensure_event(
    device_id,
    *,
    active_type: str,
    recovery_type: str,
    component: str,
    severity: str,
    description: str,
    source: str,
    active: bool,
) -> None:
    if active:
        existing = db.switch_hardware_events.find_one(
            _active_key(device_id, active_type, component)
        )
        if existing:
            return
        _insert_event(
            device_id,
            event_type=active_type,
            severity=severity,
            component=component,
            description=description,
            source=source,
        )
        return

    existing = db.switch_hardware_events.find_one(
        _active_key(device_id, active_type, component)
    )
    if not existing:
        return
    _resolve_active(device_id, active_type, component)
    _insert_event(
        device_id,
        event_type=recovery_type,
        severity="info",
        component=component,
        description=f"{component} recovered",
        source=source,
        resolved=True,
    )


def detect_hardware_events(
    device_id,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    source: str = "collector",
) -> list[str]:
    if isinstance(device_id, str) and ObjectId.is_valid(device_id):
        device_id = ObjectId(device_id)

    generated: list[str] = []

    for sensor in current.get("temperature", {}).get("sensors") or []:
        name = sensor.get("name") or "temperature"
        status = sensor.get("status") or "unknown"
        _ensure_event(
            device_id,
            active_type="temperature_critical",
            recovery_type="temperature_recovered",
            component=name,
            severity="critical",
            description=f"Temperature critical on {name}",
            source=source,
            active=status == STATUS_CRITICAL,
        )
        _ensure_event(
            device_id,
            active_type="temperature_warning",
            recovery_type="temperature_recovered",
            component=name,
            severity="warning",
            description=f"Temperature warning on {name}",
            source=source,
            active=status == STATUS_WARNING,
        )
        if status == STATUS_CRITICAL:
            generated.append("temperature_critical")
        elif status == STATUS_WARNING:
            generated.append("temperature_warning")

    for fan in current.get("fans", {}).get("items") or []:
        name = fan.get("name") or "fan"
        status = fan.get("status") or "unknown"
        _ensure_event(
            device_id,
            active_type="fan_failed",
            recovery_type="fan_recovered",
            component=name,
            severity="critical",
            description=f"Fan failure on {name}",
            source=source,
            active=status == STATUS_CRITICAL,
        )
        if status == STATUS_CRITICAL:
            generated.append("fan_failed")

    for psu in current.get("powerSupplies", {}).get("items") or []:
        name = psu.get("name") or "psu"
        status = psu.get("status") or "unknown"
        _ensure_event(
            device_id,
            active_type="psu_failed",
            recovery_type="psu_recovered",
            component=name,
            severity="critical",
            description=f"Power supply failure on {name}",
            source=source,
            active=status == STATUS_CRITICAL,
        )
        if status == STATUS_CRITICAL:
            generated.append("psu_failed")

    cpu_status = current.get("cpu", {}).get("status") or "unknown"
    _ensure_event(
        device_id,
        active_type="cpu_critical",
        recovery_type="cpu_recovered",
        component="cpu",
        severity="critical",
        description="CPU utilization critical",
        source=source,
        active=cpu_status == STATUS_CRITICAL,
    )
    _ensure_event(
        device_id,
        active_type="cpu_warning",
        recovery_type="cpu_recovered",
        component="cpu",
        severity="warning",
        description="CPU utilization warning",
        source=source,
        active=cpu_status == STATUS_WARNING,
    )

    mem_status = current.get("memory", {}).get("status") or "unknown"
    _ensure_event(
        device_id,
        active_type="memory_critical",
        recovery_type="memory_recovered",
        component="memory",
        severity="critical",
        description="Memory utilization critical",
        source=source,
        active=mem_status == STATUS_CRITICAL,
    )
    _ensure_event(
        device_id,
        active_type="memory_warning",
        recovery_type="memory_recovered",
        component="memory",
        severity="warning",
        description="Memory utilization warning",
        source=source,
        active=mem_status == STATUS_WARNING,
    )

    for alarm in current.get("hardwareAlarms") or []:
        message = alarm.get("message") or "hardware alarm"
        _ensure_event(
            device_id,
            active_type="hardware_alarm",
            recovery_type="hardware_alarm_recovered",
            component=message[:80],
            severity=alarm.get("severity") or "warning",
            description=message,
            source=source,
            active=True,
        )

    return generated
