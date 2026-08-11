"""
Leader-only due-device dispatcher for dispatch-mode monitoring (Phase 4).

Does not ping devices directly. Claims due devices up to free worker capacity
and submits them to ``monitor_runtime``. Legacy ``monitor_all_devices`` is
untouched and remains the default scheduler path.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from services.collector_health import begin_cycle_connectivity_check
from services.monitor_claim import (
    build_due_unclaimed_filter,
    claim_device,
)
from services.monitor_integrity import run_integrity_audit
from services.monitor_metrics import get_dispatch_metrics
from services.monitor_runtime import (
    get_monitor_runtime,
    get_monitor_runtime_stats,
    signal_monitor_runtime_leadership_lost,
    start_monitor_runtime,
    submit_claimed_device,
)
from services.scheduler_ownership import (
    CycleLeadershipGuard,
    require_scheduler_leadership,
)
from utils.monitor_logger import get_monitor_logger
from utils.utc import ensure_utc, utc_now

logger = get_monitor_logger("monitor_dispatch")

DISPATCH_JOB_NAME = "device_monitor_dispatch_job"
INTEGRITY_MIN_INTERVAL_SECONDS = 60

_integrity_lock = threading.Lock()
_last_integrity_at = None

_DEVICE_PROJECTION = {
    "_id": 1,
    "hostname": 1,
    "ipAddress": 1,
    "monitor": 1,
    "critical": 1,
    "status": 1,
    "pingInterval": 1,
    "pingTimeoutMs": 1,
    "pingRetries": 1,
    "nextCheckAt": 1,
    "lastPingStartedAt": 1,
    "scanClaimId": 1,
    "scanClaimExpiresAt": 1,
}


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def free_worker_capacity() -> int:
    """How many additional claimed devices the runtime can accept."""
    stats = get_monitor_runtime_stats()
    if not stats.get("started"):
        return 0
    concurrency = int(stats.get("concurrency") or 0)
    occupancy = int(stats.get("occupancy") or 0)
    return max(0, concurrency - occupancy)


def _maybe_run_integrity_audit(cycle_id: str) -> None:
    """Throttle integrity audits to roughly once per 60s in dispatch mode."""
    global _last_integrity_at
    now = utc_now()
    with _integrity_lock:
        if _last_integrity_at is not None:
            elapsed = (now - _last_integrity_at).total_seconds()
            if elapsed < INTEGRITY_MIN_INTERVAL_SECONDS:
                return
        _last_integrity_at = now

    try:
        run_integrity_audit(cycle_id=cycle_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Integrity audit error | dispatchId=%s | error=%s",
            cycle_id,
            exc,
        )


def _had_expired_claim(device: dict[str, Any], now) -> bool:
    """True when the candidate still carried an expired claim id (reclaim path)."""
    if not device.get("scanClaimId"):
        return False
    expires = ensure_utc(device.get("scanClaimExpiresAt"))
    if expires is None:
        return False
    return expires <= now


def _emit_heartbeat(dispatch_id: str) -> None:
    stats = get_monitor_runtime_stats()
    get_dispatch_metrics().maybe_emit_heartbeat(
        dispatch_id=dispatch_id,
        workers_active=int(stats.get("workers_active") or 0),
        queue_depth=int(stats.get("queue_depth") or 0),
        workers_total=int(stats.get("workers_total") or 0),
    )


def dispatch_monitor_due_devices() -> dict[str, Any]:
    """
    APScheduler entrypoint for dispatch mode.

    Leader-only. Claims at most ``free_worker_capacity`` due devices and submits
    them to the bounded runtime. Returns quickly without waiting for pings.
    """
    metrics = get_dispatch_metrics()
    summary: dict[str, Any] = {
        "skipped": False,
        "reason": None,
        "candidates": 0,
        "claimed": 0,
        "submitted": 0,
        "claim_conflicts": 0,
        "aborted": False,
    }

    if not require_scheduler_leadership(DISPATCH_JOB_NAME):
        # Former leader may still have a live runtime with queued work — drain it.
        signal_monitor_runtime_leadership_lost()
        summary["skipped"] = True
        summary["reason"] = "not_leader"
        return summary

    runtime = start_monitor_runtime()
    # Recover from a prior leadership-loss signal if we still/again hold the lease.
    runtime.clear_leadership_lost()

    dispatch_id = uuid.uuid4().hex[:12]
    guard = CycleLeadershipGuard(cycle_id=dispatch_id)
    if not guard.ensure(force=True, reason="dispatch_start"):
        signal_monitor_runtime_leadership_lost()
        summary["skipped"] = True
        summary["reason"] = "leadership_lost_start"
        summary["aborted"] = True
        _emit_heartbeat(dispatch_id)
        return summary

    suppress_offline = begin_cycle_connectivity_check(dispatch_id)

    free = free_worker_capacity()
    if free <= 0:
        # Quiet on the hot path — heartbeat carries capacity pressure.
        logger.debug(
            "Dispatch tick idle - no free capacity | dispatchId=%s | "
            "workers_active=%s | queue_depth=%s",
            dispatch_id,
            get_monitor_runtime_stats().get("workers_active"),
            get_monitor_runtime_stats().get("queue_depth"),
        )
        summary["reason"] = "no_capacity"
        _maybe_run_integrity_audit(dispatch_id)
        _emit_heartbeat(dispatch_id)
        return summary

    now = utc_now()
    query = build_due_unclaimed_filter(now)

    try:
        # Bound the read set to free slots — never load the full inventory.
        candidates = list(
            _db()
            .devices.find(query, _DEVICE_PROJECTION)
            .sort([("nextCheckAt", 1)])
            .limit(free)
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Dispatch candidate query failed | dispatchId=%s | error=%s",
            dispatch_id,
            exc,
        )
        summary["reason"] = "query_failed"
        summary["error"] = str(exc)
        _emit_heartbeat(dispatch_id)
        return summary

    summary["candidates"] = len(candidates)
    # Per-tick detail stays at DEBUG; heartbeat (~45s) is the INFO summary.
    logger.debug(
        "Dispatch tick started | dispatchId=%s | freeCapacity=%s | candidates=%s | "
        "partitionSuppress=%s",
        dispatch_id,
        free,
        len(candidates),
        suppress_offline,
    )

    for index, device in enumerate(candidates):
        if free_worker_capacity() <= 0:
            break

        guard.note_device_visited()
        if not guard.ensure(reason=f"dispatch_claim:{index}"):
            signal_monitor_runtime_leadership_lost()
            summary["aborted"] = True
            summary["reason"] = "leadership_lost"
            logger.error(
                "Dispatch aborted - leadership lost | dispatchId=%s | "
                "claimed=%s | submitted=%s",
                dispatch_id,
                summary["claimed"],
                summary["submitted"],
            )
            break

        device_id = device.get("_id")
        due_at = device.get("nextCheckAt")
        previous_ping_started_at = device.get("lastPingStartedAt")
        expired_reclaim = _had_expired_claim(device, now)

        claimed_doc = claim_device(device_id, device=device, now=now)
        if claimed_doc is None:
            summary["claim_conflicts"] += 1
            metrics.incr_claims_conflict()
            continue

        summary["claimed"] += 1
        metrics.incr_claims_won()
        if expired_reclaim:
            metrics.incr_claim_expired_reclaims()

        claim_id = claimed_doc.get("scanClaimId")
        claimed_at = claimed_doc.get("scanClaimedAt") or now
        next_check_at = claimed_doc.get("nextCheckAt")
        if not claim_id:
            logger.error(
                "Claim succeeded without scanClaimId | dispatchId=%s | deviceId=%s",
                dispatch_id,
                device_id,
            )
            continue

        # If lease was lost during the claim write, do not submit — release and abort.
        if not guard.ensure(force=True, reason=f"dispatch_post_claim:{index}"):
            try:
                from services.monitor_claim import release_device_claim  # noqa: PLC0415

                release_device_claim(device_id, claim_id)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Post-claim release after leadership loss failed | "
                    "dispatchId=%s | deviceId=%s | claimId=%s | error=%s",
                    dispatch_id,
                    device_id,
                    claim_id,
                    exc,
                )
            signal_monitor_runtime_leadership_lost()
            summary["aborted"] = True
            summary["reason"] = "leadership_lost_after_claim"
            summary["claimed"] = max(0, summary["claimed"] - 1)
            logger.error(
                "Dispatch aborted after claim - leadership lost | dispatchId=%s | "
                "deviceId=%s | claimId=%s",
                dispatch_id,
                device_id,
                claim_id,
            )
            break

        accepted = submit_claimed_device(
            claimed_doc,
            claim_id,
            suppress_offline=suppress_offline,
            cycle_id=dispatch_id,
            due_at=due_at,
            claimed_at=claimed_at,
            previous_ping_started_at=previous_ping_started_at,
            next_check_at=next_check_at,
        )
        if accepted:
            summary["submitted"] += 1

    logger.debug(
        "Dispatch tick finished | dispatchId=%s | candidates=%s | claimed=%s | "
        "submitted=%s | conflicts=%s | aborted=%s",
        dispatch_id,
        summary["candidates"],
        summary["claimed"],
        summary["submitted"],
        summary["claim_conflicts"],
        summary["aborted"],
    )

    if not summary["aborted"]:
        _maybe_run_integrity_audit(dispatch_id)

    _emit_heartbeat(dispatch_id)
    return summary
