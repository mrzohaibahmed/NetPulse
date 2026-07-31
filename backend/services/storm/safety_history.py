"""
Safety context loaders — MongoDB + optional read-only SSH probes.

Never modifies switch configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from bson import ObjectId

from services.storm.lock_service import LockService


SAFETY_COLLECTION = "storm_safety_history"
CONFIRM_COLLECTION = "storm_confirmation_history"
RISK_COLLECTION = "storm_risk_history"


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _as_oid(device_id):
    if isinstance(device_id, ObjectId):
        return device_id
    if isinstance(device_id, str) and ObjectId.is_valid(device_id):
        return ObjectId(device_id)
    return device_id


@dataclass
class SafetyContext:
    """Snapshot of everything the Safety Engine needs for one evaluation."""

    device_id: Any
    interface: str
    device: Optional[dict] = None
    iface: Optional[dict] = None
    confirmation: Optional[dict] = None
    risk: Optional[dict] = None
    ssh_reachable: Optional[bool] = None
    ssh_error: Optional[str] = None
    live_admin_status: Optional[str] = None
    cpu_percent: Optional[float] = None
    memory_percent: Optional[float] = None
    mitigation_running: bool = False
    mitigation_attempts: int = 0
    last_safe_at: Optional[datetime] = None
    cooldown_remaining_seconds: int = 0
    extras: dict[str, Any] = field(default_factory=dict)


def load_device(device_id) -> Optional[dict]:
    return _db().devices.find_one({"_id": _as_oid(device_id)})


def load_interface(device_id, interface: str) -> Optional[dict]:
    return _db().interfaces.find_one(
        {"deviceId": _as_oid(device_id), "name": interface}
    )


def load_latest_confirmation(device_id, interface: str) -> Optional[dict]:
    return _db()[CONFIRM_COLLECTION].find_one(
        {"deviceId": _as_oid(device_id), "interface": interface},
        sort=[("timestamp", -1)],
    )


def load_latest_risk(device_id, interface: str) -> Optional[dict]:
    return _db()[RISK_COLLECTION].find_one(
        {"deviceId": _as_oid(device_id), "interface": interface},
        sort=[("timestamp", -1)],
    )


def load_last_safe_timestamp(device_id, interface: str) -> Optional[datetime]:
    row = _db()[SAFETY_COLLECTION].find_one(
        {
            "deviceId": _as_oid(device_id),
            "interface": interface,
            "safe": True,
        },
        sort=[("timestamp", -1)],
    )
    if not row:
        return None
    ts = row.get("timestamp")
    if isinstance(ts, datetime) and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def is_mitigation_running(device_id, interface: str) -> bool:
    """Read the same active mitigation locks used by the execution engine."""
    return LockService.is_mitigation_active(_as_oid(device_id), interface)


def count_mitigation_attempts(device_id, interface: str) -> int:
    """
    Prefer explicit counters on interface/device metadata; fall back to
    counting SAFE history rows (proxy until Mitigation Engine exists).
    """
    iface = load_interface(device_id, interface) or {}
    for key in ("mitigationAttempts", "stormMitigationAttempts"):
        if iface.get(key) is not None:
            try:
                return int(iface[key])
            except (TypeError, ValueError):
                pass

    device = load_device(device_id) or {}
    storm = device.get("storm") or {}
    if storm.get("mitigationAttempts") is not None:
        try:
            return int(storm["mitigationAttempts"])
        except (TypeError, ValueError):
            pass

    try:
        return int(
            _db()[SAFETY_COLLECTION].count_documents(
                {
                    "deviceId": _as_oid(device_id),
                    "interface": interface,
                    "safe": True,
                }
            )
        )
    except Exception:  # noqa: BLE001
        return 0


def _flag(doc: Optional[dict], *keys: str, default: bool = False) -> bool:
    if not doc:
        return default
    for key in keys:
        if key in doc and doc[key] is not None:
            return bool(doc[key])
        nested = doc.get("storm") if isinstance(doc.get("storm"), dict) else None
        if nested and key in nested and nested[key] is not None:
            return bool(nested[key])
    return default


def probe_ssh_readonly(device: dict, timeout: int = 15) -> tuple[bool, Optional[str]]:
    """
    Attempt a read-only SSH login. Does not change device configuration.
    """
    try:
        from services.interface_collection.ssh_collector import (  # noqa: PLC0415
            SSHCollectorError,
            SSHInterfaceCollector,
            resolve_ssh_credentials,
        )

        creds = resolve_ssh_credentials(device)
        # Soft-override connect timeout via socket default in collector path
        collector = SSHInterfaceCollector(creds)
        try:
            collector.connect()
            return True, None
        finally:
            try:
                collector.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def build_safety_context(
    device_id,
    interface: str,
    *,
    config=None,
    probe_ssh: bool = True,
    injected: Optional[SafetyContext] = None,
) -> SafetyContext:
    """Assemble context from MongoDB (and optional SSH) for evaluation."""
    if injected is not None:
        return injected

    from services.storm.safety_rules import get_safety_config  # noqa: PLC0415

    cfg = config or get_safety_config()
    name = str(interface).strip()
    device = load_device(device_id)
    iface = load_interface(device_id, name)
    confirmation = load_latest_confirmation(device_id, name)
    risk = load_latest_risk(device_id, name)
    last_safe = load_last_safe_timestamp(device_id, name)

    cooldown_remaining = 0
    if last_safe and cfg.cooldown_minutes > 0:
        expires = last_safe + timedelta(minutes=int(cfg.cooldown_minutes))
        now = datetime.now(timezone.utc)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        remaining = (expires - now).total_seconds()
        cooldown_remaining = max(0, int(remaining))

    ssh_ok: Optional[bool] = None
    ssh_err: Optional[str] = None
    if probe_ssh and device is not None and cfg.require_ssh:
        ssh_ok, ssh_err = probe_ssh_readonly(
            device, timeout=cfg.ssh_timeout_seconds
        )

    cpu = None
    mem = None
    if iface:
        cpu = iface.get("cpuPercent") or iface.get("cpu")
        mem = iface.get("memoryPercent") or iface.get("memory")
    if device:
        storm = device.get("storm") or {}
        health = device.get("health") or storm.get("health") or {}
        if cpu is None:
            cpu = health.get("cpuPercent", storm.get("cpuPercent"))
        if mem is None:
            mem = health.get("memoryPercent", storm.get("memoryPercent"))

    try:
        cpu_f = float(cpu) if cpu is not None else None
    except (TypeError, ValueError):
        cpu_f = None
    try:
        mem_f = float(mem) if mem is not None else None
    except (TypeError, ValueError):
        mem_f = None

    return SafetyContext(
        device_id=device_id,
        interface=name,
        device=device,
        iface=iface,
        confirmation=confirmation,
        risk=risk,
        ssh_reachable=ssh_ok,
        ssh_error=ssh_err,
        live_admin_status=(iface or {}).get("adminStatus"),
        cpu_percent=cpu_f,
        memory_percent=mem_f,
        mitigation_running=is_mitigation_running(device_id, name),
        mitigation_attempts=count_mitigation_attempts(device_id, name),
        last_safe_at=last_safe,
        cooldown_remaining_seconds=cooldown_remaining,
        extras={
            "automation_global": cfg.automation_enabled,
            "device_automation": _flag(
                device, "automationEnabled", "stormAutomationEnabled", default=True
            ),
            "interface_automation": _flag(
                iface, "automationEnabled", "stormAutomationEnabled", default=True
            ),
            "maintenance_mode": _flag(
                device, "maintenanceMode", "maintenance", default=False
            ),
            "device_locked": _flag(
                device, "locked", "deviceLocked", "adminLock", default=False
            ),
            "interface_locked": _flag(
                iface, "locked", "interfaceLocked", "adminLock", default=False
            ),
            "manual_override": _flag(
                device, "manualOverride", default=False
            )
            or _flag(iface, "manualOverride", default=False),
        },
    )
