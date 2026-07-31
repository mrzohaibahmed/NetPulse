"""
utilization.py
==============
Enterprise interface utilization calculation.

Computes RX / TX / overall link utilization from consecutive counter samples
and negotiated (or configured) interface bandwidth.

Formula
-------
    delta_bits = delta_bytes × 8
    bits_per_second = delta_bits / interval_seconds
    utilization_% = (bits_per_second / speed_bps) × 100

Overall utilization for full-duplex links is ``max(rx%, tx%)`` — each
direction has its own full bandwidth.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


# Counter wrap moduli
_MOD_32 = 2**32
_MOD_64 = 2**64

# Treat simultaneous large counter drops as a device/interface counter reset
# (reboot, clear counters) rather than wrap.
_RESET_DROP_RATIO = 0.25
_RESET_MIN_PREVIOUS = 1_000_000  # bytes


def resolve_speed_bps(
    *candidates: Any,
) -> Optional[int]:
    """
    Return the first positive speed in bits/sec from mixed inputs.

    Accepts raw ``speed_bps``, Mongo ``speedBps``, or Mbps integers/strings.
    """
    for value in candidates:
        bps = _coerce_speed_bps(value)
        if bps and bps > 0:
            return bps
    return None


def _coerce_speed_bps(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    # Heuristic: values below 100_000 are almost certainly Mbps (1..100000),
    # not bits/sec (1 Mbps = 1e6 bps). Inventory stores speedMbps this way.
    if number < 100_000:
        return int(number * 1_000_000)
    return int(number)


def counter_delta(current: Any, previous: Any) -> tuple[int, str]:
    """
    Compute a non-negative counter delta with wrap / reset detection.

    Returns
    -------
    (delta_bytes, event)
        event is one of: ``ok``, ``wrap32``, ``wrap64``, ``reset``, ``invalid``
    """
    try:
        cur = int(current or 0)
        prev = int(previous or 0)
    except (TypeError, ValueError):
        return 0, "invalid"

    if cur < 0 or prev < 0:
        return 0, "invalid"

    if cur >= prev:
        return cur - prev, "ok"

    # Counter moved backwards — distinguish wrap from clear/reboot.
    if prev > _MOD_32:
        # 64-bit / HC counter
        if prev >= _RESET_MIN_PREVIOUS and cur <= int(prev * _RESET_DROP_RATIO):
            return 0, "reset"
        return (cur + _MOD_64) - prev, "wrap64"

    wrap32 = (cur + _MOD_32) - prev
    # Small wrap deltas near the 32-bit ceiling are genuine wraps.
    if wrap32 < (_MOD_32 // 2):
        return wrap32, "wrap32"

    if prev >= _RESET_MIN_PREVIOUS and cur <= int(prev * _RESET_DROP_RATIO):
        return 0, "reset"

    return wrap32, "wrap32"


def compute_utilization(
    *,
    current_rx_bytes: Any,
    current_tx_bytes: Any,
    previous_rx_bytes: Any,
    previous_tx_bytes: Any,
    speed_bps: Any,
    current_timestamp: datetime,
    previous_timestamp: Any,
    min_interval_seconds: float = 1.0,
    max_interval_seconds: float = 3600.0,
) -> dict[str, Any]:
    """
    Compute RX/TX/overall utilization between two samples.

    Returns a dict with utilization fields (floats or None) plus diagnostics:
    ``intervalSeconds``, ``rxBps``, ``txBps``, ``event``.
    """
    empty = {
        "utilization": None,
        "rx_utilization": None,
        "tx_utilization": None,
        "rx_bps": None,
        "tx_bps": None,
        "interval_seconds": None,
        "speed_bps": None,
        "event": "skipped",
    }

    speed = resolve_speed_bps(speed_bps)
    if not speed:
        empty["event"] = "missing_speed"
        return empty

    if previous_timestamp is None or current_timestamp is None:
        empty["event"] = "missing_timestamp"
        return empty

    prev_ts = previous_timestamp
    if getattr(prev_ts, "tzinfo", None) is None:
        prev_ts = prev_ts.replace(tzinfo=timezone.utc)
    cur_ts = current_timestamp
    if getattr(cur_ts, "tzinfo", None) is None:
        cur_ts = cur_ts.replace(tzinfo=timezone.utc)

    interval = (cur_ts - prev_ts).total_seconds()
    if interval < min_interval_seconds:
        empty["event"] = "interval_too_small"
        empty["interval_seconds"] = interval
        empty["speed_bps"] = speed
        return empty
    if interval > max_interval_seconds:
        # Stale previous sample — refuse to compute misleading rates.
        empty["event"] = "interval_too_large"
        empty["interval_seconds"] = interval
        empty["speed_bps"] = speed
        return empty

    rx_delta, rx_event = counter_delta(current_rx_bytes, previous_rx_bytes)
    tx_delta, tx_event = counter_delta(current_tx_bytes, previous_tx_bytes)

    if rx_event == "reset" or tx_event == "reset":
        empty["event"] = "counter_reset"
        empty["interval_seconds"] = interval
        empty["speed_bps"] = speed
        return empty

    if rx_event == "invalid" and tx_event == "invalid":
        empty["event"] = "invalid_counters"
        return empty

    rx_bps = (rx_delta * 8) / interval
    tx_bps = (tx_delta * 8) / interval

    rx_util = _pct(rx_bps, speed)
    tx_util = _pct(tx_bps, speed)
    overall = round(max(rx_util, tx_util), 6)

    event = "ok"
    if "wrap" in rx_event or "wrap" in tx_event:
        event = f"{rx_event},{tx_event}"

    return {
        "utilization": overall,
        "rx_utilization": rx_util,
        "tx_utilization": tx_util,
        "rx_bps": round(rx_bps, 3),
        "tx_bps": round(tx_bps, 3),
        "interval_seconds": round(interval, 3),
        "speed_bps": speed,
        "event": event,
    }


def _pct(rate_bps: float, speed_bps: int) -> float:
    return round(min(max((rate_bps / speed_bps) * 100.0, 0.0), 100.0), 6)
