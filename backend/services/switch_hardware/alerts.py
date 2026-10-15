"""Hardware alert integration with existing alert_service patterns."""

from __future__ import annotations

from typing import Any

from pymongo.errors import DuplicateKeyError

from config.database import db
from services.switch_hardware import config as hw_config
from services.email_service import (
    send_switch_hardware_alert_email,
    send_switch_hardware_recovery_alert_email,
)
from services.mongo_retry import assert_insert_acknowledged
from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("switch_hardware.alerts")

ALERT_TYPE_HARDWARE = "Switch Hardware"
CATEGORY_HARDWARE = "Switch Hardware Monitoring"


def _normalize_device_id(device_id):
    if device_id is None:
        return None
    from bson import ObjectId

    if isinstance(device_id, ObjectId):
        return device_id
    if isinstance(device_id, str) and ObjectId.is_valid(device_id):
        return ObjectId(device_id)
    return device_id


def _build_hardware_alert_doc(
    device: dict,
    *,
    title: str,
    message: str,
    severity: str,
    hardware_alert_key: str,
) -> dict[str, Any]:
    now = utc_now()
    return {
        "deviceId": _normalize_device_id(device.get("_id")),
        "hostname": device.get("hostname", "unknown"),
        "ipAddress": device.get("ipAddress", "unknown"),
        "deviceType": device.get("deviceType") or device.get("type"),
        "alertType": ALERT_TYPE_HARDWARE,
        "category": CATEGORY_HARDWARE,
        "title": title,
        "message": message,
        "severity": severity,
        "hardwareAlertKey": hardware_alert_key,
        "emailSent": False,
        "recoveryEmailSent": False,
        "acknowledged": False,
        "dismissed": False,
        "resolved": False,
        "createdAt": now,
        "updatedAt": now,
    }


