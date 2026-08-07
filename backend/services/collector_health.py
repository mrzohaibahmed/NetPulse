"""
Collector / network-partition protection for ping monitoring (Phase 8).

When the collector itself loses upstream connectivity, mass device failures
are suppressed so the inventory is not painted Offline incorrectly.
Genuine single-device failures are never suppressed while connectivity is OK.
"""

from __future__ import annotations

import os
from typing import Any

from services.mongo_retry import assert_insert_acknowledged, with_mongo_retry
from services.monitor_events import EVENT_COLLECTOR_HEALTH, publish
from services.ping_service import ping_device
from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("collector_health")

# Module state — cleared automatically when connectivity returns.
_partition_active = False
_partition_alert_id = None


def _probe_host() -> str | None:
    host = (os.getenv("MONITOR_CONNECTIVITY_PROBE_HOST") or "").strip()
    return host or None


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def probe_collector_connectivity() -> tuple[bool, str]:
    """
    Return (healthy, reason).

    When no probe host is configured, connectivity is assumed healthy so
    individual device failures are never suppressed by accident.
    """
    host = _probe_host()
    if not host:
        return True, "probe_disabled"

    # Short, single-attempt probe — must not dominate the monitor cycle.
    result = ping_device(
        host,
        critical=False,
        timeout_ms=int(os.getenv("MONITOR_CONNECTIVITY_PROBE_TIMEOUT_MS", "800")),
        retries=1,
        device=None,
    )
    if result.get("success"):
        return True, f"probe_ok:{host}"
    return False, f"probe_failed:{host}:{result.get('message')}"


def begin_cycle_connectivity_check(cycle_id: str) -> bool:
    """
    Run at the start of each monitoring cycle.

    Returns True when offline transitions should be SUPPRESSED (partition).
    """
    global _partition_active, _partition_alert_id

    healthy, reason = probe_collector_connectivity()

    if healthy:
        if _partition_active:
            logger.warning(
                "Collector connectivity restored | cycleId=%s | reason=%s",
                cycle_id,
                reason,
            )
            _resolve_collector_alert()
            publish(
                EVENT_COLLECTOR_HEALTH,
                {
                    "status": "healthy",
                    "cycleId": cycle_id,
                    "reason": reason,
                },
            )
        _partition_active = False
        return False

    # Unhealthy probe → suppress mass offline for this cycle.
    logger.error(
        "Collector connectivity FAILED — suppressing offline transitions | "
        "cycleId=%s | reason=%s",
        cycle_id,
        reason,
    )
    if not _partition_active:
        _partition_alert_id = _create_collector_alert(cycle_id, reason)
        publish(
            EVENT_COLLECTOR_HEALTH,
            {
                "status": "partition",
                "cycleId": cycle_id,
                "reason": reason,
                "alertId": str(_partition_alert_id) if _partition_alert_id else None,
            },
        )
    _partition_active = True
    return True


def is_partition_active() -> bool:
    return _partition_active


def _create_collector_alert(cycle_id: str, reason: str) -> Any:
    now = utc_now()
    doc = {
        "deviceId": None,
        "hostname": "collector",
        "ipAddress": _probe_host() or "local",
        "deviceType": "Collector",
        "deviceName": "NetPulse Collector",
        "status": "COLLECTOR_PARTITION",
        "title": "Collector Connectivity Failure",
        "message": (
            "NetPulse collector lost upstream connectivity. "
            "Mass offline transitions are suppressed until connectivity returns. "
            f"Reason: {reason}. Cycle: {cycle_id}."
        ),
        "scanType": "Collector Health",
        "alertType": "Collector Health",
        "category": "Collector Health",
        "severity": "CRITICAL",
        "action": "SUPPRESS_OFFLINE",
        "generatedBy": "SYSTEM",
        "cycleId": cycle_id,
        "emailSent": False,
        "acknowledged": False,
        "dismissed": False,
        "resolved": False,
        "acknowledgedAt": None,
        "dismissedAt": None,
        "resolvedAt": None,
        "createdAt": now,
    }

    def _insert():
        return _db().alerts.insert_one(doc)

    try:
        result = with_mongo_retry(
            _insert,
            action="collector_health_alert_insert",
            idempotent=False,
        )
        assert_insert_acknowledged(
            result,
            action="collector_health_alert_insert",
        )
        return result.inserted_id
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to create collector health alert: %s", exc)
        return None


def _resolve_collector_alert() -> None:
    global _partition_alert_id
    now = utc_now()

    def _resolve():
        return _db().alerts.update_many(
            {
                "alertType": "Collector Health",
                "resolved": {"$ne": True},
                "dismissed": {"$ne": True},
            },
            {
                "$set": {
                    "resolved": True,
                    "resolvedAt": now,
                    "resolvedBy": "SYSTEM",
                    "resolvedReason": "Collector connectivity restored",
                }
            },
        )

    try:
        result = with_mongo_retry(
            _resolve,
            action="collector_health_alert_resolve",
            idempotent=True,
        )
        logger.info(
            "Collector health alerts resolved | matched=%s | modified=%s",
            result.matched_count,
            result.modified_count,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to resolve collector health alert: %s", exc)
    _partition_alert_id = None
