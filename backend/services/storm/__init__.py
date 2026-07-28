"""
Storm Protection package.

Engines:
- Port Eligibility
- Risk Score
- Confirmation
- Safety
"""

from services.storm.confirmation import (
    ConfirmationEngine,
    ensure_confirmation_indexes,
    evaluate as evaluate_confirmation,
    evaluate_all_confirmations,
    get_confirmation_engine,
)
from services.storm.eligibility import (
    EligibilityEngine,
    evaluate,
    evaluate_all_interfaces,
    ensure_eligibility_indexes,
    get_eligibility_engine,
)
from services.storm.models import (
    ConfirmationResult,
    EligibilityChecks,
    EligibilityResult,
    RiskScoreResult,
    SafetyResult,
)
from services.storm.risk_engine import (
    RiskScoreEngine,
    calculate_all_risks,
    calculate_risk,
    ensure_risk_indexes,
    get_risk_engine,
)
from services.storm.safety import (
    SafetyEngine,
    ensure_safety_indexes,
    evaluate as evaluate_safety,
    evaluate_all_safety,
    get_safety_engine,
)

__all__ = [
    "ConfirmationEngine",
    "ConfirmationResult",
    "EligibilityChecks",
    "EligibilityEngine",
    "EligibilityResult",
    "RiskScoreEngine",
    "RiskScoreResult",
    "SafetyEngine",
    "SafetyResult",
    "calculate_all_risks",
    "calculate_risk",
    "evaluate",
    "evaluate_all_confirmations",
    "evaluate_all_interfaces",
    "evaluate_all_safety",
    "evaluate_confirmation",
    "evaluate_safety",
    "ensure_confirmation_indexes",
    "ensure_eligibility_indexes",
    "ensure_risk_indexes",
    "ensure_safety_indexes",
    "get_confirmation_engine",
    "get_eligibility_engine",
    "get_risk_engine",
    "get_safety_engine",
]
