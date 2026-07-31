"""
Recovery policy — thin facade over the Recovery Safety Engine.

Mitigation Safety Engine must never be called from recovery paths.
"""

from __future__ import annotations

from typing import Any

from services.storm.recovery.safety import (
    check_cooldown_expired,
    evaluate_recovery_safety,
)
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.recovery.policy")

__all__ = [
    "check_cooldown_expired",
    "validate_recovery_policy",
]


def validate_recovery_policy(
    incident_id: str,
    *,
    probe_ssh: bool = True,
) -> dict[str, Any]:
    """
    Evaluate recovery safety (R1–R8).

    Returns a mitigation-style result:
    {"passed", "safe", "checks", "reason", "failedRule", "status", ...}
    """
    result = evaluate_recovery_safety(incident_id, probe_ssh=probe_ssh)
    payload = result.to_api_dict()
    logger.info(
        "Recovery policy | incident=%s | safe=%s | failedRule=%s | %s",
        incident_id,
        payload.get("safe"),
        payload.get("failedRule"),
        payload.get("reason"),
    )
    return payload
