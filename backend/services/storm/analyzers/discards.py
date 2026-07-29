"""Discard packets/sec analyzer."""

from __future__ import annotations

from typing import Any, Optional

from services.storm.analyzers.directional import score_directional_metric
from services.storm.models import AnalyzerResult
from services.storm.thresholds import RiskConfig


class DiscardAnalyzer:
    metric = "discards"

    def analyze(
        self,
        current: dict,
        previous: Optional[dict],
        config: RiskConfig,
        interface_context: Optional[dict[str, Any]] = None,
    ) -> AnalyzerResult:
        value, score, supported, detail = score_directional_metric(
            current,
            previous,
            rx_logical="rx_discards",
            tx_logical="tx_discards",
            combined_logical="discards",
            thresholds=config.discards,
            interface_context=interface_context,
        )
        if not supported:
            return AnalyzerResult(
                metric=self.metric,
                value=None,
                score=0.0,
                supported=False,
                weight=config.weights.discards,
            )
        return AnalyzerResult(
            metric=self.metric,
            value=value,
            score=score,
            supported=True,
            weight=config.weights.discards,
            detail=detail,
        )
