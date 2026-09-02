"""ISP offline/recovery alerting — reuses alerts collection and SMTP service."""

from __future__ import annotations

from typing import Any, Optional

from models.isp_connection import STATUS_OFFLINE
from services.alert_service import CRITICAL_OFFLINE_ALERT_THRESHOLD, GENERATED_BY_SYSTEM
from services.email_service import send_isp_offline_alert, send_isp_recovery_alert
from services.mongo_retry import (
    assert_insert_acknowledged,
    assert_update_acknowledged,
    with_mongo_retry,
)
from services.monitor_events import EVENT_ALERT_CREATED, EVENT_ALERT_RESOLVED, publish
from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("isp.alert")

ISP_OFFLINE_ALERT_TYPE = "ISP Offline"
ISP_MONITORING_CATEGORY = "ISP Monitoring"
ISP_ENTITY_TYPE = "ISP"


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def isp_has_monitoring_target(isp: dict | None) -> bool:
    """True when the ISP has a non-empty ping target configured."""
    if not isp:
        return False
    return bool((isp.get("target") or "").strip())


def _normalize_isp_id(isp: dict) -> str | None:
    """Return the ISP document id as a string, or None when missing."""
    isp_id = isp.get("_id")
    if isp_id is None:
        return None
    return str(isp_id)


def _active_isp_offline_filter(isp_id: str) -> dict[str, Any]:
    return {
        "ispId": isp_id,
        "alertType": ISP_OFFLINE_ALERT_TYPE,
        "resolved": False,
        "dismissed": False,
    }


def maybe_send_isp_offline_alert(
    isp: dict,
    *,
    consecutive_failures: int,
    scan_type: str = "Automatic",
    cycle_id: str | None = None,
    attempt_id: str | None = None,
) -> bool:
    """
    Create one ISP offline alert + email when failures reach the shared threshold.

    Idempotent while an active incident exists for this ISP.
    """
    from pymongo.errors import DuplicateKeyError  # noqa: PLC0415

    if not isp_has_monitoring_target(isp):
        return False

    failures = int(consecutive_failures or 0)
    if failures < CRITICAL_OFFLINE_ALERT_THRESHOLD:
        return False

    isp_id = _normalize_isp_id(isp)
    if isp_id is None:
        return False

    isp_name = isp.get("name", "unknown")
    location = isp.get("location") or "Unknown"
    target = (isp.get("target") or "").strip()

    existing = _db().alerts.find_one(_active_isp_offline_filter(isp_id), {"_id": 1})
    if existing:
        logger.info(
            "[ISP ALERT] active incident exists — skip duplicate | isp=%s | location=%s | "
            "failures=%s | alertId=%s",
            isp_name,
            location,
            failures,
            existing.get("_id"),
        )
        return False

    logger.warning(
        "[ISP ALERT] threshold reached | isp=%s | location=%s | failures=%s | "
        "cycleId=%s | attemptId=%s",
        isp_name,
        location,
        failures,
        cycle_id,
        attempt_id,
    )

    email_sent = False
    try:
        email_sent = send_isp_offline_alert(isp, consecutive_failures=failures)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[ISP ALERT] offline email failed | isp=%s | location=%s | error=%s",
            isp_name,
            location,
            exc,
        )

    now = utc_now()
    message = (
        f"ISP {isp_name} ({location}) is offline — target {target} unreachable via "
        f"{scan_type} scan (consecutiveFailures={failures})."
    )
    doc = {
        "ispId": isp_id,
        "entityType": ISP_ENTITY_TYPE,
        "hostname": isp_name,
        "deviceName": isp_name,
        "ipAddress": target,
        "location": location,
        "status": STATUS_OFFLINE,
        "title": f"ISP Offline — {isp_name}",
        "message": message,
        "scanType": scan_type,
        "alertType": ISP_OFFLINE_ALERT_TYPE,
        "category": ISP_MONITORING_CATEGORY,
        "severity": "CRITICAL",
        "generatedBy": GENERATED_BY_SYSTEM,
        "emailSent": bool(email_sent),
        "recoveryEmailSent": False,
        "acknowledged": False,
        "dismissed": False,
        "resolved": False,
        "acknowledgedAt": None,
        "dismissedAt": None,
        "resolvedAt": None,
        "resolvedBy": None,
        "resolvedReason": None,
        "createdAt": now,
        "cycleId": cycle_id,
        "attemptId": attempt_id,
        "consecutiveFailuresAtAlert": failures,
    }

    def _insert_or_existing():
        try:
            return _db().alerts.insert_one(doc)
        except DuplicateKeyError:
            logger.info(
                "[ISP ALERT] insert idempotent (DuplicateKey) | isp=%s | location=%s",
                isp_name,
                location,
            )
            return None

    try:
        result = with_mongo_retry(
            _insert_or_existing,
            action="isp_offline_alert_insert",
            device_id=isp_id,
            ip_address=target,
            idempotent=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[ISP ALERT] failed to insert offline alert | isp=%s | error=%s",
            isp_name,
            exc,
        )
        return False

    if result is None:
        return False

    try:
        assert_insert_acknowledged(
            result,
            action="isp_offline_alert_insert",
            device_id=isp_id,
            ip_address=target,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[ISP ALERT] insert acknowledgement failed | isp=%s | error=%s",
            isp_name,
            exc,
        )
        return False

    if email_sent:
        logger.info("[ISP ALERT] offline email sent | isp=%s | location=%s", isp_name, location)

    publish(
        EVENT_ALERT_CREATED,
        {
            "ispId": isp_id,
            "hostname": isp_name,
            "location": location,
            "status": STATUS_OFFLINE,
            "alertId": str(result.inserted_id),
            "cycleId": cycle_id,
            "attemptId": attempt_id,
        },
    )
    return True


