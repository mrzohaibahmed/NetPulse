"""
Audit and history tracking for storm mitigation.
Creates immutable storm_mitigation_history documents and audit logs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING

from services.audit_service import log_audit
from utils.monitor_logger import get_monitor_logger
from utils.serializers import format_datetime

logger = get_monitor_logger("storm.mitigation.audit")

COLLECTION = "storm_mitigation_history"


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _oid(device_id):
    if isinstance(device_id, ObjectId):
        return device_id
    if isinstance(device_id, str) and ObjectId.is_valid(device_id):
        return ObjectId(device_id)
    return device_id


def ensure_mitigation_indexes() -> None:
    """Bootstrap MongoDB indexes for storm_mitigation_history."""
    try:
        coll = _db()[COLLECTION]
        coll.create_index([("incidentId", ASCENDING)], name="idx_mitigation_incident")
        coll.create_index(
            [("deviceId", ASCENDING), ("interface", ASCENDING)],
            name="idx_mitigation_device_interface",
        )
        coll.create_index([("timestamp", DESCENDING)], name="idx_mitigation_time")
        logger.info("[MITIGATION.AUDIT] Indexes ensured on %s", COLLECTION)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MITIGATION.AUDIT] Failed to create indexes: %s", exc)


def record_mitigation_history(
    incident_id: str,
    device_id: Any,
    interface: str,
    strategy: str,
    status: str,
    commands_executed: list[str],
    verification_result: dict[str, Any],
    rollback_performed: bool,
    operator: str,
) -> dict[str, Any]:
    """Create an immutable mitigation execution history record."""
    doc = {
        "incidentId": incident_id,
        "deviceId": _oid(device_id),
        "interface": interface,
        "strategy": strategy,
        "status": status,
        "commandsExecuted": commands_executed,
        "verificationResult": verification_result,
        "rollbackPerformed": rollback_performed,
        "operator": operator,
        "timestamp": datetime.now(timezone.utc),
    }

    try:
        _db()[COLLECTION].insert_one(doc)
        logger.info(
            "Mitigation history recorded | incident=%s | strategy=%s | status=%s",
            incident_id,
            strategy,
            status,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to record mitigation history | incident=%s | %s",
            incident_id,
            exc,
        )

    # Log in system audit table
    log_audit(
        action="storm_mitigation_execute",
        entity_type="incident",
        entity_id=incident_id,
        details={
            "interface": interface,
            "strategy": strategy,
            "status": status,
            "rollback": rollback_performed,
            "operator": operator,
        },
    )

    return doc


def get_mitigation_history(
    incident_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """Retrieve paginated mitigation logs."""
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
        logger.error("Failed to query mitigation logs: %s", exc)
        return [], 0


def serialize_mitigation_log(doc: dict[str, Any]) -> dict[str, Any]:
    """HTTP response serialization for mitigation history."""
    return {
        "_id": str(doc["_id"]) if doc.get("_id") is not None else None,
        "incidentId": doc.get("incidentId"),
        "deviceId": str(doc["deviceId"]) if doc.get("deviceId") is not None else None,
        "interface": doc.get("interface"),
        "strategy": doc.get("strategy"),
        "status": doc.get("status"),
        "commandsExecuted": doc.get("commandsExecuted") or [],
        "verificationResult": doc.get("verificationResult") or {},
        "rollbackPerformed": bool(doc.get("rollbackPerformed")),
        "operator": doc.get("operator") or "SYSTEM",
        "timestamp": format_datetime(doc.get("timestamp")),
    }
