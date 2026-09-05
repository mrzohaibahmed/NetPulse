"""
Device reachability monitoring — ping cycle and atomic status writes.

Correctness guarantees (final hardening):
  - Scheduler leadership checked with time + device heartbeats; loss aborts cycle.
  - Each ping attempt has an attemptId so $inc / history / alerts are idempotent.
  - lastPingStartedAt provides cross-attempt freshness (older results cannot overwrite).
  - Offline status requires consecutive failed SCANS (hysteresis), not one blip.
  - Devices remain the source of truth; events are advisory only.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Literal

from pymongo import ReturnDocument

from services.alert_service import (
    maybe_send_critical_offline_alert,
    resolve_critical_offline_alerts,
)
from services.collector_health import begin_cycle_connectivity_check
from services.history_service import save_ping_history
from services.mongo_retry import with_mongo_retry
from services.monitor_events import (
    EVENT_DASHBOARD_METRICS_CHANGED,
    EVENT_DEVICE_RECOVERED,
    EVENT_DEVICE_STATUS_CHANGED,
    publish,
)
from services.monitor_integrity import run_integrity_audit
from services.ping_service import STATUS_ONLINE, ping_device
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

logger = get_monitor_logger("monitor")

ApplyDisposition = Literal["applied", "idempotent", "stale", "unmatched"]


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _new_attempt_id() -> str:
    """Unique id for one ping apply — used for idempotent Mongo writes."""
    return uuid.uuid4().hex


def _should_check_now(device, now):
    """Honor per-device interval override within the global scheduler cycle (FR8.1)."""
    config = get_ping_config(device)
    interval = config["interval"]
    last_checked = ensure_utc(device.get("lastCheckedAt"))
    if not last_checked:
        return True

    elapsed = (now - last_checked).total_seconds()
    return elapsed >= interval


def _freshness_filter(device_id, attempt_id: str, ping_started_at) -> dict[str, Any]:
    """
    Atomic CAS: same attempt cannot re-apply; older starts cannot overwrite newer.
    Missing lastPingStartedAt allows the first write (legacy devices).
    """
    return {
        "_id": device_id,
        "lastPingAttemptId": {"$ne": attempt_id},
        "$or": [
            {"lastPingStartedAt": {"$exists": False}},
            {"lastPingStartedAt": None},
            {"lastPingStartedAt": {"$lte": ping_started_at}},
        ],
    }


def _log_stale_rejection(
    *,
    device,
    result,
    attempt_id: str,
    cycle_id: str | None,
    scan_type: str,
    current_doc: dict[str, Any] | None,
):
    logger.warning(
        "stale_ping_result_rejected | reason=stale_ping_result_rejected | "
        "deviceId=%s | hostname=%s | ip=%s | attemptId=%s | cycleId=%s | "
        "scanType=%s | pingStartedAt=%s | pingCompletedAt=%s | "
        "currentLastPingStartedAt=%s | currentLastPingAttemptId=%s | "
        "currentStatus=%s | resultRejected=true",
        device.get("_id"),
        device.get("hostname", "unknown"),
        device.get("ipAddress", "unknown"),
        attempt_id,
        cycle_id,
        scan_type,
        result.get("pingStartedAt"),
        result.get("pingCompletedAt"),
        (current_doc or {}).get("lastPingStartedAt"),
        (current_doc or {}).get("lastPingAttemptId"),
        (current_doc or {}).get("status"),
    )


def apply_ping_result(
    device,
    result,
    scan_type="Automatic",
    *,
    suppress_offline: bool = False,
    cycle_id: str | None = None,
    attempt_id: str | None = None,
):
    """
    Atomically update device fields and history from a ping result (FR3.4, FR3.5).

    ``attempt_id`` scopes idempotent $inc / history / alert writes for one ping.
    ``pingStartedAt`` on the result provides cross-attempt freshness ordering.
    """
    now = utc_now()
    attempt_id = attempt_id or _new_attempt_id()
    device_id = device.get("_id")
    hostname = device.get("hostname", "unknown")
    ip_address = device.get("ipAddress", "unknown")
    previous_status = device.get("status", "Unknown")
    ping_started_at = ensure_utc(result.get("pingStartedAt")) or now
    ping_completed_at = ensure_utc(result.get("pingCompletedAt")) or now
    # Ensure result carries resolved timestamps for logging / persistence.
    result = {
        **result,
        "pingStartedAt": ping_started_at,
        "pingCompletedAt": ping_completed_at,
    }

    if suppress_offline and not result.get("success"):
        logger.warning(
            "Offline transition suppressed (collector partition) | "
            "cycleId=%s | deviceId=%s | hostname=%s | ip=%s | wouldStatus=%s | "
            "attemptId=%s",
            cycle_id,
            device_id,
            hostname,
            ip_address,
            result.get("status"),
            attempt_id,
        )

        def _touch_checked():
            return _db().devices.find_one_and_update(
                {"_id": device_id},
                {"$set": {"lastCheckedAt": now, "updatedAt": now}},
                return_document=ReturnDocument.AFTER,
            )

        try:
            # $set-only on timestamps — safe to retry.
            with_mongo_retry(
                _touch_checked,
                action="device_touch_checked_partition",
                device_id=device_id,
                ip_address=ip_address,
                idempotent=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Partition touch write failed | deviceId=%s | ip=%s | error=%s",
                device_id,
                ip_address,
                exc,
            )
        return previous_status

    if result.get("success"):
        updated, disposition = _atomic_mark_online(
            device_id=device_id,
            ip_address=ip_address,
            result=result,
            now=now,
            attempt_id=attempt_id,
            ping_started_at=ping_started_at,
        )
        if disposition == "stale":
            current = _db().devices.find_one({"_id": device_id})
            _log_stale_rejection(
                device=device,
                result=result,
                attempt_id=attempt_id,
                cycle_id=cycle_id,
                scan_type=scan_type,
                current_doc=current,
            )
            return previous_status
        if updated is None:
            logger.warning(
                "Ping apply unmatched | deviceId=%s | attemptId=%s | scanType=%s | "
                "resultApplied=false",
                device_id,
                attempt_id,
                scan_type,
            )
            return previous_status

        consecutive = 0
        new_status = STATUS_ONLINE
    else:
        updated, disposition = _atomic_mark_failure(
            device_id=device_id,
            ip_address=ip_address,
            result=result,
            now=now,
            attempt_id=attempt_id,
            ping_started_at=ping_started_at,
        )
        if disposition == "stale":
            current = _db().devices.find_one({"_id": device_id})
            _log_stale_rejection(
                device=device,
                result=result,
                attempt_id=attempt_id,
                cycle_id=cycle_id,
                scan_type=scan_type,
                current_doc=current,
            )
            return previous_status
        if updated is None:
            logger.warning(
                "Ping apply unmatched | deviceId=%s | attemptId=%s | scanType=%s | "
                "resultApplied=false",
                device_id,
                attempt_id,
                scan_type,
            )
            return previous_status

        consecutive = int(updated.get("consecutiveFailures") or 0)
        # Authoritative status after hysteresis — may still be Online.
        new_status = updated.get("status") or previous_status

    logger.info(
        "Ping applied | cycleId=%s | attemptId=%s | deviceId=%s | hostname=%s | "
        "ip=%s | previous=%s | new=%s | success=%s | responseTime=%s | "
        "consecutiveFailures=%s | scanType=%s | disposition=%s | "
        "pingStartedAt=%s | pingCompletedAt=%s | resultApplied=true",
        cycle_id,
        attempt_id,
        device_id,
        hostname,
        ip_address,
        previous_status,
        new_status,
        bool(result.get("success")),
        result.get("responseTime"),
        consecutive,
        scan_type,
        disposition,
        ping_started_at,
        ping_completed_at,
    )

    try:
        save_ping_history(
            device=device,
            ping_result=result,
            scan_type=scan_type,
            cycle_id=cycle_id,
            attempt_id=attempt_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Ping history write failed after status update | "
            "deviceId=%s | ip=%s | attemptId=%s | error=%s",
            device_id,
            ip_address,
            attempt_id,
            exc,
        )

    # Events only after durable device write (advisory — never SoT).
    if previous_status != new_status:
        publish(
            EVENT_DEVICE_STATUS_CHANGED,
            {
                "deviceId": str(device_id) if device_id is not None else None,
                "hostname": hostname,
                "ipAddress": ip_address,
                "previousStatus": previous_status,
                "newStatus": new_status,
                "status": new_status,
                "scanType": scan_type,
                "cycleId": cycle_id,
                "attemptId": attempt_id,
            },
        )

    if result.get("success"):
        if previous_status != STATUS_ONLINE:
            publish(
                EVENT_DEVICE_RECOVERED,
                {
                    "deviceId": str(device_id) if device_id is not None else None,
                    "hostname": hostname,
                    "ipAddress": ip_address,
                    "previousStatus": previous_status,
                    "status": STATUS_ONLINE,
                    "cycleId": cycle_id,
                    "attemptId": attempt_id,
                },
            )
        resolve_critical_offline_alerts(
            device,
            scan_type=scan_type,
            cycle_id=cycle_id,
        )
    else:
        maybe_send_critical_offline_alert(
            device,
            previous_status,
            new_status,
            consecutive_failures=consecutive,
            scan_type=scan_type,
            cycle_id=cycle_id,
            attempt_id=attempt_id,
        )

    return previous_status


def _recover_attempt_doc(device_id, attempt_id: str) -> dict[str, Any] | None:
    """If a prior write for this attempt committed, return the current device doc."""
    existing = _db().devices.find_one({"_id": device_id})
    if existing is None:
        return None
    if existing.get("lastPingAttemptId") == attempt_id:
        return existing
    return None


def _classify_unmatched(
    device_id,
    attempt_id: str,
) -> tuple[dict[str, Any] | None, ApplyDisposition]:
    recovered = _recover_attempt_doc(device_id, attempt_id)
    if recovered is not None:
        return recovered, "idempotent"
    existing = _db().devices.find_one({"_id": device_id})
    if existing is None:
        return None, "unmatched"
    # Device exists but filter did not match → stale (or concurrent same-window).
    return None, "stale"


def _atomic_mark_online(
    *,
    device_id,
    ip_address,
    result,
    now,
    attempt_id: str,
    ping_started_at,
) -> tuple[dict[str, Any] | None, ApplyDisposition]:
    """Set Online + reset consecutiveFailures; idempotent + freshness-ordered."""
    last_seen = result.get("lastSeen") or now
    last_seen = ensure_utc(last_seen) or now

    def _update_once():
        doc = _db().devices.find_one_and_update(
            _freshness_filter(device_id, attempt_id, ping_started_at),
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
        return _classify_unmatched(device_id, attempt_id)

    try:
        doc, disposition = with_mongo_retry(
            _update_once,
            action="device_mark_online",
            device_id=device_id,
            ip_address=ip_address,
            idempotent=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Atomic online write failed | deviceId=%s | ip=%s | attemptId=%s | error=%s",
            device_id,
            ip_address,
            attempt_id,
            exc,
        )
        raise

    if disposition == "idempotent":
        logger.info(
            "Online write idempotent recovery | deviceId=%s | attemptId=%s",
            device_id,
            attempt_id,
        )
    elif disposition == "applied":
        logger.info(
            "Mongo write ok | action=device_mark_online | deviceId=%s | ip=%s | "
            "attemptId=%s | consecutiveFailures=0 | lastPingStartedAt=%s",
            device_id,
            ip_address,
            attempt_id,
            ping_started_at,
        )
    elif disposition == "unmatched":
        logger.warning(
            "Atomic online write unmatched | deviceId=%s | ip=%s | attemptId=%s",
            device_id,
            ip_address,
            attempt_id,
        )

    return doc, disposition


def _atomic_mark_failure(
    *,
    device_id,
    ip_address,
    result,
    now,
    attempt_id: str,
    ping_started_at,
) -> tuple[dict[str, Any] | None, ApplyDisposition]:
    """
    Increment consecutiveFailures at most once per attempt_id.

    Offline status is applied only when consecutiveFailures (after $inc) reaches
    pingFailureConfirmationScans. Freshness filter rejects older ping starts.
    """
    failure_status = result["status"]
    threshold = get_failure_confirmation_scans()

    def _update_once():
        # Aggregation pipeline: atomic $inc + conditional status in one write.
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
                            failure_status,
                            "$status",
                        ]
                    },
                }
            }
        ]
        doc = _db().devices.find_one_and_update(
            _freshness_filter(device_id, attempt_id, ping_started_at),
            pipeline,
            return_document=ReturnDocument.AFTER,
        )
        if doc is not None:
            return doc, "applied"
        return _classify_unmatched(device_id, attempt_id)

    try:
        doc, disposition = with_mongo_retry(
            _update_once,
            action="device_mark_failure",
            device_id=device_id,
            ip_address=ip_address,
            idempotent=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Atomic failure write failed | deviceId=%s | ip=%s | attemptId=%s | error=%s",
            device_id,
            ip_address,
            attempt_id,
            exc,
        )
        raise

    if disposition == "idempotent":
        logger.info(
            "Failure write idempotent recovery | deviceId=%s | attemptId=%s | "
            "consecutiveFailures=%s",
            device_id,
            attempt_id,
            (doc or {}).get("consecutiveFailures"),
        )
    elif disposition == "applied" and doc is not None:
        logger.info(
            "Mongo write ok | action=device_mark_failure | deviceId=%s | ip=%s | "
            "attemptId=%s | consecutiveFailures=%s | status=%s | "
            "confirmationThreshold=%s | lastPingStartedAt=%s",
            device_id,
            ip_address,
            attempt_id,
            doc.get("consecutiveFailures"),
            doc.get("status"),
            threshold,
            ping_started_at,
        )
    elif disposition == "unmatched":
        logger.warning(
            "Atomic failure write unmatched | deviceId=%s | ip=%s | attemptId=%s",
            device_id,
            ip_address,
            attempt_id,
        )

    return doc, disposition


def _scan_device(device, *, suppress_offline: bool, cycle_id: str):
    hostname = device.get("hostname", "unknown")
    ip_address = device.get("ipAddress", "unknown")
    device_id = device.get("_id")
    attempt_id = _new_attempt_id()

    logger.info(
        "Device scan started | cycleId=%s | attemptId=%s | deviceId=%s | "
        "hostname=%s | ip=%s",
        cycle_id,
        attempt_id,
        device_id,
        hostname,
        ip_address,
    )

    result = ping_device(
        ip_address,
        critical=bool(device.get("critical")),
        device=device,
    )
    apply_ping_result(
        device,
        result,
        scan_type="Automatic",
        suppress_offline=suppress_offline,
        cycle_id=cycle_id,
        attempt_id=attempt_id,
    )

    if result["status"] == STATUS_ONLINE:
        logger.info(
            "Device online | cycleId=%s | attemptId=%s | deviceId=%s | "
            "hostname=%s | ip=%s | response=%s ms | pingStartedAt=%s",
            cycle_id,
            attempt_id,
            device_id,
            hostname,
            ip_address,
            result["responseTime"],
            result.get("pingStartedAt"),
        )
    else:
        logger.warning(
            "Device down | cycleId=%s | attemptId=%s | deviceId=%s | "
            "hostname=%s | ip=%s | status=%s | message=%s | suppressed=%s | "
            "pingStartedAt=%s",
            cycle_id,
            attempt_id,
            device_id,
            hostname,
            ip_address,
            result["status"],
            result.get("message", "No response"),
            suppress_offline,
            result.get("pingStartedAt"),
        )
    return result


def _scan_device_safe(
    device,
    *,
    suppress_offline: bool,
    cycle_id: str,
    timing_out: dict | None = None,
) -> str:
    """Thread-pool worker wrapper — returns scanned|failed for cycle counters."""
    hostname = device.get("hostname", "unknown")
    ip_address = device.get("ipAddress", "unknown")
    device_id = device.get("_id")
    try:
        result = _scan_device(
            device,
            suppress_offline=suppress_offline,
            cycle_id=cycle_id,
        )
        if timing_out is not None and isinstance(result, dict):
            timing_out["pingStartedAt"] = result.get("pingStartedAt")
            timing_out["pingCompletedAt"] = result.get("pingCompletedAt")
            timing_out["responseTime"] = result.get("responseTime")
        return "scanned"
    except Exception as error:  # noqa: BLE001
        logger.exception(
            "Scan failed | cycleId=%s | deviceId=%s | hostname=%s | ip=%s | error=%s",
            cycle_id,
            device_id,
            hostname,
            ip_address,
            error,
        )
        return "failed"


def scan_claimed_device(
    device,
    *,
    claim_id: str,
    suppress_offline: bool = False,
    cycle_id: str,
    timing_out: dict | None = None,
) -> str:
    """
    Dispatch-mode scan entrypoint for a device that is already claimed.

    Requires a non-empty ``claim_id``. Delegates entirely to the existing
    ``_scan_device`` / ``ping_device`` / ``apply_ping_result`` pipeline via
    ``_scan_device_safe`` — no alternate ping or apply path.

    Optional ``timing_out`` receives ``pingStartedAt`` / ``pingCompletedAt``
    for dispatch observability (Phase 8); does not alter apply semantics.
    """
    device_id = (device or {}).get("_id")
    if not claim_id:
        logger.error(
            "Refusing scan - missing claimId | deviceId=%s | cycleId=%s",
            device_id,
            cycle_id,
        )
        return "failed"

    owned = (device or {}).get("scanClaimId")
    if owned is not None and owned != claim_id:
        logger.error(
            "Refusing scan - claimId mismatch | deviceId=%s | cycleId=%s | "
            "expected=%s | deviceClaimId=%s",
            device_id,
            cycle_id,
            claim_id,
            owned,
        )
        return "failed"

    return _scan_device_safe(
        device,
        suppress_offline=suppress_offline,
        cycle_id=cycle_id,
        timing_out=timing_out,
    )


def monitor_all_devices():
    """
    Scan all devices with monitor=True (FR2.1).

    Leader-only. Devices due for a check are pinged with bounded parallelism
    (pingConcurrency) so large inventories can finish within the scheduler
    interval. Leadership is renewed between batches; lease loss aborts further
    batches (in-flight batch workers finish their current device).
    """
    if not require_scheduler_leadership("device_monitor_job"):
        return

    cycle_id = uuid.uuid4().hex[:12]
    guard = CycleLeadershipGuard(cycle_id=cycle_id)
    # Force renew at cycle start and verify ownership.
    if not guard.ensure(force=True, reason="cycle_start"):
        return

    concurrency = get_monitor_ping_concurrency()
    ping_cfg = get_ping_config()
    worst_batch_s = (max(ping_cfg["timeout_ms"], 100) / 1000.0) * max(
        ping_cfg["retries"], 1
    )

    logger.info(
        "Monitoring cycle started | cycleId=%s | heartbeat_s=%s | "
        "pingConcurrency=%s | timeoutMs=%s | retries=%s | "
        "estWorstBatchSeconds=%.1f",
        cycle_id,
        guard.heartbeat_s,
        concurrency,
        ping_cfg["timeout_ms"],
        ping_cfg["retries"],
        worst_batch_s,
    )

    suppress_offline = begin_cycle_connectivity_check(cycle_id)

    now = utc_now()
    try:
        devices = list(_db().devices.find({"monitor": True}))
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to load devices for monitoring | cycleId=%s | error=%s",
            cycle_id,
            exc,
        )
        return

    scanned = 0
    skipped = 0
    failed = 0
    aborted = False
    due_devices: list[dict[str, Any]] = []

    # Phase 1: select due devices (cheap) while holding leadership.
    for index, device in enumerate(devices):
        guard.note_device_visited()
        if not guard.ensure(reason=f"select:{index}"):
            aborted = True
            break
        if not _should_check_now(device, now):
            skipped += 1
            continue
        due_devices.append(device)

    if not aborted and due_devices:
        batch_count = (len(due_devices) + concurrency - 1) // concurrency
        est_cycle_s = batch_count * worst_batch_s
        if est_cycle_s > float(ping_cfg["interval"]):
            logger.warning(
                "Monitoring capacity risk | cycleId=%s | due=%s | batches=%s | "
                "concurrency=%s | estWorstCycleSeconds=%.1f | pingInterval=%ss | "
                "hint=raise_pingConcurrency_or_lower_timeout_retries",
                cycle_id,
                len(due_devices),
                batch_count,
                concurrency,
                est_cycle_s,
                ping_cfg["interval"],
            )

        # Phase 2: bounded parallel scans in leadership-gated batches.
        for batch_start in range(0, len(due_devices), concurrency):
            if not guard.ensure(force=True, reason=f"batch_pre:{batch_start}"):
                aborted = True
                break

            batch = due_devices[batch_start : batch_start + concurrency]
            workers = min(concurrency, len(batch))
            logger.info(
                "Monitoring batch started | cycleId=%s | offset=%s | size=%s | "
                "workers=%s",
                cycle_id,
                batch_start,
                len(batch),
                workers,
            )

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(
                        _scan_device_safe,
                        device,
                        suppress_offline=suppress_offline,
                        cycle_id=cycle_id,
                    )
                    for device in batch
                ]
                for fut in as_completed(futures):
                    outcome = fut.result()
                    if outcome == "scanned":
                        scanned += 1
                    else:
                        failed += 1

            if not guard.ensure(force=True, reason=f"batch_post:{batch_start}"):
                aborted = True
                break

    logger.info(
        "Monitoring cycle finished | cycleId=%s | total=%s due=%s scanned=%s "
        "skipped=%s failed=%s | concurrency=%s | partitionSuppress=%s | "
        "aborted=%s | abortReason=%s",
        cycle_id,
        len(devices),
        len(due_devices),
        scanned,
        skipped,
        failed,
        concurrency,
        suppress_offline,
        aborted,
        guard.abort_reason,
    )

    if scanned > 0 and not aborted:
        publish(
            EVENT_DASHBOARD_METRICS_CHANGED,
            {"cycleId": cycle_id, "scanned": scanned, "failed": failed},
        )

    try:
        run_integrity_audit(cycle_id=cycle_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Integrity audit error | cycleId=%s | error=%s", cycle_id, exc)


def _manual_ping_one(device: dict[str, Any]) -> dict[str, Any]:
    """Worker for manual bulk ping — mirrors single-device /scan semantics."""
    ip_address = device.get("ipAddress") or "unknown"
    hostname = device.get("hostname") or "unknown"
    try:
        result = ping_device(
            ip_address,
            critical=bool(device.get("critical")),
            device=device,
        )
        # Manual scans never use partition suppression — operator intent wins.
        apply_ping_result(device, result, scan_type="Manual")
        return {
            "success": bool(result.get("success")),
            "ip": ip_address,
            "hostname": hostname,
            "status": result.get("status"),
            "error": None if result.get("success") else (result.get("message") or "Unreachable"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Manual bulk ping failed | hostname=%s | ip=%s | error=%s",
            hostname,
            ip_address,
            exc,
        )
        return {
            "success": False,
            "ip": ip_address,
            "hostname": hostname,
            "status": None,
            "error": str(exc),
        }


def manual_ping_all_devices() -> dict[str, Any]:
    """
    Manually ping every device in inventory with bounded parallelism.

    Same apply_ping_result / Manual history path as POST /devices/<id>/scan.
    Does not require scheduler leadership (operator-triggered).
    """
    cycle_id = f"manual-{uuid.uuid4().hex[:12]}"
    concurrency = get_monitor_ping_concurrency()
    devices = list(_db().devices.find({}))
    total = len(devices)

    logger.info(
        "Manual bulk ping started | cycleId=%s | total=%s | concurrency=%s",
        cycle_id,
        total,
        concurrency,
    )

    if total == 0:
        return {"total": 0, "online": 0, "failed": 0, "errors": []}

    online = 0
    failed = 0
    errors: list[dict[str, str]] = []
    workers = max(1, min(concurrency, total))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_manual_ping_one, device) for device in devices]
        for fut in as_completed(futures):
            outcome = fut.result()
            if outcome.get("success"):
                online += 1
            else:
                failed += 1
                errors.append({
                    "ip": str(outcome.get("ip") or "unknown"),
                    "hostname": str(outcome.get("hostname") or "unknown"),
                    "error": str(outcome.get("error") or "Unreachable"),
                })

    logger.info(
        "Manual bulk ping finished | cycleId=%s | total=%s | online=%s | failed=%s",
        cycle_id,
        total,
        online,
        failed,
    )

    if total > 0:
        publish(
            EVENT_DASHBOARD_METRICS_CHANGED,
            {"cycleId": cycle_id, "scanned": total, "online": online, "failed": failed},
        )

    return {
        "total": total,
        "online": online,
        "failed": failed,
        "errors": errors,
    }
