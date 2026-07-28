"""
Confirmation history helpers.

Loads risk history, previous confirmation state, eligibility, and polling
health from MongoDB only — never talks to devices.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from bson import ObjectId


COLLECTION = "storm_confirmation_history"
RISK_COLLECTION = "storm_risk_history"


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _as_oid(device_id):
    if isinstance(device_id, ObjectId):
        return device_id
    if isinstance(device_id, str) and ObjectId.is_valid(device_id):
        return ObjectId(device_id)
    return device_id


def load_latest_confirmation(device_id, interface: str) -> Optional[dict]:
    """Return the newest confirmation document for an interface."""
    oid = _as_oid(device_id)
    return _db()[COLLECTION].find_one(
        {"deviceId": oid, "interface": interface},
        sort=[("timestamp", -1)],
    )


def load_recent_risk_scores(
    device_id,
    interface: str,
    *,
    limit: int = 20,
) -> list[dict]:
    """Newest-first risk history rows for the confirmation window."""
    oid = _as_oid(device_id)
    return list(
        _db()[RISK_COLLECTION]
        .find({"deviceId": oid, "interface": interface})
        .sort("timestamp", -1)
        .limit(max(int(limit), 1))
    )


def load_latest_risk(device_id, interface: str) -> Optional[dict]:
    rows = load_recent_risk_scores(device_id, interface, limit=1)
    return rows[0] if rows else None


def load_eligibility(device_id, interface: str) -> Optional[bool]:
    oid = _as_oid(device_id)
    row = _db().eligibility_results.find_one(
        {"deviceId": oid, "interface": interface},
        sort=[("timestamp", -1)],
    )
    if row is None:
        return None
    return bool(row.get("eligible"))


def load_device_status(device_id) -> Optional[str]:
    oid = _as_oid(device_id)
    device = _db().devices.find_one({"_id": oid}, {"status": 1})
    if not device:
        return None
    return device.get("status")


def interface_exists(device_id, interface: str) -> bool:
    oid = _as_oid(device_id)
    return (
        _db().interfaces.find_one(
            {"deviceId": oid, "name": interface},
            {"_id": 1},
        )
        is not None
    )


def detect_poll_failure(
    device_id,
    interface: str,
    *,
    stale_seconds: int = 180,
    latest_risk: Optional[dict] = None,
) -> tuple[bool, Optional[str]]:
    """
    Detect polling / data-path failures that should reset confirmation.

    Returns (failed, reason).
    """
    status = load_device_status(device_id)
    if status is None:
        return True, "Device not found"
    if str(status).lower() not in ("online",):
        return True, f"Device unreachable ({status})"

    if not interface_exists(device_id, interface):
        return True, "Interface removed"

    oid = _as_oid(device_id)
    latest_stat = _db().interface_stats.find_one(
        {"deviceId": oid, "interfaceName": interface},
        sort=[("timestamp", -1)],
    )
    if latest_stat is None:
        return True, "Missing statistics"

    ts = latest_stat.get("timestamp")
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age > max(int(stale_seconds), 1):
            return True, "Stale statistics (polling failure)"

    if latest_risk:
        skipped = (latest_risk.get("skippedReason") or "").lower()
        if "missing statistics" in skipped or "missing history" in skipped:
            return True, "Missing statistics history"

    # Counter reset heuristic: consecutive samples where counters dropped
    # sharply without wrap handling (already handled in rates). Detect via
    # risk rawMetrics all unsupported / confidence collapse after prior data.
    if latest_risk is not None:
        confidence = latest_risk.get("confidence")
        try:
            if confidence is not None and float(confidence) <= 0:
                # Only treat as poll failure when we previously had samples.
                prev = load_recent_risk_scores(device_id, interface, limit=2)
                if len(prev) >= 2 and float(prev[1].get("confidence") or 0) > 0:
                    return True, "Counter reset / confidence collapse"
        except (TypeError, ValueError):
            pass

    return False, None


def window_stats(risk_scores: list[float]) -> tuple[float, float, float]:
    """Return (current, highest, average) for a confirmation window."""
    if not risk_scores:
        return 0.0, 0.0, 0.0
    current = float(risk_scores[0])
    highest = max(float(v) for v in risk_scores)
    average = sum(float(v) for v in risk_scores) / len(risk_scores)
    return (
        round(current, 2),
        round(highest, 2),
        round(average, 2),
    )


def count_trailing_high(
    risk_rows_newest_first: list[dict],
    threshold: float,
) -> list[float]:
    """
    Walk newest→oldest and collect trailing scores that stay >= threshold.

    Stops at the first sample below threshold (or ineligible / missing score).
    """
    window: list[float] = []
    for row in risk_rows_newest_first:
        try:
            score = float(row.get("riskScore") or 0)
        except (TypeError, ValueError):
            break
        if score < threshold:
            break
        # Skip explicitly non-eligible risk rows inside the streak.
        if row.get("eligible") is False:
            break
        window.append(score)
    return window
