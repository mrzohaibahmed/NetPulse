"""
Configurable risk thresholds and metric weights.

Administrators adjust values via environment variables — no code changes.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from functools import lru_cache


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, float(default)))


@dataclass(frozen=True)
class MetricThresholds:
    """
    Four-band thresholds for rate → score mapping.

    Band mapping (approx):
      ≤ low → 0–24 (LOW)
      ≤ medium → 25–49 (MEDIUM)
      ≤ high → 50–74 (HIGH)
      ≤ critical → 75–95
      > critical → up to 100
    """

    low: float
    medium: float
    high: float
    critical: float


@dataclass(frozen=True)
class RiskWeights:
    broadcast: float = 35.0
    multicast: float = 15.0
    unknown_unicast: float = 15.0
    utilization: float = 10.0
    errors: float = 10.0
    discards: float = 5.0
    crc: float = 5.0

    def as_dict(self) -> dict[str, float]:
        return {
            "broadcast": self.broadcast,
            "multicast": self.multicast,
            "unknown_unicast": self.unknown_unicast,
            "utilization": self.utilization,
            "errors": self.errors,
            "discards": self.discards,
            "crc": self.crc,
        }

    def weight_for(self, metric: str) -> float:
        return float(self.as_dict().get(metric, 0.0))


@dataclass(frozen=True)
class RiskConfig:
    """Full risk-engine configuration surface."""

    enable_risk: bool = True
    weights: RiskWeights = RiskWeights()
    broadcast: MetricThresholds = MetricThresholds(50, 200, 1000, 5000)
    multicast: MetricThresholds = MetricThresholds(100, 500, 2000, 8000)
    unknown_unicast: MetricThresholds = MetricThresholds(50, 200, 1000, 5000)
    utilization: MetricThresholds = MetricThresholds(30, 50, 75, 90)
    errors: MetricThresholds = MetricThresholds(1, 5, 20, 50)
    discards: MetricThresholds = MetricThresholds(1, 10, 50, 200)
    crc: MetricThresholds = MetricThresholds(1, 5, 20, 50)

    def to_dict(self) -> dict:
        return {
            "enableRisk": self.enable_risk,
            "weights": {
                "broadcast": self.weights.broadcast,
                "multicast": self.weights.multicast,
                "unknownUnicast": self.weights.unknown_unicast,
                "utilization": self.weights.utilization,
                "errors": self.weights.errors,
                "discards": self.weights.discards,
                "crc": self.weights.crc,
            },
            "thresholds": {
                "broadcast": asdict(self.broadcast),
                "multicast": asdict(self.multicast),
                "unknownUnicast": asdict(self.unknown_unicast),
                "utilization": asdict(self.utilization),
                "errors": asdict(self.errors),
                "discards": asdict(self.discards),
                "crc": asdict(self.crc),
            },
        }


def _thresholds(prefix: str, defaults: MetricThresholds) -> MetricThresholds:
    return MetricThresholds(
        low=_env_float(f"{prefix}_LOW", defaults.low),
        medium=_env_float(f"{prefix}_MEDIUM", defaults.medium),
        high=_env_float(f"{prefix}_HIGH", defaults.high),
        critical=_env_float(f"{prefix}_CRITICAL", defaults.critical),
    )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=1)
def get_risk_config() -> RiskConfig:
    """Load risk weights/thresholds from environment (cached)."""
    defaults = RiskConfig()
    return RiskConfig(
        enable_risk=_env_bool("STORM_ENABLE_RISK", True),
        weights=RiskWeights(
            broadcast=_env_float("STORM_WEIGHT_BROADCAST", 35),
            multicast=_env_float("STORM_WEIGHT_MULTICAST", 15),
            unknown_unicast=_env_float("STORM_WEIGHT_UNKNOWN_UNICAST", 15),
            utilization=_env_float("STORM_WEIGHT_UTILIZATION", 10),
            errors=_env_float("STORM_WEIGHT_ERRORS", 10),
            discards=_env_float("STORM_WEIGHT_DISCARDS", 5),
            crc=_env_float("STORM_WEIGHT_CRC", 5),
        ),
        broadcast=_thresholds("STORM_THRESH_BROADCAST", defaults.broadcast),
        multicast=_thresholds("STORM_THRESH_MULTICAST", defaults.multicast),
        unknown_unicast=_thresholds(
            "STORM_THRESH_UNKNOWN_UNICAST", defaults.unknown_unicast
        ),
        utilization=_thresholds("STORM_THRESH_UTILIZATION", defaults.utilization),
        errors=_thresholds("STORM_THRESH_ERRORS", defaults.errors),
        discards=_thresholds("STORM_THRESH_DISCARDS", defaults.discards),
        crc=_thresholds("STORM_THRESH_CRC", defaults.crc),
    )


def reload_risk_config() -> RiskConfig:
    get_risk_config.cache_clear()
    return get_risk_config()


def score_from_thresholds(value: float, thresholds: MetricThresholds) -> float:
    """Map a metric value onto 0–100 using configurable four-band thresholds."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v <= 0:
        return 0.0

    t = thresholds
    if v <= t.low:
        return _lerp(v, 0.0, t.low, 0.0, 24.0)
    if v <= t.medium:
        return _lerp(v, t.low, t.medium, 25.0, 49.0)
    if v <= t.high:
        return _lerp(v, t.medium, t.high, 50.0, 74.0)
    if v <= t.critical:
        return _lerp(v, t.high, t.critical, 75.0, 95.0)

    # Saturate toward 100 above critical.
    over = (v - t.critical) / max(t.critical, 1.0)
    return round(min(100.0, 95.0 + min(5.0, over * 5.0)), 2)


def severity_from_score(score: float) -> str:
    s = max(0.0, min(100.0, float(score)))
    if s < 25:
        return "LOW"
    if s < 50:
        return "MEDIUM"
    if s < 75:
        return "HIGH"
    return "CRITICAL"


def _lerp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    if x1 <= x0:
        return float(y1)
    ratio = (x - x0) / (x1 - x0)
    return round(y0 + ratio * (y1 - y0), 2)


# Silence unused helper when imported for typing only.
_ = _env_int
