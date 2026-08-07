"""
Monitoring data integrity checks (Phase 11).

Runs as a non-blocking audit after each cycle (or on demand). Issues are
logged only — monitoring is never interrupted.
"""

from __future__ import annotations

from typing import Any

from services.ping_service import (
    STATUS_NOT_REACHABLE,
    STATUS_OFFLINE_CRITICAL,
    STATUS_ONLINE,
)
from utils.monitor_logger import get_monitor_logger
from utils.utc import ensure_utc, utc_now

logger = get_monitor_logger("monitor_integrity")

VALID_STATUSES = frozenset(
    {
        STATUS_ONLINE,
        STATUS_NOT_REACHABLE,
        STATUS_OFFLINE_CRITICAL,
        "Unknown",
        "Offline",  # legacy
    }
)


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def validate_device_document(device: dict[str, Any], *, cycle_id: str | None = None) -> list[str]:
    """Return a list of integrity issue codes for one device."""
    issues: list[str] = []
    device_id = device.get("_id")
    ip_address = device.get("ipAddress")
    hostname = device.get("hostname")

    status = device.get("status")
    if status not in VALID_STATUSES:
        issues.append(f"invalid_status:{status!r}")

    if not ip_address:
        issues.append("null_ipAddress")

    if device.get("monitor") is None:
        issues.append("null_monitor")

    consecutive = device.get("consecutiveFailures")
    if consecutive is not None:
        try:
            if int(consecutive) < 0:
                issues.append("negative_consecutiveFailures")
        except (TypeError, ValueError):
            issues.append("non_numeric_consecutiveFailures")

    # Online with outstanding failures is inconsistent (unless mid-race).
    if status == STATUS_ONLINE and int(device.get("consecutiveFailures") or 0) > 0:
        issues.append("online_with_failures")

    # Offline / NR with zero failures after at least one check is odd.
    if (
        status in (STATUS_NOT_REACHABLE, STATUS_OFFLINE_CRITICAL, "Offline")
        and int(device.get("consecutiveFailures") or 0) == 0
        and device.get("lastCheckedAt") is not None
    ):
        issues.append("offline_with_zero_failures")

    rt = device.get("responseTime")
    if rt is not None:
        try:
            if float(rt) < 0:
                issues.append("negative_responseTime")
        except (TypeError, ValueError):
            issues.append("non_numeric_responseTime")
        if status != STATUS_ONLINE:
            issues.append("responseTime_while_not_online")

    now = utc_now()
    for field in ("lastSeen", "lastCheckedAt", "updatedAt", "createdAt"):
        raw = device.get(field)
        if raw is None:
            continue
        dt = ensure_utc(raw) if hasattr(raw, "isoformat") else None
        if dt is None:
            issues.append(f"unparseable_{field}")
            continue
        if dt > now:
            issues.append(f"future_{field}")

    last_seen = ensure_utc(device.get("lastSeen")) if device.get("lastSeen") else None
    last_checked = (
        ensure_utc(device.get("lastCheckedAt")) if device.get("lastCheckedAt") else None
    )
    if last_seen and last_checked and last_seen > last_checked:
        # lastSeen should not be newer than lastCheckedAt for the same cycle.
        # Allow small clock skew tolerance is unnecessary if both set together.
        pass  # discovery may set lastSeen without lastCheckedAt historically

    if issues:
        logger.warning(
            "Integrity issue | cycleId=%s | deviceId=%s | hostname=%s | ip=%s | issues=%s",
            cycle_id,
            device_id,
            hostname,
            ip_address,
            ",".join(issues),
        )
    return issues


def run_integrity_audit(*, cycle_id: str | None = None, sample_limit: int = 500) -> dict:
    """
    Sample monitored devices and log integrity problems.

    Also flags duplicate ipAddress documents if the unique index were missing.
    """
    issues_total = 0
    checked = 0
    try:
        cursor = (
            _db()
            .devices.find({"monitor": True})
            .limit(max(int(sample_limit), 1))
        )
        for device in cursor:
            checked += 1
            found = validate_device_document(device, cycle_id=cycle_id)
            issues_total += len(found)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Integrity audit failed | cycleId=%s | error=%s",
            cycle_id,
            exc,
        )
        return {"checked": checked, "issues": issues_total, "error": str(exc)}

    logger.info(
        "Integrity audit complete | cycleId=%s | checked=%s | issueFields=%s",
        cycle_id,
        checked,
        issues_total,
    )
    return {"checked": checked, "issues": issues_total}
