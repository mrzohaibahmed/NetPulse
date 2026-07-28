"""
Storm Protection package.

Engines:
- Port Eligibility (Milestone 1)
- Risk Score (Milestone 2)
"""

from services.storm.eligibility import (
    EligibilityEngine,
    evaluate,
    evaluate_all_interfaces,
    ensure_eligibility_indexes,
    get_eligibility_engine,
)
from services.storm.models import EligibilityChecks, EligibilityResult, RiskScoreResult
from services.storm.risk_engine import (
    RiskScoreEngine,
    calculate_all_risks,
    calculate_risk,
    ensure_risk_indexes,
    get_risk_engine,
)

__all__ = [
    "EligibilityChecks",
    "EligibilityEngine",
    "EligibilityResult",
    "RiskScoreEngine",
    "RiskScoreResult",
    "calculate_all_risks",
    "calculate_risk",
    "evaluate",
    "evaluate_all_interfaces",
    "ensure_eligibility_indexes",
    "ensure_risk_indexes",
    "get_eligibility_engine",
    "get_risk_engine",
]
