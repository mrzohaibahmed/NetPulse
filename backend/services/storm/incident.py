"""
Storm incident lifecycle.

Creates immutable storm_incidents documents (append-only evidence).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING

from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.incident")

COLLECTION = "storm_incidents"
COUNTER_ID = "storm_incident"


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _oid(device_id):
    if isinstance(device_id, ObjectId):
        return device_id
    if isinstance(device_id, str) and ObjectId.is_valid(device_id):
        return ObjectId(device_id)
    return device_id


def ensure_incident_indexes() -> None:
    try:
        coll = _db()[COLLECTION]
        coll.create_index([("incidentId", ASCENDING)], unique=True, name="idx_incident_id")
        coll.create_index(
            [("deviceId", ASCENDING), ("interface", ASCENDING), ("createdAt", DESCENDING)],
            name="idx_incident_device_iface_created",
        )
        coll.create_index([("status", ASCENDING), ("createdAt", DESCENDING)], name="idx_incident_status")
        coll.create_index([("createdAt", DESCENDING)], name="idx_incident_created")
        logger.info("[INCIDENT] MongoDB indexes ensured on %s", COLLECTION)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INCIDENT] Failed to ensure indexes: %s", exc)


def next_incident_id(now: Optional[datetime] = None) -> str:
    """Generate storm-YYYY-NNNNNN using an atomic counter."""
    from pymongo import ReturnDocument  # noqa: PLC0415

    ts = now or datetime.now(timezone.utc)
    year = ts.year
    counter_key = f"{COUNTER_ID}-{year}"
    try:
        doc = _db().counters.find_one_and_update(
            {"_id": counter_key},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        seq = int((doc or {}).get("seq") or 1)
    except Exception:  # noqa: BLE001
        # Fallback non-atomic when counters unavailable (tests / mongo down)
        seq = int(ts.timestamp()) % 1_000_000
    return f"storm-{year}-{seq:06d}"


def find_open_incident(device_id, interface: str) -> Optional[dict]:
    return _db()[COLLECTION].find_one(
        {
            "deviceId": _oid(device_id),
            "interface": interface,
            "status": {"$in": ["OPEN", "PREPARED", "READY_FOR_MITIGATION"]},
        },
        sort=[("createdAt", DESCENDING)],
    )


def _timeline_event(event: str, when: Optional[datetime] = None, detail: str | None = None) -> dict:
    return {
        "event": event,
        "time": when or datetime.now(timezone.utc),
        "detail": detail,
    }


def _severity_from_risk(risk: Optional[dict]) -> str:
    try:
        score = float((risk or {}).get("riskScore") or 0)
    except (TypeError, ValueError):
        score = 0.0
    if score >= 75:
        return "CRITICAL"
    if score >= 50:
        return "HIGH"
    if score >= 25:
        return "MEDIUM"
    return "LOW"


def create_incident_from_diagnostics(
    diagnostics: dict[str, Any],
    *,
    force_new: bool = False,
    persist: bool = True,
    incident_metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Create exactly one OPEN incident per active storm (device + interface).

    If an open incident already exists, return it unchanged (immutable evidence).
    """
    device_id = diagnostics.get("deviceId")
    interface = diagnostics.get("interface")
    if not device_id or not interface:
        raise ValueError("deviceId and interface are required")

    incident_metadata = incident_metadata or {}
    incident_type = incident_metadata.get("incidentType")
    requested_by = incident_metadata.get("requestedBy")
    requested_at = incident_metadata.get("requestedAt")
    reason = incident_metadata.get("reason")
    action = incident_metadata.get("action")
    trigger_type = incident_metadata.get("triggerType")

    if persist and not force_new:
        try:
            existing = find_open_incident(device_id, interface)
            if existing:
                # Best-effort: if this open incident was created previously
                # (e.g., by automatic Storm pipeline) and we are now executing
                # a manual action, attach manual metadata fields once.
                updates: dict[str, Any] = {}
                if incident_type and not existing.get("incidentType"):
                    updates["incidentType"] = incident_type
                if requested_by and not existing.get("requestedBy"):
                    updates["requestedBy"] = requested_by
                if requested_at and not existing.get("requestedAt"):
                    updates["requestedAt"] = requested_at
                if reason and not existing.get("reason"):
                    updates["reason"] = reason
                if action and not existing.get("action"):
                    updates["action"] = action
                if trigger_type:
                    existing_trigger = existing.get("trigger") or {}
                    if (
                        not isinstance(existing_trigger, dict)
                        or not existing_trigger.get("type")
                    ):
                        updates["trigger.type"] = trigger_type

                if updates:
                    _db()[COLLECTION].update_one(
                        {"incidentId": existing["incidentId"]},
                        {"$set": updates},
                    )
                    existing = _db()[COLLECTION].find_one(
                        {"incidentId": existing["incidentId"]}
                    )

                logger.info(
                    "Incident already open | %s | %s",
                    existing.get("incidentId"),
                    interface,
                )
                return existing
        except Exception as exc:  # noqa: BLE001
            logger.warning("Open incident lookup failed: %s", exc)

    now = datetime.now(timezone.utc)
    risk = diagnostics.get("risk") or {}
    confirmation = diagnostics.get("confirmation") or {}
    safety = diagnostics.get("safety") or {}

    timeline = [
        _timeline_event(
            "Risk Calculated",
            risk.get("timestamp") or now,
            detail=f"risk={risk.get('riskScore')}",
        ),
        _timeline_event(
            "Storm Confirmed",
            confirmation.get("timestamp") or now,
            detail=str(confirmation.get("state") or confirmation.get("confirmed")),
        ),
        _timeline_event(
            "Safety Passed" if safety.get("safe") else "Safety Evaluated",
            safety.get("timestamp") or now,
            detail=safety.get("reason"),
        ),
        _timeline_event("Diagnostics Captured", diagnostics.get("capturedAt") or now),
        _timeline_event("Incident Created", now),
    ]

    try:
        incident_id = next_incident_id(now) if persist else f"storm-test-{int(now.timestamp())}"
    except Exception:  # noqa: BLE001
        incident_id = f"storm-test-{int(now.timestamp())}"

    document = {
        "incidentId": incident_id,
        "deviceId": _oid(device_id) if persist else device_id,
        "interface": interface,
        "hostname": diagnostics.get("hostname"),
        "ipAddress": diagnostics.get("ipAddress"),
        "incidentType": incident_type or "STORM",
        "requestedBy": requested_by,
        "requestedAt": requested_at,
        "reason": reason,
        "action": action,
        "status": "OPEN",
        "severity": _severity_from_risk(risk),
        "trigger": {
            "risk": risk.get("riskScore"),
            "confirmation": bool(
                confirmation.get("confirmed")
                or str(confirmation.get("state", "")).upper() == "CONFIRMED"
            ),
            "safety": bool(safety.get("safe")),
            **({"type": trigger_type} if trigger_type else {}),
        },
        "interfaceSnapshot": diagnostics.get("interfaceSnapshot") or {},
        "switchportSnapshot": diagnostics.get("switchportSnapshot") or {},
        "macTable": diagnostics.get("macTable") or {},
        "statistics": diagnostics.get("statistics") or {},
        "neighbor": diagnostics.get("neighbor"),
        "deviceHealth": diagnostics.get("deviceHealth") or {},
        "eligibility": diagnostics.get("eligibility"),
        "risk": risk,
        "confirmation": confirmation,
        "safety": safety,
        "diagnosticsMeta": diagnostics.get("diagnosticsMeta") or {},
        "timeline": timeline,
        "createdAt": now,
        "updatedAt": now,
    }

    # Source attribution + related flood victims (optional telemetry)
    source_attr = diagnostics.get("sourceAttribution") or incident_metadata.get(
        "sourceAttribution"
    )
    if source_attr:
        document["sourceAttribution"] = source_attr
        document["sourceClassification"] = (
            source_attr.get("interfaceSourceClassification")
            or (risk or {}).get("sourceClassification")
            or (source_attr.get("bestCandidate") or {}).get("sourceClassification")
        )
        document["sourceConfidence"] = (
            source_attr.get("interfaceSourceConfidence")
            or (risk or {}).get("sourceConfidence")
            or source_attr.get("sourceConfidence")
        )
    elif risk.get("sourceClassification"):
        document["sourceClassification"] = risk.get("sourceClassification")
        document["sourceConfidence"] = risk.get("sourceConfidence")

    affected = diagnostics.get("affectedInterfaces")
    if affected is None:
        affected = incident_metadata.get("affectedInterfaces")
    if affected is not None:
        document["affectedInterfaces"] = list(affected)

    related = diagnostics.get("relatedInterfaces")
    if related is None:
        related = incident_metadata.get("relatedInterfaces")
    if related is not None:
        document["relatedInterfaces"] = list(related)

    if not persist:
        logger.info("Incident Created (in-memory) | %s | %s", incident_id, interface)
        return document

    try:
        _db()[COLLECTION].insert_one(document)
        logger.info("Incident Created | %s | %s", incident_id, interface)
    except Exception as exc:  # noqa: BLE001
        logger.error("Incident create failed | %s | %s", interface, exc)
        document["_id"] = None
        document["_persistError"] = str(exc)

    return document


