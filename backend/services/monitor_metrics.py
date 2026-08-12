"""
Dispatch-mode observability helpers (Phase 8).

Process-local counters + start-to-start samples for SLO checks. Emits a
throttled heartbeat (30–60s), not a heavy summary on every ~5s dispatcher tick.

Does not change scheduling, claiming, or ping/apply semantics.
Never logs secrets (passwords, tokens, JWTs, Mongo/SNMP credentials).
"""

from __future__ import annotations

import math
import threading
from collections import deque
from datetime import datetime
from typing import Any

from utils.monitor_logger import get_monitor_logger
from utils.utc import ensure_utc, utc_now

logger = get_monitor_logger("monitor_metrics")

# SLO targets for start-to-start cadence (ms) when pingInterval ≈ 60s.
SLO_P50_TARGET_MS = 60_000
SLO_P95_BUDGET_MS = 70_000
SLO_P99_BUDGET_MS = 90_000
SLO_MAX_ALERT_MS = 120_000

HEARTBEAT_MIN_INTERVAL_SECONDS = 45
SAMPLE_WINDOW = 500
MAX_REASONABLE_DELTA_MS = 24 * 60 * 60 * 1000

# Keys that must never appear in monitor metric log lines / payloads.
_SECRET_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "jwt",
    "authorization",
    "mongo_uri",
    "mongodb",
    "credential",
    "snmp",
    "community",
    "apikey",
    "api_key",
    "private_key",
)


def safe_ms_delta(later, earlier) -> int | None:
    """
    Return non-negative millisecond delta, or None if missing/invalid.

    Negative clock skew and absurd deltas are treated as invalid (None).
    """
    if later is None or earlier is None:
        return None
    if not isinstance(later, datetime) or not isinstance(earlier, datetime):
        return None
    left = ensure_utc(later)
    right = ensure_utc(earlier)
    if left is None or right is None:
        return None
    try:
        delta_ms = (left - right).total_seconds() * 1000.0
    except (OverflowError, TypeError, ValueError):
        return None
    if math.isnan(delta_ms) or math.isinf(delta_ms):
        return None
    if delta_ms < 0:
        return None
    if delta_ms > MAX_REASONABLE_DELTA_MS:
        return None
    return int(delta_ms)


def compute_start_to_start_ms(ping_started_at, previous_ping_started_at) -> int | None:
    """current ping start − previous ping start; None if previous missing/invalid."""
    return safe_ms_delta(ping_started_at, previous_ping_started_at)


def compute_due_lag_ms(ping_started_at, due_next_check_at) -> int | None:
    """
    ping start − scheduled due time (``nextCheckAt`` that made the device due).

    Missing due time (new device) → None (not a lag measurement).
    """
    return safe_ms_delta(ping_started_at, due_next_check_at)


def compute_queue_wait_ms(worker_started_at, queued_at) -> int | None:
    return safe_ms_delta(worker_started_at, queued_at)


def compute_ping_duration_ms(ping_completed_at, ping_started_at) -> int | None:
    return safe_ms_delta(ping_completed_at, ping_started_at)


def is_slo_miss(start_to_start_ms: int | None) -> bool:
    """True when start-to-start exceeds the p95 budget (35s)."""
    if start_to_start_ms is None:
        return False
    return int(start_to_start_ms) > SLO_P95_BUDGET_MS


def is_slo_max_alert(start_to_start_ms: int | None) -> bool:
    if start_to_start_ms is None:
        return False
    return int(start_to_start_ms) > SLO_MAX_ALERT_MS


def _percentile_nearest_rank(sorted_vals: list[int], pct: float) -> int | None:
    if not sorted_vals:
        return None
    if pct <= 0:
        return sorted_vals[0]
    if pct >= 100:
        return sorted_vals[-1]
    rank = max(1, int(math.ceil(pct / 100.0 * len(sorted_vals))))
    return sorted_vals[min(rank - 1, len(sorted_vals) - 1)]


def contains_secret_keys(payload: dict[str, Any] | None) -> bool:
    """Detect credential-like keys (for tests / defensive checks)."""
    if not payload:
        return False
    for key in payload:
        lowered = str(key).lower().replace("-", "_")
        for frag in _SECRET_KEY_FRAGMENTS:
            if frag in lowered:
                return True
    return False


def format_metric_fields(**fields: Any) -> str:
    """
    Build a pipe-safe ``key=value`` suffix. Drops None values and refuses
    secret-like keys.
    """
    parts: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        lowered = str(key).lower()
        if any(frag in lowered for frag in _SECRET_KEY_FRAGMENTS):
            continue
        if isinstance(value, datetime):
            stamp = ensure_utc(value)
            text = stamp.isoformat() if stamp is not None else str(value)
        else:
            text = str(value)
        # Never embed multi-line / huge blobs.
        if len(text) > 200:
            text = text[:200] + "…"
        parts.append(f"{key}={text}")
    return " | ".join(parts)


