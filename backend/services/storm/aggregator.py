"""
Weighted aggregator for independent risk analyzer outputs.

Unsupported analyzers are ignored (their weight is redistributed implicitly
by normalizing over supported weights only).
"""

from __future__ import annotations

from typing import Iterable

from services.storm.models import AnalyzerResult, RiskScoreResult
from services.storm.thresholds import severity_from_score


def aggregate_analyzer_results(
    results: Iterable[AnalyzerResult],
    *,
    eligible: bool = True,
    device_id: str | None = None,
    interface: str | None = None,
    timestamp=None,
) -> RiskScoreResult:
    """
    Combine analyzer scores into a single 0–100 risk score.

    Final score = Σ(score_i × weight_i) / Σ(weight_i) for supported analyzers
    that produced a numeric rate/value. Unsupported analyzers are ignored.
    """
    all_results = list(results)
    supported = [r for r in all_results if r.supported]
    # Include only analyzers that produced a value AND a non-zero score so
    # idle metrics do not dilute a dominant storm signal. Zero-score rows
    # remain visible in raw_metrics for explainability.
    scored = [
        r for r in supported
        if r.value is not None and float(r.score) > 0
    ]

    weight_sum = sum(max(float(r.weight), 0.0) for r in scored)
    if weight_sum <= 0:
        risk_score = 0.0
    else:
        weighted = sum(
            float(r.score) * max(float(r.weight), 0.0) for r in scored
        )
        risk_score = round(min(100.0, max(0.0, weighted / weight_sum)), 2)

    contributors = [
        r.to_contributor()
        for r in sorted(scored, key=lambda x: x.score, reverse=True)
    ]

    # Confidence: fraction of supported analyzers that yielded a rate/value.
    valued = [r for r in supported if r.value is not None]
    total_analyzers = len(all_results)
    supported_count = len(supported)
    valued_count = len(valued)

    if supported_count == 0:
        confidence = 0.0
    else:
        base = (valued_count / supported_count) * 100.0
        support_ratio = supported_count / max(total_analyzers, 1)
        confidence = round(min(100.0, base * (0.7 + 0.3 * support_ratio)), 2)

    raw_metrics = {
        r.metric: {
            "value": r.value,
            "score": round(float(r.score), 2),
            "supported": r.supported,
            "weight": r.weight,
            **({"detail": dict(r.detail)} if r.detail else {}),
        }
        for r in all_results
    }

    return RiskScoreResult(
        risk_score=risk_score,
        severity=severity_from_score(risk_score),
        confidence=confidence,
        contributors=contributors,
        eligible=eligible,
        timestamp=timestamp,
        device_id=device_id,
        interface=interface,
        raw_metrics=raw_metrics,
    )
