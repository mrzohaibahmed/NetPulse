"""Multicast packets/sec analyzer."""

from __future__ import annotations

from typing import Any, Optional

from services.storm.analyzers.directional import score_directional_metric
from services.storm.models import AnalyzerResult
from services.storm.thresholds import RiskConfig


class MulticastAnalyzer:
    metric = "multicast"

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
            rx_logical="rx_multicast_packets",
            tx_logical="tx_multicast_packets",
            combined_logical="multicast_packets",
            thresholds=config.multicast,
            interface_context=interface_context,
        )
        if not supported:
            return AnalyzerResult(
                metric=self.metric,
                value=None,
                score=0.0,
                supported=False,
                weight=config.weights.multicast,
            )
        return AnalyzerResult(
            metric=self.metric,
            value=value,
            score=score,
            supported=True,
            weight=config.weights.multicast,
            detail=detail,
        )
