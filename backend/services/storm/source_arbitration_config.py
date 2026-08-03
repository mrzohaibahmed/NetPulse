"""
Configurable storm source arbitration policy.

Loaded from environment; safe defaults preserve enterprise behaviour
(arbitration + receiver filtering ON).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return int(default)
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


@dataclass(frozen=True)
class SourceArbitrationConfig:
    """Policy knobs for source selection between Risk and Confirmation."""

    enable_source_arbitration: bool = True
    enable_receiver_filtering: bool = True
    filter_forwarders: bool = True
    allow_confirm_receivers: bool = False
    minimum_source_confidence: float = 40.0
    maximum_candidates: int = 25
    tie_threshold: float = 5.0
    receiver_penalty: float = 40.0
    forwarder_penalty: float = 35.0
    rx_weight: float = 1.0
    tx_penalty: float = 0.5
    risk_weight: float = 0.35
    unknown_unicast_weight: float = 0.15
    growth_weight: float = 0.1
    allow_multiple_sources: bool = False

    def to_dict(self) -> dict:
        return {
            "enableSourceArbitration": self.enable_source_arbitration,
            "enableReceiverFiltering": self.enable_receiver_filtering,
            "filterForwarders": self.filter_forwarders,
            "allowConfirmReceivers": self.allow_confirm_receivers,
            "minimumSourceConfidence": self.minimum_source_confidence,
            "maximumCandidates": self.maximum_candidates,
            "tieThreshold": self.tie_threshold,
            "receiverPenalty": self.receiver_penalty,
            "forwarderPenalty": self.forwarder_penalty,
            "rxWeight": self.rx_weight,
            "txPenalty": self.tx_penalty,
            "riskWeight": self.risk_weight,
            "unknownUnicastWeight": self.unknown_unicast_weight,
            "growthWeight": self.growth_weight,
            "allowMultipleSources": self.allow_multiple_sources,
        }


@lru_cache(maxsize=1)
def get_source_arbitration_config() -> SourceArbitrationConfig:
    return SourceArbitrationConfig(
        enable_source_arbitration=_env_bool("STORM_ENABLE_SOURCE_ARBITRATION", True),
        enable_receiver_filtering=_env_bool("STORM_ENABLE_RECEIVER_FILTERING", True),
        filter_forwarders=_env_bool("STORM_FILTER_FORWARDERS", True),
        allow_confirm_receivers=_env_bool("STORM_ALLOW_CONFIRM_RECEIVERS", False),
        minimum_source_confidence=max(
            0.0,
            min(100.0, _env_float("STORM_MINIMUM_SOURCE_CONFIDENCE", 40.0)),
        ),
        maximum_candidates=max(1, _env_int("STORM_MAXIMUM_SOURCE_CANDIDATES", 25)),
        tie_threshold=max(0.0, _env_float("STORM_SOURCE_TIE_THRESHOLD", 5.0)),
        receiver_penalty=max(0.0, _env_float("STORM_RECEIVER_PENALTY", 40.0)),
        forwarder_penalty=max(0.0, _env_float("STORM_FORWARDER_PENALTY", 35.0)),
        rx_weight=max(0.0, _env_float("STORM_SOURCE_RX_WEIGHT", 1.0)),
        tx_penalty=max(0.0, _env_float("STORM_SOURCE_TX_PENALTY", 0.5)),
        risk_weight=max(0.0, _env_float("STORM_SOURCE_RISK_WEIGHT", 0.35)),
        unknown_unicast_weight=max(
            0.0, _env_float("STORM_SOURCE_UNKNOWN_UNICAST_WEIGHT", 0.15)
        ),
        growth_weight=max(0.0, _env_float("STORM_SOURCE_GROWTH_WEIGHT", 0.1)),
        allow_multiple_sources=_env_bool("STORM_ALLOW_MULTIPLE_SOURCES", False),
    )


def reload_source_arbitration_config() -> SourceArbitrationConfig:
    get_source_arbitration_config.cache_clear()
    return get_source_arbitration_config()