def create_manual_incident(
    *,
    device_id,
    interface: str,
    hostname: str | None = None,
    ip_address: str | None = None,
    requested_by: str | None = None,
    action: str = "MANUAL_SHUTDOWN",
    reason: str | None = None,
    persist: bool = True,
    force_new: bool = True,
) -> dict[str, Any]:
    """
    Create an OPEN incident for explicit operator-driven manual controls.

    This bypasses diagnostics/risk/confirmation/safety payload generation and
    records only the minimum incident metadata needed by mitigation/recovery
    engines plus timeline/audit context.
    """
    name = str(interface or "").strip()
    if not device_id or not name:
        raise ValueError("deviceId and interface are required")

    now = datetime.now(timezone.utc)
    incident_id = next_incident_id(now) if persist else f"storm-test-{int(now.timestamp())}"

    document: dict[str, Any] = {
        "incidentId": incident_id,
        "deviceId": _oid(device_id) if persist else device_id,
        "interface": name,
        "hostname": hostname,
        "ipAddress": ip_address,
        "incidentType": "MANUAL",
        "requestedBy": requested_by,
        "requestedAt": now,
        "reason": reason,
        "action": action,
        "status": "OPEN",
        "severity": "MEDIUM",
        "trigger": {"type": "MANUAL"},
        "interfaceSnapshot": {},
        "switchportSnapshot": {},
        "macTable": {},
        "statistics": {},
        "neighbor": None,
        "deviceHealth": {},
        "eligibility": None,
        "risk": {},
        "confirmation": {},
        "safety": {},
        "diagnosticsMeta": {"source": "manual-control"},
        "timeline": [
            _timeline_event("Manual Action Requested", now, detail=f"{action} by {requested_by or 'SYSTEM'}"),
            _timeline_event("Incident Created", now, detail="manual control path"),
        ],
        "createdAt": now,
        "updatedAt": now,
    }

    if not persist:
        logger.info("Manual Incident Created (in-memory) | %s | %s", incident_id, name)
        return document

    if not force_new:
        existing = find_open_incident(device_id, name)
        if existing:
            return existing

    _db()[COLLECTION].insert_one(document)
    logger.info("Manual Incident Created | %s | %s", incident_id, name)
    return document


