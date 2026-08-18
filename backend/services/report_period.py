"""
Bounded report time windows.

Every historical report query must use an explicit [start, end] range.
Default is the last 24 hours. Custom ranges are capped at 90 days
(telemetry TTL). This module does not query MongoDB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

VALID_PERIODS = ("24h", "7d", "30d", "custom")
DEFAULT_PERIOD = "24h"
MAX_RANGE_DAYS = 90


def parse_report_datetime(value: Any, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            dt = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59, microsecond=999000)
            return dt
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid date: {value}") from exc


def resolve_report_period(
    period: str | None,
    start_date: Any = None,
    end_date: Any = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Return ``{start, end, period, label}`` as timezone-aware UTC datetimes.

    Raises ValueError for invalid period, missing custom bounds, inverted
    range, or a span longer than MAX_RANGE_DAYS.
    """
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)

    raw = (period or DEFAULT_PERIOD).strip().lower()
    if raw not in VALID_PERIODS:
        raise ValueError("Invalid period. Use 24h, 7d, 30d, or custom.")

    if raw == "24h":
        start, end = stamp - timedelta(hours=24), stamp
        label = "Last 24 hours"
    elif raw == "7d":
        start, end = stamp - timedelta(days=7), stamp
        label = "Last 7 days"
    elif raw == "30d":
        start, end = stamp - timedelta(days=30), stamp
        label = "Last 30 days"
    else:
        start = parse_report_datetime(start_date)
        end = parse_report_datetime(end_date, end_of_day=True)
        if start is None or end is None:
            raise ValueError("Custom range requires startDate and endDate.")
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if start >= end:
            raise ValueError("startDate must be before endDate.")
        span_days = (end - start).total_seconds() / 86400.0
        if span_days > MAX_RANGE_DAYS:
            raise ValueError(
                f"Date range cannot exceed {MAX_RANGE_DAYS} days."
            )
        label = "Custom range"

    return {
        "start": start,
        "end": end,
        "period": raw,
        "label": label,
    }


def timestamp_match(start: datetime, end: datetime, field: str = "timestamp") -> dict:
    return {field: {"$gte": start, "$lte": end}}