def resolve_isp_offline_alerts(
    isp: dict,
    *,
    scan_type: str = "Automatic",
    cycle_id: str | None = None,
) -> int:
    """
    Resolve active ISP offline alerts when the ISP recovers.

    Sends one recovery email per resolved incident. Never raises.
    """
    if not isp_has_monitoring_target(isp):
        return 0

    isp_id = _normalize_isp_id(isp)
    if isp_id is None:
        return 0

    isp_name = isp.get("name", "unknown")
    location = isp.get("location") or "Unknown"
    alert_filter = _active_isp_offline_filter(isp_id)
    active_alerts = list(_db().alerts.find(alert_filter))
    if not active_alerts:
        return 0

    now = utc_now()

    def _update():
        return _db().alerts.update_many(
            alert_filter,
            {
                "$set": {
                    "resolved": True,
                    "resolvedAt": now,
                    "resolvedBy": GENERATED_BY_SYSTEM,
                    "resolvedReason": "ISP recovered to Online",
                }
            },
        )

    try:
        result = with_mongo_retry(
            _update,
            action="isp_offline_alert_resolve",
            device_id=isp_id,
            ip_address=(isp.get("target") or "").strip(),
            idempotent=True,
        )
        assert_update_acknowledged(
            result,
            action="isp_offline_alert_resolve",
            device_id=isp_id,
            ip_address=(isp.get("target") or "").strip(),
            require_matched=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[ISP ALERT] failed to resolve offline alerts | isp=%s | error=%s",
            isp_name,
            exc,
        )
        return 0

    modified = int(result.modified_count or 0)
    if modified:
        logger.info(
            "[ISP ALERT] recovery detected | isp=%s | location=%s | count=%s | cycleId=%s",
            isp_name,
            location,
            modified,
            cycle_id,
        )
        publish(
            EVENT_ALERT_RESOLVED,
            {
                "ispId": isp_id,
                "hostname": isp_name,
                "location": location,
                "status": STATUS_OFFLINE,
                "resolvedCount": modified,
                "cycleId": cycle_id,
            },
        )

    for alert in active_alerts[:modified]:
        try:
            recovery_sent = send_isp_recovery_alert(isp, alert, scan_type=scan_type)
            if recovery_sent:
                _db().alerts.update_one(
                    {"_id": alert["_id"]},
                    {"$set": {"recoveryEmailSent": True}},
                )
                logger.info(
                    "[ISP ALERT] recovery email sent | isp=%s | location=%s | alertId=%s",
                    isp_name,
                    location,
                    alert.get("_id"),
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[ISP ALERT] recovery email failed | isp=%s | alertId=%s | error=%s",
                isp_name,
                alert.get("_id"),
                exc,
            )

    return modified


def has_active_isp_offline_alert(isp_id: str) -> bool:
    """Return whether an unresolved ISP offline incident exists (restart-safe)."""
    return (
        _db().alerts.find_one(_active_isp_offline_filter(isp_id), {"_id": 1}) is not None
    )
