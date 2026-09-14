"""Normalized document builders for switch hardware monitoring."""

from __future__ import annotations

from typing import Any

from utils.utc import utc_now


NOT_AVAILABLE = "Not available"


def _na(value: Any) -> Any:
    if value is None or value == "":
        return None
    return value


def empty_temperature() -> dict[str, Any]:
    return {
        "status": "unknown",
        "sensors": [],
    }


def empty_fans() -> dict[str, Any]:
    return {
        "count": None,
        "healthyCount": None,
        "failedCount": None,
        "items": [],
    }


def empty_power_supplies() -> dict[str, Any]:
    return {
        "count": None,
        "healthyCount": None,
        "failedCount": None,
        "redundancy": None,
        "items": [],
    }


def empty_cpu() -> dict[str, Any]:
    return {
        "utilizationPercent": None,
        "status": "unknown",
    }


def empty_memory() -> dict[str, Any]:
    return {
        "utilizationPercent": None,
        "status": "unknown",
    }


def empty_inventory() -> dict[str, Any]:
    return {
        "model": None,
        "serialNumber": None,
        "productId": None,
        "firmwareVersion": None,
        "iosVersion": None,
        "hostname": None,
        "uptime": None,
        "bootReason": None,
        "chassis": [],
        "modules": [],
    }


def build_sensor(
    *,
    name: str,
    sensor_type: str = "temperature",
    value: float | None = None,
    unit: str | None = None,
    status: str = "unknown",
    warning_threshold: float | None = None,
    critical_threshold: float | None = None,
    threshold_source: str | None = None,
    source: str = "unknown",
    available: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "sensorType": sensor_type,
        "value": value,
        "unit": unit,
        "status": status,
        "warningThreshold": warning_threshold,
        "criticalThreshold": critical_threshold,
        "thresholdSource": threshold_source,
        "source": source,
        "available": available,
    }


def build_fan(
    *,
    name: str,
    status: str = "unknown",
    rpm: int | None = None,
    source: str = "unknown",
    available: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "speedRpm": rpm,
        "source": source,
        "available": available,
    }


def build_psu(
    *,
    name: str,
    status: str = "unknown",
    input_status: str | None = None,
    output_status: str | None = None,
    source: str = "unknown",
    available: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "inputStatus": input_status,
        "outputStatus": output_status,
        "source": source,
        "available": available,
    }


def build_current_document(
    device_id,
    *,
    vendor: str = "Cisco",
    platform: str | None = None,
    collection_status: str = "unknown",
    overall_health: str = "unknown",
    inventory: dict[str, Any] | None = None,
    temperature: dict[str, Any] | None = None,
    fans: dict[str, Any] | None = None,
    power_supplies: dict[str, Any] | None = None,
    cpu: dict[str, Any] | None = None,
    memory: dict[str, Any] | None = None,
    alarms: list[dict[str, Any]] | None = None,
    availability: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    last_error: str | None = None,
    last_successful_collection_at=None,
    last_attempted_collection_at=None,
) -> dict[str, Any]:
    now = utc_now()
    return {
        "deviceId": device_id,
        "vendor": vendor,
        "platform": platform,
        "overallHealth": overall_health,
        "collectionStatus": collection_status,
        "inventory": inventory or empty_inventory(),
        "temperature": temperature or empty_temperature(),
        "fans": fans or empty_fans(),
        "powerSupplies": power_supplies or empty_power_supplies(),
        "cpu": cpu or empty_cpu(),
        "memory": memory or empty_memory(),
        "hardwareAlarms": alarms or [],
        "availability": availability or {
            "snmp": "unknown",
            "ssh": "unknown",
        },
        "evidence": evidence or {},
        "lastSuccessfulCollectionAt": last_successful_collection_at,
        "lastAttemptedCollectionAt": last_attempted_collection_at or now,
        "lastError": last_error,
        "updatedAt": now,
    }


def build_history_document(device_id, snapshot: dict[str, Any], *, source: str) -> dict[str, Any]:
    return {
        "deviceId": device_id,
        "timestamp": utc_now(),
        "overallHealth": snapshot.get("overallHealth"),
        "collectionStatus": snapshot.get("collectionStatus"),
        "platform": snapshot.get("platform"),
        "temperature": snapshot.get("temperature"),
        "fans": snapshot.get("fans"),
        "powerSupplies": snapshot.get("powerSupplies"),
        "cpu": snapshot.get("cpu"),
        "memory": snapshot.get("memory"),
        "hardwareAlarms": snapshot.get("hardwareAlarms") or [],
        "source": source,
    }


def build_event_document(
    device_id,
    *,
    event_type: str,
    severity: str,
    component: str,
    description: str,
    source: str,
    resolved: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "deviceId": device_id,
        "eventType": event_type,
        "severity": severity,
        "component": component,
        "description": description,
        "source": source,
        "resolved": resolved,
        "metadata": metadata or {},
        "timestamp": utc_now(),
    }


def build_outage_document(
    device_id,
    *,
    started_at,
    status: str = "active",
    evidence: dict[str, Any] | None = None,
    last_known_hardware: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = utc_now()
    return {
        "deviceId": device_id,
        "startedAt": started_at,
        "endedAt": None,
        "durationSeconds": None,
        "status": status,
        "evidence": evidence or {},
        "lastKnownHardware": last_known_hardware or {},
        "rootCause": "unknown",
        "confidence": "unknown",
        "confirmed": False,
        "timeline": [],
        "createdAt": now,
        "updatedAt": now,
    }
