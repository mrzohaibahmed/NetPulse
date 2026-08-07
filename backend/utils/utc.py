"""
UTC time helpers for the ping monitoring pipeline (Phase 7).

All monitoring timestamps must be timezone-aware UTC to keep scheduler
interval math and API serialization consistent.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current time as timezone-aware UTC."""
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    """
    Coerce a datetime to timezone-aware UTC.

    Naive values are assumed to already be UTC (legacy Mongo documents).
    """
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
