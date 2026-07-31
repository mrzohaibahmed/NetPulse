"""
Post-recovery pipeline invalidation
===================================
After a successful recovery, storm pipeline state must return to a clean
monitoring baseline. Stale CONFIRMED / SAFE history must never re-trigger
mitigation without a brand-new confirmation sequence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId

from services.storm.confirmation_rules import STATE_NOT_CONFIRMED
from services.storm.models import (
    ConfirmationResult,
    SafetyResult,
    create_confirmation_document,
    create_safety_document,
)
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.recovery.post_recovery")

_CANCELABLE_PREP_STATUSES = frozenset({
    "OPEN",
    "PREPARED",
    "READY_FOR_MITIGATION",
})


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _oid(val: Any) -> ObjectId:
    if isinstance(val, ObjectId):
        return val
    return ObjectId(str(val))


def invalidate_pipeline_after_recovery(
    device_id: Any,
    interface: str,
    *,
    reason: str,
    incident_id: Optional[str] = None,
    hostname: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> dict[str, Any]:
    """
    Reset confirmation + invalidate safety, and cancel orphan prepared incidents.
    """
    name = str(interface or "").strip()
    if not name or device_id is None:
        return {"ok": False, "reason": "Missing deviceId or interface"}

    oid = _oid(device_id)
    now = datetime.now(timezone.utc)
    db = _db()

    if hostname is None or ip_address is None:
        device = db.devices.find_one({"_id": oid}, {"hostname": 1, "ipAddress": 1}) or {}
        hostname = hostname or device.get("hostname")
        ip_address = ip_address or device.get("ipAddress")

    confirmation_doc = create_confirmation_document(
        device_id=oid,
        interface=name,
        result=ConfirmationResult(
            confirmed=False,
            state=STATE_NOT_CONFIRMED,
            current_risk=0.0,
            highest_risk=0.0,
            average_risk=0.0,
            consecutive_high_samples=0,
            required_samples=0,
            reason=reason,
            timestamp=now,
            device_id=str(oid),
            interface=name,
            reset=True,
            reset_reason=reason,
        ),
        timestamp=now,
        hostname=hostname,
        ip_address=ip_address,
    )
    db.storm_confirmation_history.insert_one(confirmation_doc)

    safety_doc = create_safety_document(
        device_id=oid,
        interface=name,
        result=SafetyResult(
            safe=False,
            reason=reason,
            confidence=100.0,
            failed_rule="POST_RECOVERY",
            checks={
                "stormConfirmed": False,
                "postRecoveryInvalidation": True,
            },
            timestamp=now,
            device_id=str(oid),
            interface=name,
            status="UNSAFE",
        ),
        timestamp=now,
        hostname=hostname,
        ip_address=ip_address,
    )
    db.storm_safety_history.insert_one(safety_doc)

    cancel_query: dict[str, Any] = {
        "deviceId": oid,
        "interface": name,
        "status": {"$in": list(_CANCELABLE_PREP_STATUSES)},
    }
    if incident_id:
        cancel_query["incidentId"] = {"$ne": incident_id}

    cancel_result = db.storm_incidents.update_many(
        cancel_query,
        {
            "$set": {
                "status": "CANCELLED",
                "updatedAt": now,
                "cancelReason": reason,
            },
            "$push": {
                "timeline": {
                    "event": "Preparation Cancelled",
                    "time": now,
                    "detail": reason,
                }
            },
        },
    )

    logger.info(
        "Post-recovery pipeline invalidated | %s | incident=%s | cancelled=%s | %s",
        name,
        incident_id,
        cancel_result.modified_count,
        reason,
    )
    return {
        "ok": True,
        "cancelledIncidents": int(cancel_result.modified_count),
        "reason": reason,
    }
