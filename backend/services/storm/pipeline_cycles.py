"""
Storm pipeline cycle coordination (execution metadata only).

Associates stats → analysis (eligibility+risk) → confirmation → safety/prepare
without changing storm decision formulas. Stages claim cycles via atomic
status transitions so incomplete writes are not consumed downstream.

Phase 3A adds stage leases for crash detection. Expired leases are NEVER
auto-retried for confirmation/safety/analysis (append-only / mitigation-adjacent
writes). They are marked ``failed_recovery_required`` for operator review.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from typing import Any, Optional

from pymongo import ASCENDING, DESCENDING, ReturnDocument

from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("storm.pipeline")

COLLECTION = "storm_pipeline_cycles"

STATUS_STATS_RUNNING = "stats_running"
STATUS_STATS_COMPLETE = "stats_complete"
STATUS_ANALYSIS_RUNNING = "analysis_running"
STATUS_ANALYSIS_COMPLETE = "analysis_complete"
STATUS_CONFIRMATION_RUNNING = "confirmation_running"
STATUS_CONFIRMATION_COMPLETE = "confirmation_complete"
STATUS_SAFETY_RUNNING = "safety_running"
STATUS_SAFETY_COMPLETE = "safety_complete"
STATUS_FAILED = "failed"
STATUS_FAILED_RECOVERY_REQUIRED = "failed_recovery_required"

# Crash-recovery leases (NOT performance timeouts). Must exceed worst-case stage.
LEASE_SECONDS = {
    "stats": int(os.environ.get("STORM_LEASE_STATS_SECONDS", str(15 * 60))),
    "analysis": int(os.environ.get("STORM_LEASE_ANALYSIS_SECONDS", str(20 * 60))),
    "confirmation": int(
        os.environ.get("STORM_LEASE_CONFIRMATION_SECONDS", str(15 * 60))
    ),
    "safety": int(os.environ.get("STORM_LEASE_SAFETY_SECONDS", str(45 * 60))),
}

RUNNING_STATUSES = (
    STATUS_STATS_RUNNING,
    STATUS_ANALYSIS_RUNNING,
    STATUS_CONFIRMATION_RUNNING,
    STATUS_SAFETY_RUNNING,
)

_STATUS_TO_STAGE = {
    STATUS_STATS_RUNNING: "stats",
    STATUS_ANALYSIS_RUNNING: "analysis",
    STATUS_CONFIRMATION_RUNNING: "confirmation",
    STATUS_SAFETY_RUNNING: "safety",
}


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def new_cycle_id() -> str:
    return uuid.uuid4().hex


def lease_reclaim_enabled() -> bool:
    """
    When disabled, expired leases are logged but not marked failed.

    Set STORM_CYCLE_LEASE_RECLAIM=0 for operator-only review mode.
    """
    return str(os.environ.get("STORM_CYCLE_LEASE_RECLAIM", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _lease_fields(stage: str, owner: Optional[str]) -> dict[str, Any]:
    now = utc_now()
    seconds = max(int(LEASE_SECONDS.get(stage, 15 * 60)), 60)
    return {
        "stage": stage,
        "stageOwner": owner,
        "stageStartedAt": now,
        "leaseHeartbeatAt": now,
        "leaseExpiresAt": now + timedelta(seconds=seconds),
    }


def ensure_pipeline_cycle_indexes() -> None:
    try:
        coll = _db()[COLLECTION]
        coll.create_index(
            [("status", ASCENDING), ("createdAt", ASCENDING)],
            name="idx_storm_cycle_status_created",
        )
        coll.create_index([("createdAt", DESCENDING)], name="idx_storm_cycle_created")
        coll.create_index(
            [("status", ASCENDING), ("leaseExpiresAt", ASCENDING)],
            name="idx_storm_cycle_status_lease",
        )
        logger.info("[STORM_CYCLE] indexes ensured on %s", COLLECTION)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STORM_CYCLE] Failed to ensure indexes: %s", exc)


def begin_stats_cycle(*, leader: Optional[str] = None) -> dict[str, Any]:
    now = utc_now()
    cycle_id = new_cycle_id()
    lease = _lease_fields("stats", leader)
    doc = {
        "_id": cycle_id,
        "status": STATUS_STATS_RUNNING,
        "leaderId": leader,
        "createdAt": now,
        "updatedAt": now,
        "statsStartedAt": now,
        "statsCompletedAt": None,
        "analysisStartedAt": None,
        "analysisCompletedAt": None,
        "riskPublishedAt": None,
        "confirmationStartedAt": None,
        "confirmationCompletedAt": None,
        "safetyStartedAt": None,
        "safetyCompletedAt": None,
        "statsSummary": None,
        "analysisSummary": None,
        "confirmationSummary": None,
        "safetySummary": None,
        "error": None,
        "reclaimed": False,
        **lease,
    }
    _db()[COLLECTION].insert_one(doc)
    logger.info(
        "STORM_STAGE_START stage=stats cycleId=%s leader=%s owner=%s leaseExpiresAt=%s",
        cycle_id,
        leader or "-",
        lease.get("stageOwner") or "-",
        lease.get("leaseExpiresAt"),
    )
    return doc


def _transition(
    cycle_id: str,
    *,
    from_status: str,
    to_status: str,
    extra: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    now = utc_now()
    updates: dict[str, Any] = {
        "status": to_status,
        "updatedAt": now,
    }
    if extra:
        updates.update(extra)
    # Clear active lease when leaving a running stage.
    if from_status in RUNNING_STATUSES:
        updates.update(
            {
                "stageOwner": None,
                "leaseExpiresAt": None,
                "leaseHeartbeatAt": None,
            }
        )
    return _db()[COLLECTION].find_one_and_update(
        {"_id": cycle_id, "status": from_status},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )


def mark_stats_complete(cycle_id: str, summary: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    now = utc_now()
    doc = _transition(
        cycle_id,
        from_status=STATUS_STATS_RUNNING,
        to_status=STATUS_STATS_COMPLETE,
        extra={
            "statsCompletedAt": now,
            "statsSummary": summary,
        },
    )
    if doc:
        started = doc.get("statsStartedAt") or now
        if getattr(started, "tzinfo", None) is None:
            from datetime import timezone  # noqa: PLC0415

            started = started.replace(tzinfo=timezone.utc)
        duration_ms = int(max((now - started).total_seconds(), 0) * 1000)
        logger.info(
            "STORM_STAGE_COMPLETE stage=stats cycleId=%s durationMs=%s "
            "devicesProcessed=%s samples=%s errors=%s",
            cycle_id,
            duration_ms,
            (summary or {}).get("total"),
            (summary or {}).get("samples"),
            (summary or {}).get("failed"),
        )
    return doc


def mark_cycle_failed(cycle_id: str, stage: str, error: str) -> None:
    now = utc_now()
    _db()[COLLECTION].update_one(
        {"_id": cycle_id},
        {
            "$set": {
                "status": STATUS_FAILED,
                "updatedAt": now,
                "error": f"{stage}: {error}",
                "stageOwner": None,
                "leaseExpiresAt": None,
                "leaseHeartbeatAt": None,
            }
        },
    )
    logger.error(
        "STORM_STAGE_FAILED stage=%s cycleId=%s error=%s",
        stage,
        cycle_id,
        error,
    )


def mark_failed_recovery_required(
    cycle_id: str,
    *,
    stage: str,
    reason: str,
    reclaimed: bool = False,
) -> Optional[dict[str, Any]]:
    """
    Terminal operator-review state. Does NOT re-queue the stage.

    Used when a lease expires or a crash leaves append-only / mitigation-
    adjacent work in an ambiguous state.
    """
    now = utc_now()
    doc = _db()[COLLECTION].find_one_and_update(
        {
            "_id": cycle_id,
            "status": {"$in": list(RUNNING_STATUSES)},
        },
        {
            "$set": {
                "status": STATUS_FAILED_RECOVERY_REQUIRED,
                "updatedAt": now,
                "error": f"{stage}: {reason}",
                "reclaimed": bool(reclaimed),
                "reclaimedAt": now if reclaimed else None,
                "stageOwner": None,
                "leaseExpiresAt": None,
                "leaseHeartbeatAt": None,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if doc:
        logger.error(
            "STORM_STAGE_RECOVERY_REQUIRED stage=%s cycleId=%s reclaimed=%s reason=%s",
            stage,
            cycle_id,
            bool(reclaimed),
            reason,
        )
    return doc


def heartbeat_cycle_lease(
    cycle_id: str,
    *,
    owner: Optional[str],
    stage: str,
) -> Optional[dict[str, Any]]:
    """Extend lease for the owning worker. Never steals another owner's lease."""
    now = utc_now()
    seconds = max(int(LEASE_SECONDS.get(stage, 15 * 60)), 60)
    expected_status = {
        "stats": STATUS_STATS_RUNNING,
        "analysis": STATUS_ANALYSIS_RUNNING,
        "confirmation": STATUS_CONFIRMATION_RUNNING,
        "safety": STATUS_SAFETY_RUNNING,
    }.get(stage)
    if not expected_status:
        return None

    query: dict[str, Any] = {"_id": cycle_id, "status": expected_status}
    if owner is not None:
        query["stageOwner"] = owner

    doc = _db()[COLLECTION].find_one_and_update(
        query,
        {
            "$set": {
                "leaseHeartbeatAt": now,
                "leaseExpiresAt": now + timedelta(seconds=seconds),
                "updatedAt": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if doc:
        logger.info(
            "STORM_LEASE_HEARTBEAT stage=%s cycleId=%s owner=%s leaseExpiresAt=%s",
            stage,
            cycle_id,
            owner or "-",
            doc.get("leaseExpiresAt"),
        )
    return doc


def reclaim_expired_running_cycles(*, leader_id: Optional[str] = None) -> dict[str, Any]:
    """
    Leader-only crash detection.

    NEVER re-runs confirmation/safety/analysis/mitigation.
    Marks expired running cycles as ``failed_recovery_required``.
    """
    if not lease_reclaim_enabled():
        logger.info(
            "STORM_LEASE_RECLAIM skipped enabled=false leader=%s",
            leader_id or "-",
        )
        return {"scanned": 0, "reclaimed": 0, "disabled": True}

    now = utc_now()
    scanned = 0
    reclaimed = 0
    cursor = _db()[COLLECTION].find(
        {
            "status": {"$in": list(RUNNING_STATUSES)},
            "leaseExpiresAt": {"$lt": now},
        }
    )
    for doc in cursor:
        scanned += 1
        cycle_id = doc.get("_id")
        status = doc.get("status")
        stage = _STATUS_TO_STAGE.get(status, "unknown")
        # Atomic: only reclaim if still expired and still running.
        updated = _db()[COLLECTION].find_one_and_update(
            {
                "_id": cycle_id,
                "status": status,
                "leaseExpiresAt": {"$lt": now},
            },
            {
                "$set": {
                    "status": STATUS_FAILED_RECOVERY_REQUIRED,
                    "updatedAt": now,
                    "error": (
                        f"{stage}: lease expired without completion — "
                        "manual recovery required (no auto re-run; mitigation not re-fired)"
                    ),
                    "reclaimed": True,
                    "reclaimedAt": now,
                    "reclaimedBy": leader_id,
                    "stageOwner": None,
                    "leaseExpiresAt": None,
                    "leaseHeartbeatAt": None,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated:
            reclaimed += 1
            logger.error(
                "STORM_LEASE_RECLAIMED stage=%s cycleId=%s leader=%s "
                "previousOwner=%s reclaimed=true autoRetry=false",
                stage,
                cycle_id,
                leader_id or "-",
                doc.get("stageOwner") or "-",
            )
    if scanned:
        logger.info(
            "STORM_LEASE_RECLAIM_SCAN scanned=%s reclaimed=%s leader=%s",
            scanned,
            reclaimed,
            leader_id or "-",
        )
    return {"scanned": scanned, "reclaimed": reclaimed, "disabled": False}


def claim_next_for_analysis(*, owner: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Oldest stats_complete cycle → analysis_running."""
    now = utc_now()
    lease = _lease_fields("analysis", owner)
    doc = _db()[COLLECTION].find_one_and_update(
        {"status": STATUS_STATS_COMPLETE},
        {
            "$set": {
                "status": STATUS_ANALYSIS_RUNNING,
                "analysisStartedAt": now,
                "updatedAt": now,
                **lease,
            }
        },
        sort=[("createdAt", ASCENDING)],
        return_document=ReturnDocument.AFTER,
    )
    if doc:
        logger.info(
            "STORM_STAGE_START stage=analysis cycleId=%s owner=%s leaseExpiresAt=%s",
            doc["_id"],
            lease.get("stageOwner") or "-",
            lease.get("leaseExpiresAt"),
        )
    return doc


def mark_analysis_complete(
    cycle_id: str,
    summary: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    now = utc_now()
    doc = _transition(
        cycle_id,
        from_status=STATUS_ANALYSIS_RUNNING,
        to_status=STATUS_ANALYSIS_COMPLETE,
        extra={
            "analysisCompletedAt": now,
            "riskPublishedAt": now,
            "analysisSummary": summary,
        },
    )
    if doc:
        started = doc.get("analysisStartedAt") or now
        if getattr(started, "tzinfo", None) is None:
            from datetime import timezone  # noqa: PLC0415

            started = started.replace(tzinfo=timezone.utc)
        duration_ms = int(max((now - started).total_seconds(), 0) * 1000)
        logger.info(
            "STORM_STAGE_COMPLETE stage=analysis cycleId=%s durationMs=%s "
            "riskPublishedAt=%s riskWritten=%s eligibilityTotal=%s errors=%s",
            cycle_id,
            duration_ms,
            now.isoformat(),
            (summary or {}).get("riskTotal"),
            (summary or {}).get("eligibilityTotal"),
            (summary or {}).get("errors"),
        )
    return doc


def claim_next_for_confirmation(*, owner: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Oldest analysis_complete (risk published) → confirmation_running."""
    now = utc_now()
    lease = _lease_fields("confirmation", owner)
    doc = _db()[COLLECTION].find_one_and_update(
        {"status": STATUS_ANALYSIS_COMPLETE},
        {
            "$set": {
                "status": STATUS_CONFIRMATION_RUNNING,
                "confirmationStartedAt": now,
                "updatedAt": now,
                **lease,
            }
        },
        sort=[("createdAt", ASCENDING)],
        return_document=ReturnDocument.AFTER,
    )
    if doc:
        logger.info(
            "STORM_STAGE_START stage=confirmation cycleId=%s owner=%s "
            "leaseExpiresAt=%s riskPublishedAt=%s",
            doc["_id"],
            lease.get("stageOwner") or "-",
            lease.get("leaseExpiresAt"),
            doc.get("riskPublishedAt"),
        )
    return doc


def mark_confirmation_complete(
    cycle_id: str,
    summary: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    now = utc_now()
    doc = _transition(
        cycle_id,
        from_status=STATUS_CONFIRMATION_RUNNING,
        to_status=STATUS_CONFIRMATION_COMPLETE,
        extra={
            "confirmationCompletedAt": now,
            "confirmationSummary": summary,
        },
    )
    if doc:
        started = doc.get("confirmationStartedAt") or now
        if getattr(started, "tzinfo", None) is None:
            from datetime import timezone  # noqa: PLC0415

            started = started.replace(tzinfo=timezone.utc)
        duration_ms = int(max((now - started).total_seconds(), 0) * 1000)
        logger.info(
            "STORM_STAGE_COMPLETE stage=confirmation cycleId=%s durationMs=%s "
            "total=%s confirmed=%s errors=%s",
            cycle_id,
            duration_ms,
            (summary or {}).get("total"),
            (summary or {}).get("confirmed"),
            (summary or {}).get("errors"),
        )
    return doc


def claim_next_for_safety(*, owner: Optional[str] = None) -> Optional[dict[str, Any]]:
    now = utc_now()
    lease = _lease_fields("safety", owner)
    doc = _db()[COLLECTION].find_one_and_update(
        {"status": STATUS_CONFIRMATION_COMPLETE},
        {
            "$set": {
                "status": STATUS_SAFETY_RUNNING,
                "safetyStartedAt": now,
                "updatedAt": now,
                **lease,
            }
        },
        sort=[("createdAt", ASCENDING)],
        return_document=ReturnDocument.AFTER,
    )
    if doc:
        logger.info(
            "STORM_STAGE_START stage=safety cycleId=%s owner=%s leaseExpiresAt=%s",
            doc["_id"],
            lease.get("stageOwner") or "-",
            lease.get("leaseExpiresAt"),
        )
    return doc


def mark_safety_complete(
    cycle_id: str,
    summary: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    now = utc_now()
    doc = _transition(
        cycle_id,
        from_status=STATUS_SAFETY_RUNNING,
        to_status=STATUS_SAFETY_COMPLETE,
        extra={
            "safetyCompletedAt": now,
            "safetySummary": summary,
        },
    )
    if doc:
        started = doc.get("safetyStartedAt") or now
        if getattr(started, "tzinfo", None) is None:
            from datetime import timezone  # noqa: PLC0415

            started = started.replace(tzinfo=timezone.utc)
        duration_ms = int(max((now - started).total_seconds(), 0) * 1000)
        logger.info(
            "STORM_STAGE_COMPLETE stage=safety cycleId=%s durationMs=%s summary=%s",
            cycle_id,
            duration_ms,
            summary,
        )
    return doc


def get_cycle(cycle_id: str) -> Optional[dict[str, Any]]:
    return _db()[COLLECTION].find_one({"_id": cycle_id})
