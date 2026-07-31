"""
Recovery Safety Engine
======================
Independent validation for restoring a mitigated interface.

This module is intentionally separate from the Mitigation Safety Engine.
Mitigation asks: "Is it safe to shut the port down?"
Recovery asks:  "Is it safe to bring the port back up?"

Do not import or call services.storm.safety / safety_checks from here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId

from services.ping_service import STATUS_ONLINE
from services.settings_service import get_settings
from services.storm.incident import get_incident
from services.storm.lock_service import LockService
from services.storm.mitigation.ssh_executor import SSHMitigationExecutor
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.recovery.safety")

# Statuses that mean another storm/recovery lifecycle is still active.
_ACTIVE_INCIDENT_STATUSES = frozenset({
    "OPEN",
    "READY_FOR_MITIGATION",
    "MITIGATING",
    "MITIGATED",
    "RECOVERING",
    "MONITORING",
    "WAITING",
    "RECOVERY_FAILED",
    "REMITIGATE",
})

# Statuses from which recovery (no shutdown) is a valid next step.
_RECOVERABLE_STATUSES = frozenset({
    "OPEN",
    "MITIGATED",
    "RECOVERY_FAILED",
})


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _oid(val: Any) -> ObjectId:
    if isinstance(val, ObjectId):
        return val
    return ObjectId(str(val))


def _status_equals(a: Any, b: Any) -> bool:
    return str(a or "").strip().lower() == str(b or "").strip().lower()


def _is_admin_down(status: Optional[str]) -> bool:
    return str(status or "").strip().lower() in ("down", "disabled", "shutdown")


@dataclass
class RecoverySafetyResult:
    """Mitigation-style safety result for recovery decisions."""

    safe: bool
    reason: str
    failed_rule: Optional[str] = None
    checks: dict[str, Optional[bool]] = field(default_factory=dict)
    status: str = "UNSAFE"
    incident_id: Optional[str] = None
    device_id: Optional[str] = None
    interface: Optional[str] = None

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "safe": self.safe,
            "passed": self.safe,
            "reason": self.reason,
            "failedRule": self.failed_rule,
            "checks": dict(self.checks),
            "status": self.status,
            "incidentId": self.incident_id,
            "deviceId": self.device_id,
            "interface": self.interface,
        }


def check_cooldown_expired(incident_id: str, cooldown_minutes: int) -> bool:
    """R3 helper — cooldown since last successful mitigation."""
    last_mit = _db().storm_mitigation_history.find_one(
        {"incidentId": incident_id, "status": "SUCCESS"},
        sort=[("timestamp", -1)],
    )
    if not last_mit:
        logger.warning(
            "No successful mitigation history found for incident: %s", incident_id
        )
        return True

    mit_time = last_mit["timestamp"]
    if getattr(mit_time, "tzinfo", None) is None:
        mit_time = mit_time.replace(tzinfo=timezone.utc)

    elapsed = (datetime.now(timezone.utc) - mit_time).total_seconds()
    required = max(int(cooldown_minutes), 0) * 60
    expired = elapsed >= required
    logger.info(
        "Cooldown check | incident=%s | elapsed_sec=%s | required_sec=%s | expired=%s",
        incident_id,
        elapsed,
        required,
        expired,
    )
    return expired


def recovery_locks_available(device_id: Any, interface: str) -> bool:
    """R8 — true when no non-expired recovery lock blocks this device/interface."""
    coll = LockService.recovery_collection()
    device_lock_id, interface_lock_id = LockService.recovery_lock_ids(
        device_id, interface
    )
    now = datetime.now(timezone.utc)
    LockService._cleanup_expired_lock_ids(
        coll, device_lock_id, interface_lock_id, now=now
    )
    existing = coll.find_one({"_id": {"$in": [device_lock_id, interface_lock_id]}})
    return existing is None


def evaluate_recovery_safety(
    incident_id: str,
    *,
    probe_ssh: bool = True,
) -> RecoverySafetyResult:
    """
    Evaluate recovery-specific safety rules R1–R8.

    Cheap Mongo checks run first. SSH (R5/R6) runs only after R1–R4, R7, R8 pass.
    """
    settings = get_settings()
    cooldown_minutes = int(settings.get("cooldownMinutes", 5))
    risk_threshold = float(settings.get("reMitigationThreshold", 75.0))

    checks: dict[str, Optional[bool]] = {
        "stormCleared": None,          # R1
        "riskBelowThreshold": None,    # R2
        "cooldownExpired": None,       # R3
        "deviceReachable": None,       # R4
        "sshReachable": None,          # R5
        "interfaceAdminDown": None,    # R6
        "noNewerActiveIncident": None, # R7
        "recoveryLockAvailable": None, # R8
    }

    incident = get_incident(incident_id)
    if not incident:
        return RecoverySafetyResult(
            safe=False,
            reason="Incident not found",
            failed_rule="R0",
            checks=checks,
            status="UNSAFE",
            incident_id=incident_id,
        )

    device_id = incident.get("deviceId")
    interface = str(incident.get("interface") or "").strip()
    device_key = str(device_id) if device_id is not None else None

    if not device_id or not interface:
        return RecoverySafetyResult(
            safe=False,
            reason="Incident is missing deviceId or interface",
            failed_rule="R0",
            checks=checks,
            status="UNSAFE",
            incident_id=incident_id,
            device_id=device_key,
            interface=interface or None,
        )

    incident_type = str(
        incident.get("incidentType") or incident.get("type") or "STORM"
    ).upper()
    operator_driven = incident_type in ("EMERGENCY", "MANUAL")
    incident_status = str(incident.get("status") or "OPEN").upper()

    # Prerequisite: recoverable operational state
    if incident_status not in _RECOVERABLE_STATUSES:
        return RecoverySafetyResult(
            safe=False,
            reason=(
                f"Incident status {incident_status} is not recoverable "
                "(MITIGATION_FAILED / non-mitigated states cannot be recovered)"
            ),
            failed_rule="R0",
            checks=checks,
            status="UNSAFE",
            incident_id=incident_id,
            device_id=device_key,
            interface=interface,
        )

    device = _db().devices.find_one({"_id": _oid(device_id)})
    if not device:
        return RecoverySafetyResult(
            safe=False,
            reason="Device not found",
            failed_rule="R4",
            checks=checks,
            status="UNSAFE",
            incident_id=incident_id,
            device_id=device_key,
            interface=interface,
        )

    # ── Cheap checks (no SSH) ──────────────────────────────────────────
    # R3 Cooldown
    if incident_type == "EMERGENCY":
        checks["cooldownExpired"] = True
    else:
        checks["cooldownExpired"] = check_cooldown_expired(
            incident_id, cooldown_minutes
        )
    if not checks["cooldownExpired"]:
        return _fail(
            "R3",
            f"Cooldown active ({cooldown_minutes}m required)",
            checks,
            incident_id,
            device_key,
            interface,
        )

    # R4 Device reachable
    checks["deviceReachable"] = _status_equals(device.get("status"), STATUS_ONLINE)
    if not checks["deviceReachable"]:
        return _fail(
            "R4",
            f"Device offline ({device.get('status')})",
            checks,
            incident_id,
            device_key,
            interface,
        )

    # R1 / R2 — storm / risk (operator-driven recoveries skip)
    if operator_driven:
        checks["stormCleared"] = True
        checks["riskBelowThreshold"] = True
    else:
        latest_confirm = _db().storm_confirmation_history.find_one(
            {"deviceId": _oid(device_id), "interface": interface},
            sort=[("timestamp", -1)],
        )
        confirmed = bool(
            latest_confirm
            and (
                latest_confirm.get("confirmed")
                or str(latest_confirm.get("state") or "").upper() == "CONFIRMED"
            )
        )
        checks["stormCleared"] = not confirmed
        if not checks["stormCleared"]:
            return _fail(
                "R1",
                "Storm is still confirmed — recovery blocked",
                checks,
                incident_id,
                device_key,
                interface,
            )

        latest_risk = _db().storm_risk_history.find_one(
            {"deviceId": _oid(device_id), "interface": interface},
            sort=[("timestamp", -1)],
        )
        try:
            score = float((latest_risk or {}).get("riskScore") or 0)
        except (TypeError, ValueError):
            score = 0.0
        checks["riskBelowThreshold"] = score < risk_threshold
        if not checks["riskBelowThreshold"]:
            return _fail(
                "R2",
                f"Risk still high ({score:.1f} >= {risk_threshold:.0f})",
                checks,
                incident_id,
                device_key,
                interface,
            )

    # R7 No newer active incident
    created_at = incident.get("createdAt") or incident.get("timestamp")
    newer_query: dict[str, Any] = {
        "deviceId": _oid(device_id),
        "interface": interface,
        "incidentId": {"$ne": incident_id},
        "status": {"$in": list(_ACTIVE_INCIDENT_STATUSES)},
    }
    if created_at is not None:
        newer_query["createdAt"] = {"$gt": created_at}
    newer = _db().storm_incidents.find_one(newer_query, sort=[("createdAt", -1)])
    checks["noNewerActiveIncident"] = newer is None
    if not checks["noNewerActiveIncident"]:
        newer_id = (newer or {}).get("incidentId") or "unknown"
        return _fail(
            "R7",
            f"Newer active incident exists ({newer_id})",
            checks,
            incident_id,
            device_key,
            interface,
        )

    # R8 Recovery lock available
    try:
        checks["recoveryLockAvailable"] = recovery_locks_available(
            device_id, interface
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Recovery lock availability check failed | %s | %s", interface, exc)
        checks["recoveryLockAvailable"] = False
    if not checks["recoveryLockAvailable"]:
        return _fail(
            "R8",
            "Recovery lock conflict — another recovery is in progress",
            checks,
            incident_id,
            device_key,
            interface,
        )

    # ── Network checks (SSH) — only after cheap gates pass ─────────────
    if not probe_ssh:
        checks["sshReachable"] = None
        checks["interfaceAdminDown"] = None
        return RecoverySafetyResult(
            safe=False,
            reason="SSH probe required for recovery safety (R5/R6)",
            failed_rule="R5",
            checks=checks,
            status="UNSAFE",
            incident_id=incident_id,
            device_id=device_key,
            interface=interface,
        )

    ssh_ok = False
    admin_status: Optional[str] = None
    try:
        from services.storm.diagnostics.snapshots import (  # noqa: PLC0415
            parse_interface_snapshot,
        )

        with SSHMitigationExecutor(device) as executor:
            if executor.collector is not None:
                ssh_ok = True
                try:
                    output = executor.collector.run_command(
                        f"show interfaces {interface}"
                    )
                    snap = parse_interface_snapshot(output, interface)
                    admin_status = snap.get("adminStatus")
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Live admin-status probe failed | %s | %s", interface, exc
                    )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "SSH reachability failed during recovery safety | %s | %s", interface, exc
        )
        ssh_ok = False

    checks["sshReachable"] = ssh_ok
    if not ssh_ok:
        return _fail(
            "R5",
            "SSH unreachable",
            checks,
            incident_id,
            device_key,
            interface,
        )

    if admin_status is None:
        iface_doc = _db().interfaces.find_one(
            {"deviceId": _oid(device_id), "name": interface},
            {"adminStatus": 1},
        )
        admin_status = (iface_doc or {}).get("adminStatus")

    checks["interfaceAdminDown"] = _is_admin_down(admin_status)
    if not checks["interfaceAdminDown"]:
        return _fail(
            "R6",
            f"Interface already up (admin={admin_status or 'unknown'}) — nothing to recover",
            checks,
            incident_id,
            device_key,
            interface,
        )

    return RecoverySafetyResult(
        safe=True,
        reason="All recovery safety checks passed",
        failed_rule=None,
        checks=checks,
        status="SAFE",
        incident_id=incident_id,
        device_id=device_key,
        interface=interface,
    )


def _fail(
    rule: str,
    reason: str,
    checks: dict[str, Optional[bool]],
    incident_id: str,
    device_key: Optional[str],
    interface: str,
) -> RecoverySafetyResult:
    logger.info(
        "Recovery safety failed | %s | rule=%s | %s",
        interface,
        rule,
        reason,
    )
    return RecoverySafetyResult(
        safe=False,
        reason=reason,
        failed_rule=rule,
        checks=checks,
        status="UNSAFE",
        incident_id=incident_id,
        device_id=device_key,
        interface=interface,
    )
