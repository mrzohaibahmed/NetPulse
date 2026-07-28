"""Base types for independent risk analyzers."""

from __future__ import annotations

from typing import Optional, Protocol

from services.storm.models import AnalyzerResult
from services.storm.thresholds import RiskConfig


class Analyzer(Protocol):
    """Each analyzer scores exactly one metric family."""

    metric: str

    def analyze(
        self,
        current: dict,
        previous: Optional[dict],
        config: RiskConfig,
    ) -> AnalyzerResult:
        ...
