"""
HPE iLO Telemetry Freshness Evaluation Primitives (Phase 4A Step 3).

Provides a pure, deterministic evaluation layer to determine telemetry freshness:
  - FRESH
  - STALE
  - FAILING
  - NEVER_POLLED

Does NOT perform database queries, network I/O, state mutations, logging, or health calculation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from utils.utc import utc_now

DEFAULT_STALE_THRESHOLD_SECONDS = 1800  # 30 minutes


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Ensure datetime object is timezone-aware in UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class TelemetryFreshness:
    """Read-only container representing evaluated hardware telemetry freshness."""

    status: str  # FRESH | STALE | FAILING | NEVER_POLLED
    data_age_seconds: int | None
    stale_threshold_seconds: int
    is_stale: bool
    observed_at: datetime | None
    last_poll_status: str

    def to_dict(self) -> dict[str, Any]:
        """Convert freshness metrics into a JSON-serializable dictionary."""
        return {
            "status": self.status,
            "dataAgeSeconds": self.data_age_seconds,
            "staleThresholdSeconds": self.stale_threshold_seconds,
            "isStale": self.is_stale,
            "observedAt": (
                self.observed_at.isoformat().replace("+00:00", "Z")
                if self.observed_at
                else None
            ),
            "lastPollStatus": self.last_poll_status,
        }


def evaluate_telemetry_freshness(
    observed_at: datetime | None,
    collection_state: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    stale_threshold_seconds: int = DEFAULT_STALE_THRESHOLD_SECONDS,
) -> TelemetryFreshness:
    """
    Pure, deterministic evaluation of telemetry freshness.

    Categorizes telemetry as FRESH, STALE, FAILING, or NEVER_POLLED based on data age,
    stale threshold, and operational collection state.
    """
    if (
        not isinstance(stale_threshold_seconds, (int, float))
        or stale_threshold_seconds <= 0
    ):
        raise ValueError("stale_threshold_seconds must be a positive number")

    stale_threshold_int = int(stale_threshold_seconds)
    current_time = _ensure_utc(now) or utc_now()
    obs_time = _ensure_utc(observed_at)

    state = collection_state or {}
    last_poll_status = state.get("lastPollStatus") or "NEVER_POLLED"
    last_success_at = _ensure_utc(state.get("lastSuccessAt"))
    last_failure_at = _ensure_utc(state.get("lastFailureAt"))
    consecutive_failures = int(state.get("consecutiveFailures") or 0)

    # 1. Check if ANY successful hardware observation ever occurred
    has_observation = obs_time is not None or last_success_at is not None

    if not has_observation:
        return TelemetryFreshness(
            status="NEVER_POLLED",
            data_age_seconds=None,
            stale_threshold_seconds=stale_threshold_int,
            is_stale=True,
            observed_at=None,
            last_poll_status=last_poll_status,
        )

    # Calculate data age in seconds (clock-skew safe: dataAgeSeconds >= 0)
    effective_obs = obs_time or last_success_at
    assert effective_obs is not None  # Guaranteed by has_observation
    raw_age = (current_time - effective_obs).total_seconds()
    data_age_seconds = max(0, int(raw_age))

    # 2. Check for active/current collection failure
    is_failing = False
    if last_poll_status not in ("SUCCESS", "NEVER_POLLED") and consecutive_failures > 0:
        if last_failure_at and last_success_at:
            if last_failure_at > last_success_at:
                is_failing = True
        else:
            is_failing = True

    # 3. Categorize freshness state
    if is_failing:
        status = "FAILING"
        is_stale = True
    elif data_age_seconds > stale_threshold_int:
        status = "STALE"
        is_stale = True
    else:
        status = "FRESH"
        is_stale = False

    return TelemetryFreshness(
        status=status,
        data_age_seconds=data_age_seconds,
        stale_threshold_seconds=stale_threshold_int,
        is_stale=is_stale,
        observed_at=effective_obs,
        last_poll_status=last_poll_status,
    )
