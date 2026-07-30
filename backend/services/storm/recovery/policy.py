"""
Validation checks for recovery policy rules.
Verifies the 7 conditions required before executing interface recovery.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from services.ping_service import STATUS_ONLINE
from services.settings_service import get_settings
from services.storm.incident import get_incident
from services.storm.mitigation.ssh_executor import SSHMitigationExecutor
from services.storm.safety import evaluate as evaluate_safety
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.recovery.policy")


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _oid(val: Any) -> ObjectId:
    if isinstance(val, ObjectId):
        return val
    return ObjectId(str(val))


def check_cooldown_expired(incident_id: str, cooldown_minutes: int) -> bool:
    """Rule 1: Check if the cooldown period has expired since the last successful mitigation."""
    db = _db()
    # Find last successful mitigation history record
    last_mit = db.storm_mitigation_history.find_one(
        {"incidentId": incident_id, "status": "SUCCESS"},
        sort=[("timestamp", -1)],
    )

    if not last_mit:
        # If there's no successful mitigation log, allow recovery (e.g. forced or manual recovery)
        logger.warning("No successful mitigation history found for incident: %s", incident_id)
        return True

    mit_time = last_mit["timestamp"]
    if mit_time.tzinfo is None:
        mit_time = mit_time.replace(tzinfo=timezone.utc)

    elapsed_seconds = (datetime.now(timezone.utc) - mit_time).total_seconds()
    required_seconds = cooldown_minutes * 60

    expired = elapsed_seconds >= required_seconds
    logger.info(
        "Cooldown check | incident=%s | elapsed_sec=%s | required_sec=%s | expired=%s",
        incident_id,
        elapsed_seconds,
        required_seconds,
        expired,
    )
    return expired


def validate_recovery_policy(incident_id: str) -> dict[str, Any]:
    """
    Evaluate the 7 recovery conditions.

    Returns
    -------
    dict
        A result dictionary: {"passed": bool, "checks": dict, "reason": str}
    """
    db = _db()
    settings = get_settings()

    cooldown_minutes = int(settings.get("cooldownMinutes", 5))
    risk_threshold = float(settings.get("reMitigationThreshold", 75.0))

    checks = {
        "cooldownExpired": False,
        "deviceReachable": False,
        "sshReachable": False,
        "stormNotConfirmed": False,
        "riskBelowThreshold": False,
        "safetyPassed": False,
        "incidentStillOpen": False,
    }

    # Fetch incident
    incident = get_incident(incident_id)
    if not incident:
        return {"passed": False, "checks": checks, "reason": "Incident not found"}

    device_id = incident.get("deviceId")
    interface = incident.get("interface")
    incident_type = str(
        incident.get("incidentType") or incident.get("type") or "STORM"
    ).upper()
    operator_driven = incident_type in ("EMERGENCY", "MANUAL")

    # Rule 7: Incident still in a recoverable operational state.
    # MITIGATION_FAILED is excluded — the port was never shut down, so there is
    # nothing to recover (admin re-mitigation is the correct path).
    incident_status = incident.get("status", "OPEN")
    if incident_status in ("OPEN", "MITIGATED", "RECOVERY_FAILED"):
        checks["incidentStillOpen"] = True

    # Fetch device
    device = db.devices.find_one({"_id": _oid(device_id)})
    if not device:
        return {"passed": False, "checks": checks, "reason": "Device not found"}

    # Rule 1: Cooldown expired (EMERGENCY recovery is immediate — skip cooldown)
    if incident_type == "EMERGENCY":
        checks["cooldownExpired"] = True
    else:
        checks["cooldownExpired"] = check_cooldown_expired(incident_id, cooldown_minutes)

    # Rule 2: Device reachable — status is authoritative (same as monitor/nmap/iface).
    # Do not use responseTime: a non-null value can be leftover and says nothing
    # about current reachability once status is offline.
    checks["deviceReachable"] = StringEqualsIgnoreCase(
        device.get("status"), STATUS_ONLINE
    )

    # Rule 3: SSH reachable
    ssh_ok = False
    try:
        with SSHMitigationExecutor(device) as executor:
            if executor.collector is not None:
                ssh_ok = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("SSH reachability policy check failed | %s | %s", interface, exc)

    checks["sshReachable"] = ssh_ok

    # Operator-driven EMERGENCY/MANUAL recoveries intentionally skip storm
    # confirmation + full Safety Engine (those gates do not apply).
    if operator_driven:
        checks["stormNotConfirmed"] = True
        checks["riskBelowThreshold"] = True
        checks["safetyPassed"] = True
    else:
        # Rule 4: Storm NOT confirmed
        latest_confirm = db.storm_confirmation_history.find_one(
            {"deviceId": _oid(device_id), "interface": interface},
            sort=[("timestamp", -1)],
        )
        if not latest_confirm or not latest_confirm.get("confirmed"):
            checks["stormNotConfirmed"] = True

        # Rule 5: Risk below configurable threshold
        latest_risk = db.storm_risk_history.find_one(
            {"deviceId": _oid(device_id), "interface": interface},
            sort=[("timestamp", -1)],
        )
        if not latest_risk or float(latest_risk.get("riskScore", 0)) < risk_threshold:
            checks["riskBelowThreshold"] = True

        # Rule 6: Safety Engine passes
        safety_ok = False
        try:
            safety_res = evaluate_safety(
                device_id=_oid(device_id),
                interface=interface,
                probe_ssh=False,
                # Recovery policy already performed an explicit SSH reachability
                # check above. Skip only the Safety SSH rule so the reused Safety
                # evaluation still enforces every other mitigation precondition.
                skip_check_codes={"RULE_3"},
                persist=False,
            )
            safety_ok = safety_res.safe
        except Exception as exc:  # noqa: BLE001
            logger.error("Safety evaluation failed during policy check: %s", exc)

        checks["safetyPassed"] = safety_ok

    # Aggregate result
    passed = all(checks.values())
    failed_rules = [k for k, v in checks.items() if not v]
    reason = "All recovery policy checks passed" if passed else f"Failed checks: {failed_rules}"

    return {"passed": passed, "checks": checks, "reason": reason}


def StringEqualsIgnoreCase(s1: Any, s2: Any) -> bool:
    return str(s1 or "").strip().lower() == str(s2 or "").strip().lower()
