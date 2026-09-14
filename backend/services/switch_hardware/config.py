"""Configuration for Cisco switch hardware monitoring (Phase 1)."""

from __future__ import annotations

import os


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _env_float(name: str, default: float, *, minimum: float = 0.5) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        value = default
    else:
        try:
            value = float(raw)
        except ValueError:
            value = default
    return max(minimum, value)


def is_hardware_monitoring_enabled() -> bool:
    return _env_bool("SWITCH_HARDWARE_MONITORING_ENABLED", False)


def is_snmp_enabled() -> bool:
    return _env_bool("SWITCH_HARDWARE_SNMP_ENABLED", True)


def is_ssh_enabled() -> bool:
    return _env_bool("SWITCH_HARDWARE_SSH_ENABLED", True)


def poll_interval_seconds() -> int:
    return _env_int("SWITCH_HARDWARE_POLL_INTERVAL", 60, minimum=30)


def ssh_interval_seconds() -> int:
    return _env_int("SWITCH_HARDWARE_SSH_INTERVAL", 600, minimum=60)


def inventory_interval_seconds() -> int:
    return _env_int("SWITCH_HARDWARE_INVENTORY_INTERVAL", 3600, minimum=300)


def collection_timeout_seconds() -> float:
    return _env_float("SWITCH_HARDWARE_COLLECTION_TIMEOUT", 15.0, minimum=3.0)


def max_workers() -> int:
    return _env_int("SWITCH_HARDWARE_MAX_WORKERS", 8, minimum=1, maximum=32)


def cpu_warning_percent() -> float:
    return _env_float("SWITCH_HARDWARE_CPU_WARNING_PERCENT", 80.0, minimum=1.0)


def cpu_critical_percent() -> float:
    return _env_float("SWITCH_HARDWARE_CPU_CRITICAL_PERCENT", 95.0, minimum=1.0)


def memory_warning_percent() -> float:
    return _env_float("SWITCH_HARDWARE_MEMORY_WARNING_PERCENT", 85.0, minimum=1.0)


def memory_critical_percent() -> float:
    return _env_float("SWITCH_HARDWARE_MEMORY_CRITICAL_PERCENT", 95.0, minimum=1.0)
