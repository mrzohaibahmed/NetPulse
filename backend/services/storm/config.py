"""
Storm Protection configuration.

Values are read from environment variables and may later be overridden
via persisted settings without changing engine code.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from functools import lru_cache


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class StormConfig:
    """Configurable policy knobs for the Port Eligibility Engine."""

    enable_eligibility: bool = True
    allow_management_ports: bool = False
    allow_trunks: bool = False
    allow_infrastructure_ports: bool = False
    allow_protected_ports: bool = False
    confidence: int = 100

    def to_dict(self) -> dict:
        """Return camelCase dict matching the storm_config contract."""
        return {
            "enableEligibility": self.enable_eligibility,
            "allowManagementPorts": self.allow_management_ports,
            "allowTrunks": self.allow_trunks,
            "allowInfrastructurePorts": self.allow_infrastructure_ports,
            "allowProtectedPorts": self.allow_protected_ports,
            "confidence": self.confidence,
        }


@lru_cache(maxsize=1)
def get_storm_config() -> StormConfig:
    """Load storm configuration from environment (cached)."""
    return StormConfig(
        enable_eligibility=_env_bool("STORM_ENABLE_ELIGIBILITY", True),
        allow_management_ports=_env_bool("STORM_ALLOW_MANAGEMENT_PORTS", False),
        allow_trunks=_env_bool("STORM_ALLOW_TRUNKS", False),
        allow_infrastructure_ports=_env_bool(
            "STORM_ALLOW_INFRASTRUCTURE_PORTS", False
        ),
        allow_protected_ports=_env_bool("STORM_ALLOW_PROTECTED_PORTS", False),
        confidence=max(0, min(100, int(os.getenv("STORM_ELIGIBILITY_CONFIDENCE", "100")))),
    )


def reload_storm_config() -> StormConfig:
    """Clear cache and reload configuration (useful in tests)."""
    get_storm_config.cache_clear()
    return get_storm_config()


def storm_config_as_dict() -> dict:
    """Public helper returning the active storm_config document shape."""
    from services.storm.confirmation_rules import get_confirmation_config
    from services.storm.thresholds import get_risk_config

    payload = get_storm_config().to_dict()
    payload["risk"] = get_risk_config().to_dict()
    payload["confirmation"] = get_confirmation_config().to_dict()
    return payload


# Avoid unused-import lint noise if asdict is needed by callers later.
_ = asdict
