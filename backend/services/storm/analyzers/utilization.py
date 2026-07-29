"""Interface utilization % analyzer."""

from __future__ import annotations

from typing import Optional

from services.storm.history import read_utilization
from services.storm.models import AnalyzerResult
from services.storm.thresholds import RiskConfig, score_from_thresholds


class UtilizationAnalyzer:
    metric = "utilization"

    def analyze(
        self,
        current: dict,
        previous: Optional[dict],
        config: RiskConfig,
        interface_context=None,
    ) -> AnalyzerResult:
        del previous, interface_context  # utilization is absolute % on the current sample
        value, supported = read_utilization(current)
        if not supported or value is None:
            return AnalyzerResult(
                metric=self.metric,
                value=None,
                score=0.0,
                supported=False,
                weight=config.weights.utilization,
            )
        score = score_from_thresholds(float(value), config.utilization)
        return AnalyzerResult(
            metric=self.metric,
            value=round(float(value), 4),
            score=score,
            supported=True,
            weight=config.weights.utilization,
        )
