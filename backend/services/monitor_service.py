"""
Device reachability monitoring — ping cycle and atomic status writes.

Phases covered here: reliable writes, atomic failure counter, partition
suppression, structured logging, integrity audit hooks, event publishing.
"""

from __future__ import annotations

import uuid
from typing import Any

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
from services.scheduler_ownership import require_scheduler_leadership, try_acquire_or_renew
from services.settings_service import get_ping_config
from utils.monitor_logger import get_monitor_logger
from utils.utc import ensure_utc, utc_now

logger = get_monitor_logger("monitor")


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _should_check_now(device, now):
    """Honor per-device interval override within the global scheduler cycle (FR8.1)."""
    config = get_ping_config(device)
    interval = config["interval"]
    last_checked = ensure_utc(device.get("lastCheckedAt"))
    if not last_checked:
        return True

    elapsed = (now - last_checked).total_seconds()
    return elapsed >= interval


def apply_ping_result(
    device,
    result,
    scan_type="Automatic",
    *,
    suppress_offline: bool = False,
    cycle_id: str | None = None,
):
    """
    Atomically update device fields and history from a ping result (FR3.4, FR3.5).

    consecutiveFailures uses MongoDB $inc / $set (never read-modify-write).
    """
    now = utc_now()
    device_id = device.get("_id")
    hostname = device.get("hostname", "unknown")
    ip_address = device.get("ipAddress", "unknown")
    previous_status = device.get("status", "Unknown")

    # Phase 8 — suppress mass offline when collector is partitioned.
    if suppress_offline and not result.get("success"):
        logger.warning(
            "Offline transition suppressed (collector partition) | "
            "cycleId=%s | deviceId=%s | hostname=%s | ip=%s | wouldStatus=%s",
            cycle_id,
            device_id,
            hostname,
            ip_address,
            result.get("status"),
        )

        def _touch_checked():
            return _db().devices.find_one_and_update(
                {"_id": device_id},
                {"$set": {"lastCheckedAt": now, "updatedAt": now}},
                return_document=ReturnDocument.AFTER,
            )

        try:
            with_mongo_retry(
                _touch_checked,
                action="device_touch_checked_partition",
                device_id=device_id,
                ip_address=ip_address,
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
        updated = _atomic_mark_online(
            device_id=device_id,
            ip_address=ip_address,
            result=result,
            now=now,
        )
        if updated is None:
            return previous_status

        consecutive = 0
        new_status = STATUS_ONLINE
    else:
        updated = _atomic_mark_failure(
            device_id=device_id,
            ip_address=ip_address,
            result=result,
            now=now,
        )
        if updated is None:
            return previous_status

        consecutive = int(updated.get("consecutiveFailures") or 0)
        new_status = result["status"]

    logger.info(
        "Ping applied | cycleId=%s | deviceId=%s | hostname=%s | ip=%s | "
        "previous=%s | new=%s | success=%s | responseTime=%s | "
        "consecutiveFailures=%s | scanType=%s",
        cycle_id,
        device_id,
        hostname,
        ip_address,
        previous_status,
        new_status,
        bool(result.get("success")),
        result.get("responseTime"),
        consecutive,
        scan_type,
    )

    # History after device write — if history fails, device state is still
    # authoritative; exception is logged by caller / retry layer.
    try:
        save_ping_history(
            device=device,
            ping_result=result,
            scan_type=scan_type,
            cycle_id=cycle_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Ping history write failed after status update | "
            "deviceId=%s | ip=%s | error=%s",
            device_id,
            ip_address,
            exc,
        )

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
        )

    return previous_status


