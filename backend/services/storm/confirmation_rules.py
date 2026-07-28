"""
Configurable Confirmation Engine rules and thresholds.

Administrators adjust values via environment variables — no code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return int(default)
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


# Confirmation states (exported for callers / tests).
STATE_NOT_CONFIRMED = "NOT_CONFIRMED"
STATE_PENDING = "PENDING"
STATE_CONFIRMED = "CONFIRMED"


@dataclass(frozen=True)
class ConfirmationConfig:
    """Policy knobs for storm confirmation."""

    confirmation_enabled: bool = True
    required_confirmations: int = 4
    risk_threshold: float = 75.0
    reset_on_poll_failure: bool = True
    reset_on_ineligible: bool = True
    reset_on_low_risk: bool = True
    # Max age of latest stats sample before treating as polling failure (seconds).
    poll_stale_seconds: int = 180

    def to_dict(self) -> dict:
        return {
            "confirmationEnabled": self.confirmation_enabled,
            "requiredConfirmations": self.required_confirmations,
            "riskThreshold": self.risk_threshold,
            "resetOnPollFailure": self.reset_on_poll_failure,
            "resetOnIneligible": self.reset_on_ineligible,
            "resetOnLowRisk": self.reset_on_low_risk,
            "pollStaleSeconds": self.poll_stale_seconds,
        }


@lru_cache(maxsize=1)
def get_confirmation_config() -> ConfirmationConfig:
    return ConfirmationConfig(
        confirmation_enabled=_env_bool("STORM_CONFIRMATION_ENABLED", True),
        required_confirmations=max(
            1, _env_int("STORM_REQUIRED_CONFIRMATIONS", 4)
        ),
        risk_threshold=max(0.0, min(100.0, _env_float("STORM_CONFIRMATION_RISK_THRESHOLD", 75.0))),
        reset_on_poll_failure=_env_bool("STORM_CONFIRMATION_RESET_ON_POLL_FAILURE", True),
        reset_on_ineligible=_env_bool("STORM_CONFIRMATION_RESET_ON_INELIGIBLE", True),
        reset_on_low_risk=_env_bool("STORM_CONFIRMATION_RESET_ON_LOW_RISK", True),
        poll_stale_seconds=max(30, _env_int("STORM_CONFIRMATION_POLL_STALE_SECONDS", 180)),
    )


def reload_confirmation_config() -> ConfirmationConfig:
    get_confirmation_config.cache_clear()
    return get_confirmation_config()


def state_from_consecutive(consecutive: int, required: int) -> str:
    """Map consecutive high-risk count onto the confirmation state machine."""
    if consecutive <= 0:
        return STATE_NOT_CONFIRMED
    if consecutive < required:
        return STATE_PENDING
    return STATE_CONFIRMED
