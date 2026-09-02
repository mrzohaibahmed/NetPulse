"""
Centralized storm port recovery notifications (alert + email + audit).

One successful MONITORING transition per incident yields at most one notification,
enforced by an atomic claim on the incident document.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo import ReturnDocument

from services.storm.incident import get_incident
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.recovery.notifications")

RECOVERY_SOURCE_AUTOMATIC = "AUTOMATIC"
RECOVERY_SOURCE_RECONCILIATION = "RECONCILIATION"
RECOVERY_SOURCE_OPERATOR = "OPERATOR"
RECOVERY_SOURCE_MANUAL = "MANUAL"

_EVENT_TYPE_BY_SOURCE: dict[str, str] = {
    RECOVERY_SOURCE_AUTOMATIC: "Automatic Port Recovery",
    RECOVERY_SOURCE_RECONCILIATION: "Automatic Port Recovery",
    RECOVERY_SOURCE_OPERATOR: "Operator Port Recovery",
    RECOVERY_SOURCE_MANUAL: "Manual Port Recovery",
}

_ALERT_TITLE_BY_SOURCE: dict[str, str] = dict(_EVENT_TYPE_BY_SOURCE)

_DEFAULT_REASON_BY_SOURCE: dict[str, str] = {
    RECOVERY_SOURCE_AUTOMATIC: (
        "Automatic recovery verified — port restored "
        "(stabilization monitoring started)"
    ),
    RECOVERY_SOURCE_RECONCILIATION: (
        "Interface already administratively UP — reconciled into "
        "stabilization monitoring"
    ),
    RECOVERY_SOURCE_OPERATOR: (
        "Operator recovery verified — port restored "
        "(stabilization monitoring started)"
    ),
    RECOVERY_SOURCE_MANUAL: (
        "Manual recovery verified — port restored "
        "(stabilization monitoring started)"
    ),
}


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _oid(val: Any) -> ObjectId:
    if isinstance(val, ObjectId):
        return val
    return ObjectId(str(val))


def _claim_recovery_notification_slot(
    incident_id: str,
    *,
    source: str,
    operator: str,
) -> bool:
    """
    Atomically claim the one-time recovery notification slot for an incident.

    Returns True when this caller won the claim; False if already notified or
    the incident row is missing.
    """
    now = datetime.now(timezone.utc)
    claimed = _db().storm_incidents.find_one_and_update(
        {
            "incidentId": incident_id,
            "$or": [
                {"recoveryNotificationSentAt": {"$exists": False}},
                {"recoveryNotificationSentAt": None},
            ],
        },
        {
            "$set": {
                "recoveryNotificationSentAt": now,
                "recoveryNotificationSource": source,
                "recoveryNotificationOperator": operator,
            }
        },
        return_document=ReturnDocument.BEFORE,
    )
    return claimed is not None


def _alert_message(source: str, interface: str) -> str:
    if source == RECOVERY_SOURCE_RECONCILIATION:
        return (
            "Storm conditions cleared.\n"
            f"Interface {interface} was already up — incident reconciled "
            "into stabilization monitoring."
        )
    if source == RECOVERY_SOURCE_OPERATOR:
        return (
            "Storm conditions cleared.\n"
            f"Operator restored interface {interface}."
        )
    if source == RECOVERY_SOURCE_MANUAL:
        return (
            "Storm conditions cleared.\n"
            f"Manual recovery restored interface {interface}."
        )
    return (
        "Storm conditions cleared.\n"
        f"NetPulse automatically restored interface {interface}."
    )


def notify_port_recovery(
    incident_id: str,
    *,
    source: str,
    operator: str,
    device: Optional[dict] = None,
    verification_result: Any = None,
    recovered_at: Optional[datetime] = None,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create storm recovery alert, send recovery email, and audit delivery.

    Never raises. Recovery workflow must remain successful regardless of
    notification outcome.
    """
    src = str(source or RECOVERY_SOURCE_AUTOMATIC).upper()
    op = str(operator or "SYSTEM")
    result: dict[str, Any] = {
        "notified": False,
        "skipped": False,
        "alert_id": None,
        "email_sent": False,
        "reason": None,
    }

    logger.info(
        "[STORM] Recovery notification triggered | incident=%s | source=%s | operator=%s",
        incident_id,
        src,
        op,
    )

    if not _claim_recovery_notification_slot(incident_id, source=src, operator=op):
        logger.info(
            "[STORM] Recovery notification skipped | incident=%s | reason=already_notified",
            incident_id,
        )
        result["skipped"] = True
        result["reason"] = "already_notified"
        return result

    incident = get_incident(incident_id) or {}
    if device is None and incident.get("deviceId"):
        try:
            device = _db().devices.find_one({"_id": _oid(incident["deviceId"])})
        except Exception:  # noqa: BLE001
            device = None

    interface = (incident or {}).get("interface") or "unknown"
    event_type = _EVENT_TYPE_BY_SOURCE.get(src, "Automatic Port Recovery")
    alert_title = _ALERT_TITLE_BY_SOURCE.get(src, event_type)
    email_reason = reason or _DEFAULT_REASON_BY_SOURCE.get(
        src,
        _DEFAULT_REASON_BY_SOURCE[RECOVERY_SOURCE_AUTOMATIC],
    )

    alert_id: Optional[str] = None
    try:
        from services.alert_service import create_storm_recovery_alert  # noqa: PLC0415

        alert_id = create_storm_recovery_alert(
            incident,
            device=device,
            recovered_at=recovered_at,
            title=alert_title,
            message=_alert_message(src, interface),
            recovery_source=src,
        )
    except Exception as alert_exc:  # noqa: BLE001
        logger.warning(
            "Storm recovery alert failed | incident=%s | %s",
            incident_id,
            alert_exc,
        )

    email_sent = False
    try:
        from services.alert_service import mark_alert_email_sent  # noqa: PLC0415
        from services.email_service import send_storm_recovery_notification  # noqa: PLC0415

        email_sent = send_storm_recovery_notification(
            incident,
            verification_result=verification_result,
            reason=email_reason,
            operator=op,
            recovered_at=recovered_at,
            event_type=event_type,
            recovery_source=src,
        )
        if alert_id:
            mark_alert_email_sent(alert_id, bool(email_sent))
        if not email_sent:
            logger.warning(
                "[STORM] Recovery email delivery failed | incident=%s",
                incident_id,
            )
    except Exception as mail_exc:  # noqa: BLE001
        logger.warning(
            "[STORM] Recovery email delivery failed | incident=%s | %s",
            incident_id,
            mail_exc,
        )

    result.update(
        {
            "notified": True,
            "alert_id": alert_id,
            "email_sent": bool(email_sent),
        }
    )
    return result