def claim_hardware_alert(device: dict, *, title: str, message: str, severity: str, key: str) -> bool:
    """
    Create the dashboard alert, then best-effort send the notification email.

    Dedup via the partial unique index on (deviceId, hardwareAlertKey) — a
    DuplicateKeyError means an active alert already exists, so no second
    email goes out for the same ongoing condition. Mirrors the existing
    claim -> insert -> send email -> mark emailSent pattern used for ISP
    offline alerts (services/isp_alert_service.py).
    """
    doc = _build_hardware_alert_doc(
        device,
        title=title,
        message=message,
        severity=severity,
        hardware_alert_key=key,
    )
    try:
        result = db.alerts.insert_one(doc)
        assert_insert_acknowledged(result, action="hardware_alert_insert")
    except DuplicateKeyError:
        # Keep an ongoing alert's details current without creating another
        # alert or sending another notification email.
        try:
            db.alerts.update_one(
                {
                    "deviceId": doc["deviceId"],
                    "hardwareAlertKey": key,
                    "alertType": ALERT_TYPE_HARDWARE,
                    "resolved": False,
                    "dismissed": False,
                    "message": {"$ne": message},
                },
                {"$set": {
                    "title": title,
                    "message": message,
                    "severity": severity,
                    "updatedAt": utc_now(),
                }},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to refresh hardware alert details | device=%s | key=%s | %s",
                device.get("_id"), key, exc,
            )
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("Hardware alert claim failed | device=%s | %s", device.get("_id"), exc)
        return False

    email_sent = False
    try:
        email_sent = send_switch_hardware_alert_email(device, doc)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Hardware alert email failed | device=%s | key=%s | %s",
            device.get("_id"),
            key,
            exc,
        )

    if email_sent:
        try:
            db.alerts.update_one({"_id": result.inserted_id}, {"$set": {"emailSent": True}})
            logger.info(
                "Hardware alert email sent | device=%s | key=%s", device.get("_id"), key
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to update hardware alert emailSent | %s", exc)

    return True


def resolve_hardware_alert(device: dict, hardware_alert_key: str) -> None:
    """
    Resolve active alerts for this key and send one recovery email each.

    Takes the full device (not just an id) so the recovery email can include
    hostname/IP, matching send_isp_recovery_alert / send_critical_device_recovery_alert.
    """
    device_id = _normalize_device_id(device.get("_id"))
    active_filter = {
        "deviceId": device_id,
        "hardwareAlertKey": hardware_alert_key,
        "resolved": False,
        "dismissed": False,
    }
    active_alerts = list(db.alerts.find(active_filter))
    if not active_alerts:
        return

    db.alerts.update_many(active_filter, {"$set": {"resolved": True, "updatedAt": utc_now()}})

    for alert in active_alerts:
        recovery_sent = False
        try:
            recovery_sent = send_switch_hardware_recovery_alert_email(device, alert)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Hardware recovery email failed | device=%s | key=%s | %s",
                device.get("_id"),
                hardware_alert_key,
                exc,
            )
        if recovery_sent:
            try:
                db.alerts.update_one(
                    {"_id": alert["_id"]}, {"$set": {"recoveryEmailSent": True}}
                )
                logger.info(
                    "Hardware recovery email sent | device=%s | key=%s | alertId=%s",
                    device.get("_id"),
                    hardware_alert_key,
                    alert.get("_id"),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to update hardware alert recoveryEmailSent | %s", exc)


def _active_alert_keys(snapshot: dict[str, Any]) -> set[str]:
    active: set[str] = set()
    temp_status = (snapshot.get("temperature") or {}).get("status")
    if temp_status == "critical":
        active.add("temperature_critical")
    elif temp_status == "warning":
        active.add("temperature_warning")

    if (snapshot.get("fans") or {}).get("failedCount"):
        active.add("fan_failed")
    if (snapshot.get("powerSupplies") or {}).get("failedCount"):
        active.add("psu_failed")

    cpu_status = (snapshot.get("cpu") or {}).get("status")
    if cpu_status == "critical":
        active.add("cpu_critical")
    elif cpu_status == "warning":
        active.add("cpu_warning")

    mem_status = (snapshot.get("memory") or {}).get("status")
    if mem_status == "critical":
        active.add("memory_critical")
    elif mem_status == "warning":
        active.add("memory_warning")

    if snapshot.get("hardwareAlarms"):
        active.add("hardware_alarm")
    return active


def _reading(value: Any, unit: str = "") -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:g}{unit}"
    return f"{value}{unit}"


def _temperature_unit(unit: str) -> str:
    return f" °{unit}" if unit.upper() in {"C", "F"} else f" {unit}"


def _affected_lines(items: list[dict], describe) -> list[str]:
    affected = [item for item in items if item.get("status") == "critical"]
    lines = [describe(item, index) for index, item in enumerate(affected[:10], 1)]
    if len(affected) > 10:
        lines.append(f"...and {len(affected) - 10} more affected components.")
    return lines


