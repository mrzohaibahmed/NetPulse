"""
Dedicated ISP connectivity monitoring — ping cycle and atomic status writes.

Separate from device monitoring: no ping history, alerts, or device collection writes.
Reuses ping_service ICMP execution and the same freshness CAS / hysteresis semantics.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Literal

from pymongo import ReturnDocument

from models.isp_connection import STATUS_OFFLINE, STATUS_ONLINE
from services.mongo_retry import with_mongo_retry
from services.ping_service import ping_device
from services.scheduler_ownership import (
    CycleLeadershipGuard,
    require_scheduler_leadership,
)
from services.settings_service import (
    get_failure_confirmation_scans,
    get_monitor_ping_concurrency,
    get_ping_config,
)
from utils.monitor_logger import get_monitor_logger
from utils.utc import ensure_utc, utc_now

logger = get_monitor_logger("isp.monitor")

ApplyDisposition = Literal["applied", "idempotent", "stale", "unmatched"]


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _new_attempt_id() -> str:
    return uuid.uuid4().hex


def _freshness_filter(isp_id: str, attempt_id: str, ping_started_at) -> dict[str, Any]:
    return {
        "_id": isp_id,
        "lastPingAttemptId": {"$ne": attempt_id},
        "$or": [
            {"lastPingStartedAt": {"$exists": False}},
            {"lastPingStartedAt": None},
            {"lastPingStartedAt": {"$lte": ping_started_at}},
        ],
    }


def _log_stale_rejection(
    *,
    isp: dict,
    result: dict,
    attempt_id: str,
    cycle_id: str | None,
    scan_type: str,
    current_doc: dict | None,
):
    logger.warning(
        "stale_isp_ping_rejected | ispId=%s | name=%s | target=%s | attemptId=%s | "
        "cycleId=%s | scanType=%s | pingStartedAt=%s | currentLastPingStartedAt=%s | "
        "currentLastPingAttemptId=%s | currentStatus=%s | resultRejected=true",
        isp.get("_id"),
        isp.get("name", "unknown"),
        isp.get("target", ""),
        attempt_id,
        cycle_id,
        scan_type,
        result.get("pingStartedAt"),
        (current_doc or {}).get("lastPingStartedAt"),
        (current_doc or {}).get("lastPingAttemptId"),
        (current_doc or {}).get("status"),
    )


def _recover_attempt_doc(isp_id: str, attempt_id: str) -> dict | None:
    existing = _db().ispConnections.find_one({"_id": isp_id})
    if existing is None:
        return None
    if existing.get("lastPingAttemptId") == attempt_id:
        return existing
    return None


def _classify_unmatched(isp_id: str, attempt_id: str) -> tuple[dict | None, ApplyDisposition]:
    recovered = _recover_attempt_doc(isp_id, attempt_id)
    if recovered is not None:
        return recovered, "idempotent"
    existing = _db().ispConnections.find_one({"_id": isp_id})
    if existing is None:
        return None, "unmatched"
    return None, "stale"


def _atomic_mark_online(
    *,
    isp_id: str,
    result: dict,
    now,
    attempt_id: str,
    ping_started_at,
) -> tuple[dict | None, ApplyDisposition]:
    last_seen = ensure_utc(result.get("lastSeen")) or now

    def _update_once():
        doc = _db().ispConnections.find_one_and_update(
            _freshness_filter(isp_id, attempt_id, ping_started_at),
            {
                "$set": {
                    "status": STATUS_ONLINE,
                    "responseTime": result.get("responseTime"),
                    "lastSeen": last_seen,
                    "lastCheckedAt": now,
                    "updatedAt": now,
                    "consecutiveFailures": 0,
                    "lastPingAttemptId": attempt_id,
                    "lastPingStartedAt": ping_started_at,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if doc is not None:
            return doc, "applied"
        return _classify_unmatched(isp_id, attempt_id)

    doc, disposition = with_mongo_retry(
        _update_once,
        action="isp_mark_online",
        device_id=isp_id,
        ip_address=result.get("target"),
        idempotent=True,
    )
    return doc, disposition


def _atomic_mark_failure(
    *,
    isp_id: str,
    now,
    attempt_id: str,
    ping_started_at,
) -> tuple[dict | None, ApplyDisposition]:
    threshold = get_failure_confirmation_scans()

    def _update_once():
        pipeline = [
            {
                "$set": {
                    "consecutiveFailures": {
                        "$add": [{"$ifNull": ["$consecutiveFailures", 0]}, 1]
                    },
                    "responseTime": None,
                    "lastCheckedAt": now,
                    "updatedAt": now,
                    "lastPingAttemptId": attempt_id,
                    "lastPingStartedAt": ping_started_at,
                    "status": {
                        "$cond": [
                            {
                                "$gte": [
                                    {
                                        "$add": [
                                            {"$ifNull": ["$consecutiveFailures", 0]},
                                            1,
                                        ]
                                    },
                                    threshold,
                                ]
                            },
                            STATUS_OFFLINE,
                            "$status",
                        ]
                    },
                }
            }
        ]
        doc = _db().ispConnections.find_one_and_update(
            _freshness_filter(isp_id, attempt_id, ping_started_at),
            pipeline,
            return_document=ReturnDocument.AFTER,
        )
        if doc is not None:
            return doc, "applied"
        return _classify_unmatched(isp_id, attempt_id)

    doc, disposition = with_mongo_retry(
        _update_once,
        action="isp_mark_failure",
        device_id=isp_id,
        idempotent=True,
    )
    return doc, disposition


def apply_isp_ping_result(
    isp: dict,
    result: dict,
    scan_type: str = "Automatic",
    *,
    cycle_id: str | None = None,
    attempt_id: str | None = None,
) -> str:
    """Atomically update ISP fields from one completed ping scan."""
    now = utc_now()
    attempt_id = attempt_id or _new_attempt_id()
    isp_id = isp.get("_id")
    previous_status = isp.get("status", "Unknown")
    ping_started_at = ensure_utc(result.get("pingStartedAt")) or now
    ping_completed_at = ensure_utc(result.get("pingCompletedAt")) or now
    result = {
        **result,
        "pingStartedAt": ping_started_at,
        "pingCompletedAt": ping_completed_at,
    }

    if result.get("success"):
        updated, disposition = _atomic_mark_online(
            isp_id=isp_id,
            result=result,
            now=now,
            attempt_id=attempt_id,
            ping_started_at=ping_started_at,
        )
    else:
        updated, disposition = _atomic_mark_failure(
            isp_id=isp_id,
            now=now,
            attempt_id=attempt_id,
            ping_started_at=ping_started_at,
        )

    if disposition == "stale":
        current = _db().ispConnections.find_one({"_id": isp_id})
        _log_stale_rejection(
            isp=isp,
            result=result,
            attempt_id=attempt_id,
            cycle_id=cycle_id,
            scan_type=scan_type,
            current_doc=current,
        )
        return previous_status
    if updated is None:
        logger.warning(
            "ISP ping apply unmatched | ispId=%s | attemptId=%s | scanType=%s",
            isp_id,
            attempt_id,
            scan_type,
        )
        return previous_status

    new_status = updated.get("status") or previous_status
    logger.info(
        "ISP ping applied | cycleId=%s | attemptId=%s | ispId=%s | name=%s | "
        "target=%s | previous=%s | new=%s | success=%s | responseTime=%s | "
        "consecutiveFailures=%s | scanType=%s | disposition=%s | "
        "pingStartedAt=%s | pingCompletedAt=%s",
        cycle_id,
        attempt_id,
        isp_id,
        isp.get("name"),
        isp.get("target"),
        previous_status,
        new_status,
        bool(result.get("success")),
        result.get("responseTime"),
        updated.get("consecutiveFailures", 0),
        scan_type,
        disposition,
        ping_started_at,
        ping_completed_at,
    )

    if disposition in ("applied", "idempotent"):
        from services.isp_alert_service import (  # noqa: PLC0415
            maybe_send_isp_offline_alert,
            resolve_isp_offline_alerts,
        )

        consecutive = int(updated.get("consecutiveFailures") or 0)
        if result.get("success"):
            resolve_isp_offline_alerts(
                updated,
                scan_type=scan_type,
                cycle_id=cycle_id,
            )
        else:
            maybe_send_isp_offline_alert(
                updated,
                consecutive_failures=consecutive,
                scan_type=scan_type,
                cycle_id=cycle_id,
                attempt_id=attempt_id,
            )

    return new_status


def _should_check_now(isp: dict, now) -> bool:
    config = get_ping_config()
    interval = config["interval"]
    last_checked = ensure_utc(isp.get("lastCheckedAt"))
    if not last_checked:
        return True
    elapsed = (now - last_checked).total_seconds()
    return elapsed >= interval


def _ping_isp_target(isp: dict) -> dict:
    target = (isp.get("target") or "").strip()
    pseudo_device = {"hostname": isp.get("name", "isp")}
    return ping_device(target, critical=False, device=pseudo_device)


def scan_isp_connection(
    isp: dict,
    *,
    scan_type: str = "Automatic",
    cycle_id: str | None = None,
) -> dict:
    """Ping one ISP target and apply the result."""
    target = (isp.get("target") or "").strip()
    if not target:
        raise ValueError("ISP target is not configured")

    attempt_id = _new_attempt_id()
    logger.info(
        "ISP scan started | cycleId=%s | attemptId=%s | ispId=%s | name=%s | target=%s",
        cycle_id,
        attempt_id,
        isp.get("_id"),
        isp.get("name"),
        target,
    )
    result = _ping_isp_target(isp)
    apply_isp_ping_result(
        isp,
        result,
        scan_type=scan_type,
        cycle_id=cycle_id,
        attempt_id=attempt_id,
    )
    return result


def _scan_isp_safe(isp: dict, *, cycle_id: str) -> str:
    try:
        scan_isp_connection(isp, cycle_id=cycle_id)
        return "scanned"
    except Exception as error:  # noqa: BLE001
        logger.exception(
            "ISP scan failed | cycleId=%s | ispId=%s | name=%s | error=%s",
            cycle_id,
            isp.get("_id"),
            isp.get("name"),
            error,
        )
        return "failed"


def monitor_all_isp_connections() -> None:
    """
    Scan enabled ISP connections on the scheduler interval.

    Independent job body — does not touch device monitoring or storm pipelines.
    """
    if not require_scheduler_leadership("isp_monitor_job"):
        return

    cycle_id = uuid.uuid4().hex[:12]
    guard = CycleLeadershipGuard(cycle_id=cycle_id)
    if not guard.ensure(force=True, reason="cycle_start"):
        return

    concurrency = min(get_monitor_ping_concurrency(), 3)
    ping_cfg = get_ping_config()

    logger.info(
        "ISP monitoring cycle started | cycleId=%s | pingConcurrency=%s | "
        "timeoutMs=%s | retries=%s",
        cycle_id,
        concurrency,
        ping_cfg["timeout_ms"],
        ping_cfg["retries"],
    )

    now = utc_now()
    try:
        isps = list(_db().ispConnections.find({"monitor": True}))
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to load ISP connections | cycleId=%s | error=%s",
            cycle_id,
            exc,
        )
        return

    scanned = 0
    skipped = 0
    failed = 0
    aborted = False
    due_isps: list[dict] = []

    for index, isp in enumerate(isps):
        guard.note_device_visited()
        if not guard.ensure(reason=f"select:{index}"):
            aborted = True
            break
        if not (isp.get("target") or "").strip():
            skipped += 1
            continue
        if not _should_check_now(isp, now):
            skipped += 1
            continue
        due_isps.append(isp)

    if not aborted and due_isps:
        workers = min(concurrency, len(due_isps))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_scan_isp_safe, isp, cycle_id=cycle_id)
                for isp in due_isps
            ]
            for fut in as_completed(futures):
                outcome = fut.result()
                if outcome == "scanned":
                    scanned += 1
                else:
                    failed += 1

    logger.info(
        "ISP monitoring cycle finished | cycleId=%s | enabled=%s due=%s scanned=%s "
        "skipped=%s failed=%s | aborted=%s | abortReason=%s",
        cycle_id,
        len(isps),
        len(due_isps),
        scanned,
        skipped,
        failed,
        aborted,
        guard.abort_reason,
    )
