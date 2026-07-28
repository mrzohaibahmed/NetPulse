"""
Configurable Safety Engine policy.

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


@dataclass(frozen=True)
class SafetyConfig:
    """Policy knobs for pre-mitigation safety validation."""

    safety_enabled: bool = True
    automation_enabled: bool = True
    cooldown_minutes: int = 30
    cpu_threshold: float = 90.0
    memory_threshold: float = 90.0
    maximum_attempts: int = 3
    allow_manual_override: bool = False
    risk_threshold: float = 75.0
    require_ssh: bool = True
    # When CPU/memory metrics are unavailable, treat health check as passed.
    fail_open_missing_health: bool = True
    ssh_timeout_seconds: int = 15

    def to_dict(self) -> dict:
        return {
            "safetyEnabled": self.safety_enabled,
            "automationEnabled": self.automation_enabled,
            "cooldownMinutes": self.cooldown_minutes,
            "cpuThreshold": self.cpu_threshold,
            "memoryThreshold": self.memory_threshold,
            "maximumAttempts": self.maximum_attempts,
            "allowManualOverride": self.allow_manual_override,
            "riskThreshold": self.risk_threshold,
            "requireSsh": self.require_ssh,
            "failOpenMissingHealth": self.fail_open_missing_health,
            "sshTimeoutSeconds": self.ssh_timeout_seconds,
        }


@lru_cache(maxsize=1)
def get_safety_config() -> SafetyConfig:
    return SafetyConfig(
        safety_enabled=_env_bool("STORM_SAFETY_ENABLED", True),
        automation_enabled=_env_bool("STORM_AUTOMATION_ENABLED", True),
        cooldown_minutes=max(0, _env_int("STORM_COOLDOWN_MINUTES", 30)),
        cpu_threshold=max(1.0, min(100.0, _env_float("STORM_CPU_THRESHOLD", 90.0))),
        memory_threshold=max(
            1.0, min(100.0, _env_float("STORM_MEMORY_THRESHOLD", 90.0))
        ),
        maximum_attempts=max(1, _env_int("STORM_MAXIMUM_ATTEMPTS", 3)),
        allow_manual_override=_env_bool("STORM_ALLOW_MANUAL_OVERRIDE", False),
        risk_threshold=max(
            0.0, min(100.0, _env_float("STORM_SAFETY_RISK_THRESHOLD", 75.0))
        ),
        require_ssh=_env_bool("STORM_SAFETY_REQUIRE_SSH", True),
        fail_open_missing_health=_env_bool(
            "STORM_SAFETY_FAIL_OPEN_MISSING_HEALTH", True
        ),
        ssh_timeout_seconds=max(5, _env_int("STORM_SAFETY_SSH_TIMEOUT", 15)),
    )


def reload_safety_config() -> SafetyConfig:
    get_safety_config.cache_clear()
    return get_safety_config()