class DispatchMetrics:
    """Thread-safe process-local counters and start-to-start samples."""

    def __init__(self, *, sample_window: int = SAMPLE_WINDOW):
        self._lock = threading.Lock()
        self.claims_won = 0
        self.claims_conflict = 0
        self.queue_full_skips = 0
        self.claim_expired_reclaims = 0
        self.leadership_lost = 0
        self.slo_misses = 0
        self.slo_max_alerts = 0
        self.scans_observed = 0
        self._samples: deque[int] = deque(maxlen=max(10, int(sample_window)))
        self._last_heartbeat_at: datetime | None = None

    def reset(self) -> None:
        with self._lock:
            self.claims_won = 0
            self.claims_conflict = 0
            self.queue_full_skips = 0
            self.claim_expired_reclaims = 0
            self.leadership_lost = 0
            self.slo_misses = 0
            self.slo_max_alerts = 0
            self.scans_observed = 0
            self._samples.clear()
            self._last_heartbeat_at = None

    def incr_claims_won(self, n: int = 1) -> None:
        with self._lock:
            self.claims_won += n

    def incr_claims_conflict(self, n: int = 1) -> None:
        with self._lock:
            self.claims_conflict += n

    def incr_queue_full_skips(self, n: int = 1) -> None:
        with self._lock:
            self.queue_full_skips += n

    def incr_claim_expired_reclaims(self, n: int = 1) -> None:
        with self._lock:
            self.claim_expired_reclaims += n

    def incr_leadership_lost(self, n: int = 1) -> None:
        with self._lock:
            self.leadership_lost += n

    def record_scan_timing(
        self,
        *,
        start_to_start_ms: int | None,
    ) -> None:
        with self._lock:
            self.scans_observed += 1
            if start_to_start_ms is None:
                return
            self._samples.append(int(start_to_start_ms))
            if is_slo_miss(start_to_start_ms):
                self.slo_misses += 1
            if is_slo_max_alert(start_to_start_ms):
                self.slo_max_alerts += 1

    def snapshot(
        self,
        *,
        workers_active: int = 0,
        queue_depth: int = 0,
        workers_total: int = 0,
    ) -> dict[str, Any]:
        with self._lock:
            samples = sorted(self._samples)
            return {
                "claims_won": self.claims_won,
                "claims_conflict": self.claims_conflict,
                "queue_full_skips": self.queue_full_skips,
                "claim_expired_reclaims": self.claim_expired_reclaims,
                "leadership_lost": self.leadership_lost,
                "slo_misses": self.slo_misses,
                "slo_max_alerts": self.slo_max_alerts,
                "scans_observed": self.scans_observed,
                "workers_active": int(workers_active),
                "queue_depth": int(queue_depth),
                "workers_total": int(workers_total),
                "startToStart_p50Ms": _percentile_nearest_rank(samples, 50),
                "startToStart_p95Ms": _percentile_nearest_rank(samples, 95),
                "startToStart_p99Ms": _percentile_nearest_rank(samples, 99),
                "startToStart_samples": len(samples),
                "slo_p95_budget_ms": SLO_P95_BUDGET_MS,
                "slo_max_alert_ms": SLO_MAX_ALERT_MS,
            }

    def maybe_emit_heartbeat(
        self,
        *,
        workers_active: int = 0,
        queue_depth: int = 0,
        workers_total: int = 0,
        dispatch_id: str | None = None,
        force: bool = False,
        now=None,
    ) -> bool:
        """
        Emit a compact INFO heartbeat at most once per
        ``HEARTBEAT_MIN_INTERVAL_SECONDS`` (default 45s).
        """
        stamp = now or utc_now()
        with self._lock:
            if not force and self._last_heartbeat_at is not None:
                elapsed = (stamp - self._last_heartbeat_at).total_seconds()
                if elapsed < HEARTBEAT_MIN_INTERVAL_SECONDS:
                    return False
            self._last_heartbeat_at = stamp

        snap = self.snapshot(
            workers_active=workers_active,
            queue_depth=queue_depth,
            workers_total=workers_total,
        )
        if contains_secret_keys(snap):
            # Defensive — snapshot keys are fixed literals.
            logger.error("Refusing heartbeat - unexpected secret-like keys")
            return False

        logger.info(
            "Dispatch metrics heartbeat | %s",
            format_metric_fields(dispatchId=dispatch_id, **snap),
        )
        return True


_metrics = DispatchMetrics()


def get_dispatch_metrics() -> DispatchMetrics:
    return _metrics


def reset_dispatch_metrics() -> None:
    """Test helper — clear process-local counters."""
    _metrics.reset()