def _hardware_alert_message(device: dict, snapshot: dict[str, Any], key: str, title: str) -> str:
    hostname = device.get("hostname") or "switch"
    prefix = f"{title} on {hostname}."
    if key.startswith("temperature_"):
        status = key.removeprefix("temperature_")
        sensors = (snapshot.get("temperature") or {}).get("sensors") or []
        lines = []
        affected = [sensor for sensor in sensors if sensor.get("status") == status]
        for index, sensor in enumerate(affected[:10], 1):
            name = sensor.get("name") or f"Temperature sensor {index}"
            value = sensor.get("value")
            unit = sensor.get("unit") or "C"
            detail = f"{name}: {status}"
            if value is not None:
                detail += f", reading {_reading(value, _temperature_unit(unit))}."
            else:
                detail += "; reading unavailable."
            threshold = sensor.get(f"{status}Threshold")
            if threshold is not None:
                detail += f" {status.capitalize()} threshold: {_reading(threshold, _temperature_unit(unit))}."
            lines.append(detail)
        if len(affected) > 10:
            lines.append(f"...and {len(affected) - 10} more affected sensors.")
        fallback = f"Temperature status: {status}; affected sensor details unavailable."
        return "\n".join([prefix, *(lines or [fallback])])

    if key == "fan_failed":
        fans = snapshot.get("fans") or {}

        def describe_fan(item, index):
            name = item.get("name") or f"Fan {index}"
            speed = item.get("speedRpm")
            detail = f"{name}: failed"
            if speed is not None:
                detail += f", speed {_reading(speed, ' RPM')}"
            return detail + "."

        lines = _affected_lines(
            fans.get("items") or [],
            describe_fan,
        )
        fallback = f"Failed fans: {fans.get('failedCount')}; individual fan details unavailable."
        return "\n".join([prefix, *(lines or [fallback])])

    if key == "psu_failed":
        psus = snapshot.get("powerSupplies") or {}

        def describe_psu(item, index):
            name = item.get("name") or f"Power supply {index}"
            details = [f"{name}: failed"]
            for field, label in (("inputStatus", "input"), ("outputStatus", "output")):
                if item.get(field):
                    details.append(f"{label} {item[field]}")
            return ", ".join(details) + "."

        lines = _affected_lines(psus.get("items") or [], describe_psu)
        fallback = (
            f"Failed power supplies: {psus.get('failedCount')}; "
            "individual PSU details unavailable."
        )
        return "\n".join([prefix, *(lines or [fallback])])

    if key.startswith("cpu_") or key.startswith("memory_"):
        component, status = key.split("_", 1)
        info = snapshot.get(component) or {}
        value = info.get("utilizationPercent")
        thresholds = {
            ("cpu", "critical"): hw_config.cpu_critical_percent,
            ("cpu", "warning"): hw_config.cpu_warning_percent,
            ("memory", "critical"): hw_config.memory_critical_percent,
            ("memory", "warning"): hw_config.memory_warning_percent,
        }
        threshold = thresholds[(component, status)]()
        detail = f"{component.upper() if component == 'cpu' else 'Memory'} utilization: "
        if value is not None:
            detail += (
                f"{_reading(value, '%')} ({status}; "
                f"configured threshold {_reading(threshold, '%')})."
            )
        else:
            detail += f"{status}; utilization reading unavailable."
        return f"{prefix}\n{detail}"

    if key == "hardware_alarm":
        alarms = snapshot.get("hardwareAlarms") or []
        lines = []
        for alarm in alarms[:10]:
            severity = str(alarm.get("severity") or "alarm").capitalize()
            description = str(alarm.get("message") or "Description unavailable").rstrip(". ")
            lines.append(f"{severity}: {description}.")
        if len(alarms) > 10:
            lines.append(f"...and {len(alarms) - 10} more hardware alarms.")
        return "\n".join([prefix, *lines])

    return prefix


def evaluate_hardware_alerts(device: dict, snapshot: dict[str, Any]) -> None:
    mapping = {
        "temperature_critical": ("Temperature Critical", "CRITICAL", "temperature_critical"),
        "temperature_warning": ("Temperature Warning", "WARNING", "temperature_warning"),
        "fan_failed": ("Fan Failure", "CRITICAL", "fan_failed"),
        "psu_failed": ("PSU Failure", "CRITICAL", "psu_failed"),
        "cpu_critical": ("CPU Critical", "CRITICAL", "cpu_critical"),
        "cpu_warning": ("CPU Warning", "WARNING", "cpu_warning"),
        "memory_critical": ("Memory Critical", "CRITICAL", "memory_critical"),
        "memory_warning": ("Memory Warning", "WARNING", "memory_warning"),
        "hardware_alarm": ("Hardware Alarm", "WARNING", "hardware_alarm"),
    }
    active = _active_alert_keys(snapshot)
    for key, (title, severity, alert_key) in mapping.items():
        if key in active:
            claim_hardware_alert(
                device,
                title=title,
                message=_hardware_alert_message(device, snapshot, key, title),
                severity=severity,
                key=alert_key,
            )
        else:
            resolve_hardware_alert(device, alert_key)
