"""
Directional counter rate helpers for broadcast / multicast / discard analyzers.

Falls back to legacy combined counters when RX/TX fields are absent so older
``interface_stats`` documents continue to score correctly.
"""

from __future__ import annotations

from typing import Any, Optional

from services.storm.history import rate_per_second
from services.storm.thresholds import MetricThresholds, score_from_thresholds


SECONDARY_WEIGHT = 0.65


def _port_topology(interface_context: Optional[dict[str, Any]]) -> tuple[bool, bool, bool]:
    ctx = interface_context or {}
    is_trunk = bool(ctx.get("is_trunk") or ctx.get("isTrunk"))
    is_uplink = bool(ctx.get("is_uplink") or ctx.get("isUplink"))
    is_access = bool(ctx.get("is_access") or ctx.get("isAccess"))
    if not is_access and not is_trunk and not is_uplink:
        mode = str(ctx.get("port_mode") or ctx.get("portMode") or "").lower()
        is_access = mode == "access"
        is_trunk = mode == "trunk"
    return is_access, is_trunk, is_uplink


def directional_rates(
    current: dict[str, Any],
    previous: Optional[dict[str, Any]],
    *,
    rx_logical: str,
    tx_logical: str,
    combined_logical: str,
) -> tuple[Optional[float], Optional[float], Optional[float], bool]:
    """
    Return ``(rx_rate, tx_rate, combined_rate, directional_supported)``.

    ``directional_supported`` is True when at least one RX/TX counter exists on
    the current sample (even if rate cannot be computed yet).
    """
    rx_rate, rx_ok = rate_per_second(current, previous, rx_logical)
    tx_rate, tx_ok = rate_per_second(current, previous, tx_logical)
    combined_rate, combined_ok = rate_per_second(
        current, previous, combined_logical
    )

    directional = rx_ok or tx_ok
    if directional:
        return rx_rate, tx_rate, combined_rate, True
    if combined_ok:
        return None, None, combined_rate, False
    return None, None, None, False


def score_directional_metric(
    current: dict[str, Any],
    previous: Optional[dict[str, Any]],
    *,
    rx_logical: str,
    tx_logical: str,
    combined_logical: str,
    thresholds: MetricThresholds,
    interface_context: Optional[dict[str, Any]] = None,
) -> tuple[Optional[float], float, bool, dict[str, Any]]:
    """
    Compute analyzer value + score using RX/TX when available.

    Access ports: primary RX, secondary TX (weighted).
    Trunk/uplink: max(RX score, TX score).
    Legacy: combined counter rate when directional fields missing.
    """
    rx_rate, tx_rate, combined_rate, directional = directional_rates(
        current,
        previous,
        rx_logical=rx_logical,
        tx_logical=tx_logical,
        combined_logical=combined_logical,
    )

    detail: dict[str, Any] = {
        "rxRate": rx_rate,
        "txRate": tx_rate,
        "combinedRate": combined_rate,
        "directional": directional,
    }

    if not directional and combined_rate is None:
        # Unsupported or missing history — mirror legacy analyzer behaviour.
        _, combined_ok = rate_per_second(current, previous, combined_logical)
        return None, 0.0, combined_ok, detail

    is_access, is_trunk, is_uplink = _port_topology(interface_context)
    trunk_like = is_trunk or is_uplink

    if directional:
        rx_score = 0.0 if rx_rate is None else score_from_thresholds(
            float(rx_rate), thresholds
        )
        tx_score = 0.0 if tx_rate is None else score_from_thresholds(
            float(tx_rate), thresholds
        )
        if trunk_like:
            score = max(rx_score, tx_score)
            value = max(
                (rx_rate or 0.0),
                (tx_rate or 0.0),
            )
            if rx_rate is None and tx_rate is None:
                value = None
                score = 0.0
        else:
            # Access (and unknown): RX primary, TX secondary.
            score = rx_score
            if tx_score > 0:
                score = max(rx_score, tx_score * SECONDARY_WEIGHT)
            value = rx_rate if rx_rate is not None else tx_rate
        detail["rxScore"] = round(rx_score, 2)
        detail["txScore"] = round(tx_score, 2)
        return value, score, True, detail

    # Legacy combined fallback.
    value = 0.0 if combined_rate is None else float(combined_rate)
    score = 0.0 if combined_rate is None else score_from_thresholds(
        value, thresholds
    )
    return (None if combined_rate is None else value), score, True, detail