def _atomic_mark_online(*, device_id, ip_address, result, now) -> dict[str, Any] | None:
    """Set Online + reset consecutiveFailures atomically."""
    last_seen = result.get("lastSeen") or now
    last_seen = ensure_utc(last_seen) or now

    def _update():
        return _db().devices.find_one_and_update(
            {"_id": device_id},
            {
                "$set": {
                    "status": STATUS_ONLINE,
                    "responseTime": result.get("responseTime"),
                    "lastSeen": last_seen,
                    "lastCheckedAt": now,
                    "updatedAt": now,
                    "consecutiveFailures": 0,
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    try:
        doc = with_mongo_retry(
            _update,
            action="device_mark_online",
            device_id=device_id,
            ip_address=ip_address,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Atomic online write failed | deviceId=%s | ip=%s | error=%s",
            device_id,
            ip_address,
            exc,
        )
        raise

    if doc is None:
        logger.warning(
            "Atomic online write unmatched | deviceId=%s | ip=%s",
            device_id,
            ip_address,
        )
        return None

    logger.info(
        "Mongo write ok | action=device_mark_online | deviceId=%s | ip=%s | "
        "matched=1 | consecutiveFailures=0",
        device_id,
        ip_address,
    )
    return doc


def _atomic_mark_failure(*, device_id, ip_address, result, now) -> dict[str, Any] | None:
    """
    Increment consecutiveFailures with $inc and set failure status.

    Eliminates read-modify-write races across concurrent monitor / manual scans.
    """

    def _update():
        return _db().devices.find_one_and_update(
            {"_id": device_id},
            {
                "$inc": {"consecutiveFailures": 1},
                "$set": {
                    "status": result["status"],
                    "responseTime": None,
                    "lastCheckedAt": now,
                    "updatedAt": now,
                },
                # Do not overwrite lastSeen on failure (FR3.4)
            },
            return_document=ReturnDocument.AFTER,
        )

    try:
        doc = with_mongo_retry(
            _update,
            action="device_mark_failure",
            device_id=device_id,
            ip_address=ip_address,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Atomic failure write failed | deviceId=%s | ip=%s | error=%s",
            device_id,
            ip_address,
            exc,
        )
        raise

    if doc is None:
        logger.warning(
            "Atomic failure write unmatched | deviceId=%s | ip=%s",
            device_id,
            ip_address,
        )
        return None

    logger.info(
        "Mongo write ok | action=device_mark_failure | deviceId=%s | ip=%s | "
        "matched=1 | consecutiveFailures=%s",
        device_id,
        ip_address,
        doc.get("consecutiveFailures"),
    )
    return doc


def _scan_device(device, *, suppress_offline: bool, cycle_id: str):
    hostname = device.get("hostname", "unknown")
    ip_address = device.get("ipAddress", "unknown")
    device_id = device.get("_id")

    logger.info(
        "Device scan started | cycleId=%s | deviceId=%s | hostname=%s | ip=%s",
        cycle_id,
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
    )

    if result["status"] == STATUS_ONLINE:
        logger.info(
            "Device online | cycleId=%s | deviceId=%s | hostname=%s | ip=%s | "
            "response=%s ms",
            cycle_id,
            device_id,
            hostname,
            ip_address,
            result["responseTime"],
        )
    else:
        logger.warning(
            "Device down | cycleId=%s | deviceId=%s | hostname=%s | ip=%s | "
            "status=%s | message=%s | suppressed=%s",
            cycle_id,
            device_id,
            hostname,
            ip_address,
            result["status"],
            result.get("message", "No response"),
            suppress_offline,
        )


def monitor_all_devices():
    """Scan all devices with monitor=True (FR2.1). Leader-only when multi-instance."""
    # Phase 5 — only the elected scheduler owner runs monitoring.
    if not require_scheduler_leadership("device_monitor_job"):
        return

    cycle_id = uuid.uuid4().hex[:12]
    logger.info("Monitoring cycle started | cycleId=%s", cycle_id)

    # Phase 8 — collector connectivity probe.
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
    renew_every = 10  # renew leadership during long cycles

    for index, device in enumerate(devices):
        hostname = device.get("hostname", "unknown")
        ip_address = device.get("ipAddress", "unknown")
        device_id = device.get("_id")

        if index > 0 and index % renew_every == 0:
            try_acquire_or_renew()

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

    logger.info(
        "Monitoring cycle finished | cycleId=%s | total=%s scanned=%s "
        "skipped=%s failed=%s | partitionSuppress=%s",
        cycle_id,
        len(devices),
        scanned,
        skipped,
        failed,
        suppress_offline,
    )

    if scanned > 0:
        publish(
            EVENT_DASHBOARD_METRICS_CHANGED,
            {"cycleId": cycle_id, "scanned": scanned, "failed": failed},
        )

    # Phase 11 — non-blocking integrity audit.
    try:
        run_integrity_audit(cycle_id=cycle_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Integrity audit error | cycleId=%s | error=%s", cycle_id, exc)
