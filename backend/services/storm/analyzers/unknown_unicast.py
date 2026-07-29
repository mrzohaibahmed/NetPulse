"""Unknown unicast packets/sec analyzer."""

from __future__ import annotations

from typing import Optional

from services.storm.history import rate_per_second
from services.storm.models import AnalyzerResult
from services.storm.thresholds import RiskConfig, score_from_thresholds


class UnknownUnicastAnalyzer:
    metric = "unknown_unicast"

    def analyze(
        self,
        current: dict,
        previous: Optional[dict],
        config: RiskConfig,
        interface_context=None,
    ) -> AnalyzerResult:
        del interface_context
        rate, supported = rate_per_second(
            current, previous, "unknown_unicast_packets"
        )
        if not supported:
            return AnalyzerResult(
                metric=self.metric,
                value=None,
                score=0.0,
                supported=False,
                weight=config.weights.unknown_unicast,
            )
        value = 0.0 if rate is None else float(rate)
        score = 0.0 if rate is None else score_from_thresholds(
            value, config.unknown_unicast
        )
        return AnalyzerResult(
            metric=self.metric,
            value=None if rate is None else value,
            score=score,
            supported=True,
            weight=config.weights.unknown_unicast,
        )
