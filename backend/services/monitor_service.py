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
from services.settings_service import get_failure_confirmation_scans, get_ping_config
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
        resolve_critical_offline_alerts(device, cycle_id=cycle_id)
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


def monitor_all_devices():
    """Scan all devices with monitor=True (FR2.1). Leader-only; aborts on lease loss."""
    if not require_scheduler_leadership("device_monitor_job"):
        return

    cycle_id = uuid.uuid4().hex[:12]
    guard = CycleLeadershipGuard(cycle_id=cycle_id)
    # Force renew at cycle start and verify ownership.
    if not guard.ensure(force=True, reason="cycle_start"):
        return

    logger.info(
        "Monitoring cycle started | cycleId=%s | heartbeat_s=%s",
        cycle_id,
        guard.heartbeat_s,
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

    for index, device in enumerate(devices):
        hostname = device.get("hostname", "unknown")
        ip_address = device.get("ipAddress", "unknown")
        device_id = device.get("_id")

        guard.note_device_visited()
        # Time-based and device-count heartbeat — abort immediately on loss.
        if not guard.ensure(reason=f"pre_device:{index}"):
            aborted = True
            break

        try:
            if not _should_check_now(device, now):
                skipped += 1
                continue
            _scan_device(
                device,
                suppress_offline=suppress_offline,
                cycle_id=cycle_id,
            )
            scanned += 1
        except Exception as error:
            failed += 1
            logger.exception(
                "Scan failed | cycleId=%s | deviceId=%s | hostname=%s | ip=%s | error=%s",
                cycle_id,
                device_id,
                hostname,
                ip_address,
                error,
            )

        # Renew again after potentially long ping so lease cannot expire mid-device.
        if not guard.ensure(force=False, reason=f"post_device:{index}"):
            aborted = True
            break

    logger.info(
        "Monitoring cycle finished | cycleId=%s | total=%s scanned=%s "
        "skipped=%s failed=%s | partitionSuppress=%s | aborted=%s | abortReason=%s",
        cycle_id,
        len(devices),
        scanned,
        skipped,
        failed,
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
