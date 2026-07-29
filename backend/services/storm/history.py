"""
Rate calculation helpers for Storm risk analyzers.

Never use raw counters for scoring — always derive rates from consecutive
``interface_stats`` samples, with counter-rollover support.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


# Counter field aliases (snake_case API ↔ camelCase Mongo).
COUNTER_FIELDS = {
    "broadcast_packets": ("broadcastPackets", "broadcast_packets"),
    "rx_broadcast_packets": ("rxBroadcastPackets", "rx_broadcast_packets"),
    "tx_broadcast_packets": ("txBroadcastPackets", "tx_broadcast_packets"),
    "multicast_packets": ("multicastPackets", "multicast_packets"),
    "rx_multicast_packets": ("rxMulticastPackets", "rx_multicast_packets"),
    "tx_multicast_packets": ("txMulticastPackets", "tx_multicast_packets"),
    "unknown_unicast_packets": (
        "unknownUnicastPackets",
        "unknown_unicast_packets",
        "unknownUnicast",
        "uucastPackets",
    ),
    "input_errors": ("inputErrors", "input_errors"),
    "output_errors": ("outputErrors", "output_errors"),
    "discards": ("discards",),
    "rx_discards": ("rxDiscards", "rx_discards"),
    "tx_discards": ("txDiscards", "tx_discards"),
    "crc_errors": ("crcErrors", "crc_errors", "crc"),
}


def counter_delta(current: int, previous: int) -> int:
    """Handle 32/64-bit counter wrap (same strategy as stats collection)."""
    try:
        cur = int(current or 0)
        prev = int(previous or 0)
    except (TypeError, ValueError):
        return 0
    if cur >= prev:
        return cur - prev
    modulus = 2**64 if prev > 2**32 else 2**32
    return (cur + modulus) - prev


def sample_timestamp(sample: dict[str, Any]) -> Optional[datetime]:
    ts = sample.get("timestamp")
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if getattr(ts, "tzinfo", None) is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts
    if isinstance(ts, str):
        try:
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return None
    return None


def read_counter(sample: dict[str, Any], logical_name: str) -> Optional[int]:
    """
    Read a counter from a stats sample.

    Returns ``None`` when the field is absent (unsupported metric),
    otherwise an int (including 0).
    """
    keys = COUNTER_FIELDS.get(logical_name, (logical_name,))
    for key in keys:
        if key in sample and sample[key] is not None:
            try:
                return int(sample[key])
            except (TypeError, ValueError):
                return None
    return None


def has_counter(sample: dict[str, Any], logical_name: str) -> bool:
    keys = COUNTER_FIELDS.get(logical_name, (logical_name,))
    return any(key in sample and sample[key] is not None for key in keys)


def rate_per_second(
    current: dict[str, Any],
    previous: Optional[dict[str, Any]],
    logical_name: str,
) -> tuple[Optional[float], bool]:
    """
    Compute packets (or units) per second for ``logical_name``.

    Returns
    -------
    (rate, supported)
        rate is None when previous is missing / interval invalid.
        supported is False when the counter field does not exist on samples.
    """
    if not has_counter(current, logical_name):
        return None, False
    if previous is None:
        return None, True
    if not has_counter(previous, logical_name):
        return None, False

    cur_val = read_counter(current, logical_name)
    prev_val = read_counter(previous, logical_name)
    if cur_val is None or prev_val is None:
        return None, False

    cur_ts = sample_timestamp(current)
    prev_ts = sample_timestamp(previous)
    if cur_ts is None or prev_ts is None:
        return None, True

    interval = (cur_ts - prev_ts).total_seconds()
    if interval <= 0:
        return None, True

    delta = counter_delta(cur_val, prev_val)
    return round(delta / interval, 4), True


def combined_error_rate(
    current: dict[str, Any],
    previous: Optional[dict[str, Any]],
) -> tuple[Optional[float], bool]:
    """Sum of input + output error rates."""
    in_rate, in_ok = rate_per_second(current, previous, "input_errors")
    out_rate, out_ok = rate_per_second(current, previous, "output_errors")
    if not in_ok and not out_ok:
        return None, False
    if in_rate is None and out_rate is None:
        # Supported but missing history / interval.
        return None, True
    total = (in_rate or 0.0) + (out_rate or 0.0)
    return round(total, 4), True


def read_utilization(sample: dict[str, Any]) -> tuple[Optional[float], bool]:
    """Utilization is already a percentage on the sample (not a counter)."""
    for key in ("utilization", "rxUtilization", "txUtilization"):
        if key in sample and sample[key] is not None:
            try:
                return float(sample[key]), True
            except (TypeError, ValueError):
                continue
    # Prefer overall; if only rx/tx present handled above. Absent → unsupported.
    return None, False


def load_stats_pair(
    device_id,
    interface_name: str,
    *,
    db=None,
) -> tuple[Optional[dict], Optional[dict]]:
    """
    Load the two newest ``interface_stats`` samples for an interface.

    Returns (current, previous). previous may be None.
    """
    if db is None:
        from config.database import db as _db  # noqa: PLC0415

        db = _db

    rows = list(
        db.interface_stats.find(
            {"deviceId": device_id, "interfaceName": interface_name}
        )
        .sort("timestamp", -1)
        .limit(2)
    )
    if not rows:
        return None, None
    if len(rows) == 1:
        return rows[0], None
    return rows[0], rows[1]
