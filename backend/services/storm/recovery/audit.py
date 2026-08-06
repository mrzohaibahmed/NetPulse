"""
Auditing and history tracking for storm recovery.
Creates records in the storm_recovery_history collection and logs system audit events.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING

from services.audit_service import log_audit
from utils.monitor_logger import get_monitor_logger
from utils.serializers import format_datetime

logger = get_monitor_logger("storm.recovery.audit")

COLLECTION = "storm_recovery_history"


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _oid(device_id):
    if isinstance(device_id, ObjectId):
        return device_id
    if isinstance(device_id, str) and ObjectId.is_valid(device_id):
        return ObjectId(device_id)
    return device_id


def ensure_recovery_indexes() -> None:
    """Bootstrap MongoDB indexes for storm_recovery_history."""
    try:
        coll = _db()[COLLECTION]
        coll.create_index([("incidentId", ASCENDING)], name="idx_recovery_incident")
        coll.create_index(
            [("deviceId", ASCENDING), ("interface", ASCENDING)],
            name="idx_recovery_device_interface",
        )
        coll.create_index([("timestamp", DESCENDING)], name="idx_recovery_time")
        logger.info("[RECOVERY.AUDIT] Indexes ensured on %s", COLLECTION)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RECOVERY.AUDIT] Failed to create indexes: %s", exc)


def record_recovery_history(
    incident_id: str,
    device_id: Any,
    interface: str,
    recovery_status: str,
    verification_result: dict[str, Any],
    retry_count: int,
    *,
    recovery_type: Optional[str] = None,
    trigger: Optional[str] = None,
    safety_rules: Optional[str] = None,
    execution_checks: Optional[str] = None,
    executed_by: Optional[str] = None,
    recovery_method: Optional[str] = None,
) -> dict[str, Any]:
    """Create an immutable recovery execution history record."""
    doc: dict[str, Any] = {
        "incidentId": incident_id,
        "deviceId": _oid(device_id),
        "interface": interface,
        "recoveryStatus": recovery_status,
        "verificationResult": verification_result,
        "retryCount": retry_count,
        "timestamp": datetime.now(timezone.utc),
    }
    # Optional Manual Override / audit metadata (backward compatible).
    if recovery_type is not None:
        doc["recoveryType"] = recovery_type
    if trigger is not None:
        doc["trigger"] = trigger
    if safety_rules is not None:
        doc["safetyRules"] = safety_rules
    if execution_checks is not None:
        doc["executionChecks"] = execution_checks
    if executed_by is not None:
        doc["executedBy"] = executed_by
    if recovery_method is not None:
        doc["recoveryMethod"] = recovery_method

    try:
        _db()[COLLECTION].insert_one(doc)
        logger.info(
            "Recovery history recorded | incident=%s | status=%s | retry=%d",
            incident_id,
            recovery_status,
            retry_count,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to record recovery history | incident=%s | %s",
            incident_id,
            exc,
        )

    # Log in system audit table
    audit_details: dict[str, Any] = {
        "interface": interface,
        "status": recovery_status,
        "retryCount": retry_count,
    }
    if recovery_type is not None:
        audit_details["recoveryType"] = recovery_type
    if trigger is not None:
        audit_details["trigger"] = trigger
    if recovery_method is not None:
        audit_details["recoveryMethod"] = recovery_method
    if executed_by is not None:
        audit_details["executedBy"] = executed_by

    log_audit(
        action="storm_recovery_execute",
        entity_type="incident",
        entity_id=incident_id,
        details=audit_details,
    )

    return doc


def get_recovery_history(
    incident_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """Retrieve paginated recovery logs."""
    query = {}
    if incident_id:
        query["incidentId"] = incident_id

    try:
        coll = _db()[COLLECTION]
        total = coll.count_documents(query)
        rows = list(
            coll.find(query)
            .sort("timestamp", DESCENDING)
            .skip(skip)
            .limit(limit)
        )
        return rows, total
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to query recovery logs: %s", exc)
        return [], 0


def serialize_recovery_log(doc: dict[str, Any]) -> dict[str, Any]:
    """HTTP response serialization for recovery history."""
    verification = doc.get("verificationResult") or {}
    payload: dict[str, Any] = {
        "_id": str(doc["_id"]) if doc.get("_id") is not None else None,
        "incidentId": doc.get("incidentId"),
        "deviceId": str(doc["deviceId"]) if doc.get("deviceId") is not None else None,
        "interface": doc.get("interface"),
        "recoveryStatus": doc.get("recoveryStatus"),
        "verificationResult": verification,
        "retryCount": int(doc.get("retryCount", 0)),
        "timestamp": format_datetime(doc.get("timestamp")),
        # Recovery Safety Engine fields (when status is BLOCKED)
        "failedRule": verification.get("failedRule") or verification.get("recoveryRule"),
        "checks": verification.get("checks") or {},
        "engine": verification.get("engine"),
    }
    # Optional reconciliation audit fields (backward compatible)
    for key in (
        "recoveryRule",
        "previousStatus",
        "newStatus",
        "reason",
        "reconciled",
        "detectedBy",
        "note",
    ):
        if verification.get(key) is not None:
            payload[key] = verification.get(key)
    # Optional Manual Override audit fields (top-level on history docs)
    for key in (
        "recoveryType",
        "trigger",
        "safetyRules",
        "executionChecks",
        "executedBy",
        "recoveryMethod",
    ):
        if doc.get(key) is not None:
            payload[key] = doc.get(key)
    return payload
