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

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from bson import ObjectId

from services.settings_service import get_storm_risk_threshold
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

# Overlapping pipeline cycles often write a fresh CONFIRMED row ~0.5–30s after
# safety persists SAFE. Strict safety_ts >= confirm_ts then falsely blocks prepare.
def _safety_confirm_skew() -> timedelta:
    raw = (os.getenv("STORM_SAFETY_CONFIRM_SKEW_SECONDS") or "120").strip()
    try:
        seconds = int(float(raw))
    except (TypeError, ValueError):
        seconds = 120
    return timedelta(seconds=max(0, seconds))


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _oid(device_id):
    if isinstance(device_id, ObjectId):
        return device_id
    if isinstance(device_id, str) and ObjectId.is_valid(device_id):
        return ObjectId(device_id)
    return device_id


def _as_aware(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts
    return None


def _latest_safety(device_id, interface: str) -> Optional[dict]:
    return _db().storm_safety_history.find_one(
        {"deviceId": _oid(device_id), "interface": interface},
        sort=[("timestamp", -1)],
    )


def _latest_confirmation(device_id, interface: str) -> Optional[dict]:
    from services.storm.confirmation_history import (  # noqa: PLC0415
        load_latest_confirmation,
    )

    return load_latest_confirmation(device_id, interface)


def _latest_risk(device_id, interface: str) -> Optional[dict]:
    from services.storm.confirmation_history import load_latest_risk  # noqa: PLC0415

    return load_latest_risk(device_id, interface)


def _is_currently_confirmed(doc: Optional[dict]) -> bool:
    if not doc:
        return False
    return bool(doc.get("confirmed")) or str(doc.get("state") or "").upper() == "CONFIRMED"


def _validate_live_storm_gates(
    device_id,
    interface: str,
    *,
    safety_doc: Optional[dict],
) -> tuple[bool, str]:
    """
    Mitigation may only be prepared for an *active* storm backed by fresh safety.

    Gates
    -----
    1. Latest confirmation must be CONFIRMED
    2. Latest risk must still be at/above remmitigation threshold
    3. Latest safety must be safe=True and fresh enough vs confirmation
       (allows a short skew so concurrent confirmation heartbeats cannot
       invalidate a SAFE result written moments earlier in JOB D)
    """
    confirmation = _latest_confirmation(device_id, interface)
    if not _is_currently_confirmed(confirmation):
        return False, "Storm is not currently confirmed — fresh confirmation required"

    risk_threshold = get_storm_risk_threshold()
    risk = _latest_risk(device_id, interface)
    try:
        score = float((risk or {}).get("riskScore") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if score < risk_threshold:
        return (
            False,
            f"Risk no longer high ({score:.1f} < {risk_threshold:.0f}) — fresh storm required",
        )

    if not safety_doc or not safety_doc.get("safe"):
        return False, (safety_doc or {}).get("reason") or "Safety result missing or unsafe"

    safety_ts = _as_aware(safety_doc.get("timestamp"))
    confirm_ts = _as_aware((confirmation or {}).get("timestamp"))
    if safety_ts is None or confirm_ts is None:
        return False, "Safety/confirmation timestamps missing — cannot verify freshness"
    skew = _safety_confirm_skew()
    if safety_ts + skew < confirm_ts:
        return (
            False,
            "Safety result is stale relative to current confirmation — re-evaluate safety",
        )

    return True, "Live storm gates passed"


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
    require_live_storm: bool = True,
) -> dict[str, Any]:
    """
    Prepare the environment for mitigation without executing it.

    Steps
    -----
    1. Validate latest Safety Result (must be safe)
    2. Validate live confirmation + risk + safety freshness (unless bypassed)
    3. Capture Diagnostics (read-only)
    4. Create Incident (one per open storm)
    5. Build Mitigation Context
    6. Return READY_FOR_MITIGATION

    ``require_live_storm`` defaults True. Operator emergency/manual flows that
    already created their own incident may call prepare with
    ``require_live_storm=False`` only when explicitly justified by the caller.
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

    # 1) Validate safety document presence
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

    # 1b) Live storm gates — never prepare from stale SAFE history alone
    if require_live_storm:
        ok, gate_reason = _validate_live_storm_gates(
            device_id, name, safety_doc=safety_doc
        )
        if not ok:
            logger.info("Mitigation preparation blocked | %s | %s", name, gate_reason)
            return {
                "ready": False,
                "status": STATUS_BLOCKED,
                "incidentId": None,
                "deviceId": device_key,
                "interface": name,
                "reason": gate_reason,
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

    # Merge source-attribution extras from prepare_all into diagnostics
    meta = incident_metadata or {}
    if meta.get("sourceAttribution") and not diag.get("sourceAttribution"):
        diag = {**diag, "sourceAttribution": meta["sourceAttribution"]}
    if meta.get("affectedInterfaces") is not None:
        diag = {**diag, "affectedInterfaces": meta.get("affectedInterfaces")}
    if meta.get("relatedInterfaces") is not None:
        diag = {**diag, "relatedInterfaces": meta.get("relatedInterfaces")}

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
    Run prepare() for every interface whose *latest* confirmation is CONFIRMED
    and whose *latest* safety result is SAFE.

    When source arbitration is enabled, only the selected originating
    interface per (device, broadcast domain) is prepared — flood victims
    that somehow remain CONFIRMED are skipped.
    """
    logger.info("[ORCHESTRATOR] Bulk prepare started")
    total = 0
    ready = 0
    blocked = 0
    errors = 0
    skipped_receivers = 0
    block_reasons: dict[str, int] = {}

    try:
        from services.storm.source_arbitration_config import (  # noqa: PLC0415
            get_source_arbitration_config,
        )
        from services.storm.storm_source_selector import (  # noqa: PLC0415
            is_selected_storm_source,
        )

        arb_cfg = get_source_arbitration_config()

        # Start from currently CONFIRMED storms (same selection idea as safety bulk).
        confirm_pipeline = [
            {"$sort": {"timestamp": -1}},
            {
                "$group": {
                    "_id": {
                        "deviceId": "$deviceId",
                        "interface": "$interface",
                    },
                    "confirmed": {"$first": "$confirmed"},
                    "state": {"$first": "$state"},
                }
            },
            {
                "$match": {
                    "$or": [
                        {"confirmed": True},
                        {"state": "CONFIRMED"},
                    ]
                }
            },
        ]
        for row in _db().storm_confirmation_history.aggregate(confirm_pipeline):
            key = row.get("_id") or {}
            device_id = key.get("deviceId")
            name = key.get("interface")
            if device_id is None or not name:
                continue
            # Defense in depth — never prepare from a superseded confirmation.
            try:
                from services.storm.confirmation_history import (  # noqa: PLC0415
                    load_latest_confirmation,
                )

                latest = load_latest_confirmation(device_id, name)
                if not latest or not (
                    latest.get("confirmed")
                    or str(latest.get("state") or "").upper() == "CONFIRMED"
                ):
                    blocked += 1
                    block_reasons["not_currently_confirmed"] = (
                        block_reasons.get("not_currently_confirmed", 0) + 1
                    )
                    logger.info(
                        "[ORCHESTRATOR] prepare skipped | %s | not currently confirmed",
                        name,
                    )
                    continue
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.error("[ORCHESTRATOR] confirmation gate failed | %s | %s", name, exc)
                continue

            # Source arbitration: only prepare the selected origin.
            if arb_cfg.enable_source_arbitration:
                try:
                    selected, selection = is_selected_storm_source(device_id, name)
                    if not selected:
                        skipped_receivers += 1
                        logger.info(
                            "[ORCHESTRATOR] prepare skipped | %s | not selected source "
                            "(selected=%s)",
                            name,
                            selection.best.interface if selection.best else None,
                        )
                        continue
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[ORCHESTRATOR] source arbitration failed | %s | %s — proceeding",
                        name,
                        exc,
                    )

            total += 1
            try:
                # Attach related/affected interfaces from arbitration runners/receivers
                related_meta = None
                try:
                    if arb_cfg.enable_source_arbitration:
                        _ok, selection = is_selected_storm_source(device_id, name)
                        related_meta = {
                            "sourceAttribution": selection.to_dict(),
                            "affectedInterfaces": [
                                c.interface for c in selection.receivers
                            ],
                            "relatedInterfaces": [
                                c.interface for c in selection.runners_up
                            ],
                        }
                except Exception:  # noqa: BLE001
                    related_meta = None

                result = prepare(
                    device_id,
                    name,
                    probe_ssh=probe_ssh,
                    require_safety=True,
                    require_live_storm=True,
                    incident_metadata=related_meta,
                )
                if result.get("ready"):
                    ready += 1
                else:
                    blocked += 1
                    reason_key = str(result.get("reason") or "blocked").strip()
                    if len(reason_key) > 120:
                        reason_key = reason_key[:117] + "..."
                    block_reasons[reason_key] = block_reasons.get(reason_key, 0) + 1
                    logger.info(
                        "[ORCHESTRATOR] prepare blocked | %s | %s",
                        name,
                        result.get("reason"),
                    )
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.error("[ORCHESTRATOR] prepare failed | %s | %s", name, exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[ORCHESTRATOR] Bulk prepare aborted: %s", exc)
        errors += 1

    logger.info(
        "[ORCHESTRATOR] Bulk complete | total=%s ready=%s blocked=%s "
        "skippedReceivers=%s errors=%s reasons=%s",
        total,
        ready,
        blocked,
        skipped_receivers,
        errors,
        block_reasons,
    )
    return {
        "total": total,
        "ready": ready,
        "blocked": blocked,
        "skippedReceivers": skipped_receivers,
        "errors": errors,
        "blockReasons": block_reasons,
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
