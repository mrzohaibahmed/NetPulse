"""
Independent safety check implementations (ordered RULE_1 … RULE_14).

Each check returns (passed, reason). The engine short-circuits on first failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from services.storm.safety_history import SafetyContext
from services.storm.safety_rules import SafetyConfig


@dataclass(frozen=True)
class SafetyCheck:
    code: str
    key: str
    reason_fail: str
    runner: Callable[[SafetyContext, SafetyConfig], tuple[bool, str]]


def _is_up(status: Optional[str]) -> bool:
    if not status:
        return False
    return str(status).strip().lower() in ("up", "connected", "enabled")


def check_storm_confirmed(ctx: SafetyContext, _cfg: SafetyConfig) -> tuple[bool, str]:
    conf = ctx.confirmation or {}
    confirmed = bool(conf.get("confirmed")) or str(conf.get("state", "")).upper() == "CONFIRMED"
    if confirmed:
        return True, "Storm still confirmed"
    return False, "Storm is not confirmed"


def check_device_online(ctx: SafetyContext, _cfg: SafetyConfig) -> tuple[bool, str]:
    device = ctx.device
    if device is None:
        return False, "Device not found"
    status = str(device.get("status") or "").lower()
    if status == "online":
        return True, "Device online"
    return False, f"Device offline ({device.get('status')})"


def check_ssh_reachable(ctx: SafetyContext, cfg: SafetyConfig) -> tuple[bool, str]:
    if not cfg.require_ssh:
        return True, "SSH check skipped"
    if ctx.ssh_reachable is True:
        return True, "SSH reachable"
    if ctx.ssh_reachable is False:
        return False, ctx.ssh_error or "SSH unreachable"
    # Not probed — fail closed when required
    return False, "SSH reachability unknown"


def check_interface_exists(ctx: SafetyContext, _cfg: SafetyConfig) -> tuple[bool, str]:
    if ctx.iface is not None:
        return True, "Interface exists"
    return False, "Interface removed / not found"


def check_interface_up(ctx: SafetyContext, _cfg: SafetyConfig) -> tuple[bool, str]:
    status = ctx.live_admin_status
    if status is None and ctx.iface:
        status = ctx.iface.get("adminStatus")
    if _is_up(status):
        return True, "Interface administratively up"
    return False, f"Interface already shutdown (admin={status or 'unknown'})"


def check_risk_still_high(ctx: SafetyContext, cfg: SafetyConfig) -> tuple[bool, str]:
    risk = ctx.risk or {}
    try:
        score = float(risk.get("riskScore") or 0)
    except (TypeError, ValueError):
        score = 0.0
    if score >= float(cfg.risk_threshold):
        return True, f"Risk still high ({score:.1f})"
    return False, f"Risk below threshold ({score:.1f} < {cfg.risk_threshold:.0f})"


def check_no_active_mitigation(ctx: SafetyContext, _cfg: SafetyConfig) -> tuple[bool, str]:
    if ctx.mitigation_running:
        return False, "Active mitigation already running"
    return True, "No active mitigation"


def check_cooldown_expired(ctx: SafetyContext, cfg: SafetyConfig) -> tuple[bool, str]:
    if cfg.cooldown_minutes <= 0:
        return True, "Cooldown disabled"
    if ctx.cooldown_remaining_seconds > 0:
        mins = max(1, ctx.cooldown_remaining_seconds // 60)
        return False, f"Cooldown active ({mins}m remaining)"
    return True, "Cooldown expired"


def check_automation_enabled(ctx: SafetyContext, cfg: SafetyConfig) -> tuple[bool, str]:
    extras = ctx.extras or {}
    if extras.get("manual_override") and cfg.allow_manual_override:
        return True, "Manual override enabled"
    if not cfg.automation_enabled:
        return False, "Global automation disabled"
    if not extras.get("device_automation", True):
        return False, "Device automation disabled"
    if not extras.get("interface_automation", True):
        return False, "Interface automation disabled"
    return True, "Automation enabled"


def check_not_maintenance(ctx: SafetyContext, _cfg: SafetyConfig) -> tuple[bool, str]:
    if (ctx.extras or {}).get("maintenance_mode"):
        return False, "Maintenance Mode Enabled"
    return True, "Not in maintenance mode"


def check_device_unlocked(ctx: SafetyContext, _cfg: SafetyConfig) -> tuple[bool, str]:
    if (ctx.extras or {}).get("device_locked"):
        return False, "Device is locked by administrator"
    return True, "Device unlocked"


def check_interface_unlocked(ctx: SafetyContext, _cfg: SafetyConfig) -> tuple[bool, str]:
    if (ctx.extras or {}).get("interface_locked"):
        return False, "Interface is locked by administrator"
    return True, "Interface unlocked"


def check_max_attempts(ctx: SafetyContext, cfg: SafetyConfig) -> tuple[bool, str]:
    attempts = int(ctx.mitigation_attempts or 0)
    if attempts >= int(cfg.maximum_attempts):
        return False, (
            f"Maximum mitigation attempts reached "
            f"({attempts}/{cfg.maximum_attempts})"
        )
    return True, f"Attempts remaining ({attempts}/{cfg.maximum_attempts})"


def check_device_healthy(ctx: SafetyContext, cfg: SafetyConfig) -> tuple[bool, str]:
    cpu = ctx.cpu_percent
    mem = ctx.memory_percent
    if cpu is None and mem is None:
        if cfg.fail_open_missing_health:
            return True, "Device health metrics unavailable (fail-open)"
        return False, "Device health metrics unavailable"
    if cpu is not None and cpu > cfg.cpu_threshold:
        return False, f"CPU above threshold ({cpu:.1f}% > {cfg.cpu_threshold:.0f}%)"
    if mem is not None and mem > cfg.memory_threshold:
        return False, (
            f"Memory above threshold ({mem:.1f}% > {cfg.memory_threshold:.0f}%)"
        )
    return True, "Device healthy"


def build_default_checks() -> tuple[SafetyCheck, ...]:
    return (
        SafetyCheck("RULE_1", "stormConfirmed", "Storm is not confirmed", check_storm_confirmed),
        SafetyCheck("RULE_2", "deviceOnline", "Device offline", check_device_online),
        SafetyCheck("RULE_3", "sshReachable", "SSH unreachable", check_ssh_reachable),
        SafetyCheck("RULE_4", "interfaceExists", "Interface removed", check_interface_exists),
        SafetyCheck("RULE_5", "interfaceUp", "Interface already shutdown", check_interface_up),
        SafetyCheck("RULE_6", "riskStillHigh", "Risk below threshold", check_risk_still_high),
        SafetyCheck("RULE_7", "mitigationRunning", "Active mitigation running", check_no_active_mitigation),
        # Note: key name in return object uses cooldownExpired (inverted semantics)
        SafetyCheck("RULE_8", "cooldownExpired", "Cooldown active", check_cooldown_expired),
        SafetyCheck("RULE_9", "automationEnabled", "Automation disabled", check_automation_enabled),
        SafetyCheck("RULE_10", "maintenanceMode", "Maintenance Mode Enabled", check_not_maintenance),
        SafetyCheck("RULE_11", "deviceLocked", "Device locked", check_device_unlocked),
        SafetyCheck("RULE_12", "interfaceLocked", "Interface locked", check_interface_unlocked),
        SafetyCheck("RULE_13", "attemptsOk", "Maximum attempts reached", check_max_attempts),
        SafetyCheck("RULE_14", "deviceHealthy", "Device unhealthy", check_device_healthy),
    )
