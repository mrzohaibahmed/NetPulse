"""Input/output errors per second analyzer."""

from __future__ import annotations

from typing import Optional

from services.storm.history import combined_error_rate
from services.storm.models import AnalyzerResult
from services.storm.thresholds import RiskConfig, score_from_thresholds


class ErrorAnalyzer:
    metric = "errors"

    def analyze(
        self,
        current: dict,
        previous: Optional[dict],
        config: RiskConfig,
    ) -> AnalyzerResult:
        rate, supported = combined_error_rate(current, previous)
        if not supported:
            return AnalyzerResult(
                metric=self.metric,
                value=None,
                score=0.0,
                supported=False,
                weight=config.weights.errors,
            )
        value = 0.0 if rate is None else float(rate)
        score = 0.0 if rate is None else score_from_thresholds(
            value, config.errors
        )
        return AnalyzerResult(
            metric=self.metric,
            value=None if rate is None else value,
            score=score,
            supported=True,
            weight=config.weights.errors,
        )
