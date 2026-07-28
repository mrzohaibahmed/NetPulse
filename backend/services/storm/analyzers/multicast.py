"""Multicast packets/sec analyzer."""

from __future__ import annotations

from typing import Optional

from services.storm.history import rate_per_second
from services.storm.models import AnalyzerResult
from services.storm.thresholds import RiskConfig, score_from_thresholds


class MulticastAnalyzer:
    metric = "multicast"

    def analyze(
        self,
        current: dict,
        previous: Optional[dict],
        config: RiskConfig,
    ) -> AnalyzerResult:
        rate, supported = rate_per_second(
            current, previous, "multicast_packets"
        )
        if not supported:
            return AnalyzerResult(
                metric=self.metric,
                value=None,
                score=0.0,
                supported=False,
                weight=config.weights.multicast,
            )
        value = 0.0 if rate is None else float(rate)
        score = 0.0 if rate is None else score_from_thresholds(
            value, config.multicast
        )
        return AnalyzerResult(
            metric=self.metric,
            value=None if rate is None else value,
            score=score,
            supported=True,
            weight=config.weights.multicast,
        )
