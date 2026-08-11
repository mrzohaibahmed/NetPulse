"""
UTC time helpers for NetPulse.

Canonical rule: all internal timestamps are timezone-aware UTC.
MongoDB stores UTC datetime values; lease comparisons use aware UTC only.
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
    if not isinstance(value, datetime):
        raise TypeError(f"expected datetime, got {type(value)!r}")
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def require_utc_aware(value: datetime, *, field: str = "datetime") -> datetime:
    """
    Require a timezone-aware UTC datetime for lease / ownership logic.

    Raises TypeError/ValueError on naive or non-datetime values.
    """
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be datetime, got {type(value)!r}")
    if value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware UTC (got naive datetime)")
    return value.astimezone(timezone.utc)


def format_utc(value: datetime | None) -> str:
    """ISO-8601 UTC string with offset (never ambiguous local wall time)."""
    dt = ensure_utc(value)
    if dt is None:
        return "None"
    # Normalize +00:00 for readability; keep milliseconds.
    return dt.isoformat(timespec="milliseconds")