def append_timeline_event(
    incident_id: str,
    event: str,
    *,
    detail: str | None = None,
    status: str | None = None,
) -> Optional[dict]:
    """Append a timeline event without mutating stored evidence snapshots."""
    now = datetime.now(timezone.utc)
    update: dict[str, Any] = {
        "$push": {"timeline": _timeline_event(event, now, detail)},
        "$set": {"updatedAt": now},
    }
    if status:
        update["$set"]["status"] = status
    try:
        _db()[COLLECTION].update_one({"incidentId": incident_id}, update)
        return _db()[COLLECTION].find_one({"incidentId": incident_id})
    except Exception as exc:  # noqa: BLE001
        logger.error("Timeline append failed | %s | %s", incident_id, exc)
        return None


def get_incident(incident_id: str) -> Optional[dict]:
    return _db()[COLLECTION].find_one({"incidentId": incident_id})


def list_incidents(
    *,
    device_id: Optional[ObjectId] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict], int]:
    query: dict[str, Any] = {}
    if device_id is not None:
        query["deviceId"] = device_id
    if status:
        query["status"] = status.upper()
    if search:
        regex = {"$regex": search, "$options": "i"}
        query["$or"] = [
            {"incidentId": regex},
            {"interface": regex},
            {"hostname": regex},
            {"ipAddress": regex},
            {"severity": regex},
            {"status": regex},
        ]

    coll = _db()[COLLECTION]
    total = coll.count_documents(query)
    rows = list(
        coll.find(query)
        .sort("createdAt", DESCENDING)
        .skip(max(int(skip), 0))
        .limit(max(int(limit), 1))
    )
    return rows, total
