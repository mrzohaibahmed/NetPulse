"""
Mitigation Orchestrator
=======================
Coordinates diagnostics capture and incident creation before mitigation.

THIS MODULE NEVER EXECUTES CONFIGURATION COMMANDS.
It only *prepares* mitigation (diagnostics + incident). Actual shutdown /
recovery is performed by the Mitigation Engine (`services.storm.mitigation`)
when `mitigationMode` is automatic, or when an admin triggers it manually.

Public API
----------
    from services.storm.orchestrator import prepare
    result = prepare(device_id, interface)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId

from services.storm.diagnostics.collector import capture_diagnostics
from services.storm.incident import (
    append_timeline_event,
    create_incident_from_diagnostics,
    ensure_incident_indexes,
    find_open_incident,
)
from services.storm.mitigation_context import build_mitigation_context
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.orchestrator")

STATUS_READY = "READY_FOR_MITIGATION"
STATUS_BLOCKED = "BLOCKED"
STATUS_ALREADY_PREPARED = "ALREADY_PREPARED"


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _oid(device_id):
    if isinstance(device_id, ObjectId):
        return device_id
    if isinstance(device_id, str) and ObjectId.is_valid(device_id):
        return ObjectId(device_id)
    return device_id


def _latest_safety(device_id, interface: str) -> Optional[dict]:
    return _db().storm_safety_history.find_one(
        {"deviceId": _oid(device_id), "interface": interface},
        sort=[("timestamp", -1)],
    )


def prepare(
    device_id,
    interface: str,
    *,
    probe_ssh: bool = True,
    require_safety: bool = True,
    persist: bool = True,
    diagnostics: Optional[dict] = None,
    safety: Optional[dict] = None,
    incident_metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Prepare the environment for mitigation without executing it.

    Steps
    -----
    1. Validate latest Safety Result (must be safe)
    2. Capture Diagnostics (read-only)
    3. Create Incident (one per open storm)
    4. Build Mitigation Context
    5. Return READY_FOR_MITIGATION
    """
    name = str(interface or "").strip()
    device_key = str(device_id)

    if not name:
        return {
            "ready": False,
            "status": STATUS_BLOCKED,
            "incidentId": None,
            "deviceId": device_key,
            "interface": None,
            "reason": "Missing interface name",
            "context": {},
        }

    # 1) Validate safety
    safety_doc = safety
    if safety_doc is None:
        try:
            safety_doc = _latest_safety(device_id, name)
        except Exception as exc:  # noqa: BLE001
            logger.error("Safety lookup failed | %s | %s", name, exc)
            safety_doc = None

    if require_safety and (not safety_doc or not safety_doc.get("safe")):
        reason = (safety_doc or {}).get("reason") or "Safety result missing or unsafe"
        logger.info("Mitigation preparation blocked | %s | %s", name, reason)
        return {
            "ready": False,
            "status": STATUS_BLOCKED,
            "incidentId": None,
            "deviceId": device_key,
            "interface": name,
            "reason": reason,
            "context": {},
        }

    # Reuse open prepared incident if already ready
    existing = None
    try:
        existing = find_open_incident(device_id, name)
    except Exception:  # noqa: BLE001
        existing = None

    if existing and existing.get("status") in (
        STATUS_READY,
        "PREPARED",
        "READY_FOR_MITIGATION",
    ):
        context = build_mitigation_context(
            device_id=device_id,
            interface=name,
            incident=existing,
            safety=safety_doc,
        )
        return {
            "ready": True,
            "status": STATUS_ALREADY_PREPARED,
            "incidentId": existing.get("incidentId"),
            "deviceId": device_key,
            "interface": name,
            "reason": "Open incident already prepared",
            "context": context,
        }

    # 2) Diagnostics
    try:
        diag = diagnostics or capture_diagnostics(
            device_id, name, probe_ssh=probe_ssh
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Diagnostics failed | %s | %s", name, exc)
        return {
            "ready": False,
            "status": STATUS_BLOCKED,
            "incidentId": None,
            "deviceId": device_key,
            "interface": name,
            "reason": f"Diagnostics failed: {exc}",
            "context": {},
        }

    # Ensure safety snapshot is present on diagnostics package
    if not diag.get("safety") and safety_doc:
        diag = {**diag, "safety": safety_doc}

    # 3) Incident
    try:
        incident = create_incident_from_diagnostics(
            diag,
            persist=persist,
            incident_metadata=incident_metadata,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Incident creation failed | %s | %s", name, exc)
        return {
            "ready": False,
            "status": STATUS_BLOCKED,
            "incidentId": None,
            "deviceId": device_key,
            "interface": name,
            "reason": f"Incident creation failed: {exc}",
            "context": {},
        }

    incident_id = incident.get("incidentId")

    # 4) Context
    context = build_mitigation_context(
        device_id=device_id,
        interface=name,
        incident=incident,
        diagnostics=diag,
        safety=safety_doc,
    )

    # Mark prepared (timeline only — evidence snapshots stay immutable)
    if incident_id:
        append_timeline_event(
            incident_id,
            "Mitigation Preparation Ready",
            detail=STATUS_READY,
            status=STATUS_READY,
        )

    logger.info(
        "Mitigation Preparation Ready | %s | incident=%s",
        name,
        incident_id,
    )

    return {
        "ready": True,
        "status": STATUS_READY,
        "incidentId": incident_id,
        "deviceId": device_key,
        "interface": name,
        "reason": "Diagnostics captured and incident prepared",
        "context": context,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def prepare_all_safe(*, probe_ssh: bool = True) -> dict[str, Any]:
    """
    Run prepare() for every interface with a latest SAFE safety result.

    Safe for APScheduler — never raises, never executes mitigation.
    """
    logger.info("[ORCHESTRATOR] Bulk prepare started")
    total = 0
    ready = 0
    blocked = 0
    errors = 0

    try:
        pipeline = [
            {"$sort": {"timestamp": -1}},
            {
                "$group": {
                    "_id": {
                        "deviceId": "$deviceId",
                        "interface": "$interface",
                    },
                    "doc": {"$first": "$$ROOT"},
                }
            },
            {"$replaceRoot": {"newRoot": "$doc"}},
            {"$match": {"safe": True}},
        ]
        for row in _db().storm_safety_history.aggregate(pipeline):
            device_id = row.get("deviceId")
            name = row.get("interface")
            if device_id is None or not name:
                continue
            total += 1
            try:
                result = prepare(
                    device_id,
                    name,
                    probe_ssh=probe_ssh,
                    require_safety=True,
                    safety=row,
                )
                if result.get("ready"):
                    ready += 1
                else:
                    blocked += 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.error("[ORCHESTRATOR] prepare failed | %s | %s", name, exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[ORCHESTRATOR] Bulk prepare aborted: %s", exc)
        errors += 1

    logger.info(
        "[ORCHESTRATOR] Bulk complete | total=%s ready=%s blocked=%s errors=%s",
        total,
        ready,
        blocked,
        errors,
    )
    return {
        "total": total,
        "ready": ready,
        "blocked": blocked,
        "errors": errors,
    }


# Re-export for app bootstrap convenience
__all__ = [
    "STATUS_ALREADY_PREPARED",
    "STATUS_BLOCKED",
    "STATUS_READY",
    "ensure_incident_indexes",
    "prepare",
    "prepare_all_safe",
]
