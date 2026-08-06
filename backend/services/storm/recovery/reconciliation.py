"""
Recovery reconciliation for MITIGATED incidents whose port was restored
outside NetPulse (Recovery Safety R6 — interface already administratively UP).

Does not alter mitigation, confirmation, risk, eligibility, or recovery execution.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services.settings_service import get_settings
from services.storm.incident import append_timeline_event, get_incident
from services.storm.lock_service import LockService
from services.storm.recovery.audit import record_recovery_history
from services.storm.recovery.post_recovery import invalidate_pipeline_after_recovery
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.recovery.reconciliation")

RECONCILED_STATUS = "RECONCILED"
RECONCILE_NOTE = "Interface already administratively UP. Scheduler reconciled incident."
RECONCILE_REASON = "Interface already administratively UP"
RECOVERY_RULE_R6 = "R6"
ENGINE_SCHEDULER = "recovery_scheduler"
DETECTED_BY_SCHEDULER = "scheduler"


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def is_recovery_active(device_id: Any, interface: str) -> bool:
    """True when a non-expired recovery lock exists for device/interface."""
    try:
        coll = LockService.recovery_collection()
        device_lock_id, interface_lock_id = LockService.recovery_lock_ids(
            device_id, interface
        )
        now = datetime.now(timezone.utc)
        LockService._cleanup_expired_lock_ids(
            coll, device_lock_id, interface_lock_id, now=now
        )
        lock = coll.find_one({"_id": {"$in": [device_lock_id, interface_lock_id]}})
        return lock is not None
    except Exception:  # noqa: BLE001
        return False


def can_reconcile_r6(
    incident: dict,
    val_res: dict[str, Any],
) -> tuple[bool, str]:
    """
    Return (eligible, reason) when ALL reconciliation conditions hold.

    Conditions:
      1. incident.status == MITIGATED
      2. safety failed ONLY on R6
      3. SSH checks confirm interface admin UP
      4. no recovery execution in progress
      5. no mitigation execution in progress
      6. incident not RESOLVED
    """
    incident_id = incident.get("incidentId")
    device_id = incident.get("deviceId")
    interface = incident.get("interface")
    status = str(incident.get("status") or "").upper()

    if status == "RESOLVED":
        return False, "Incident already RESOLVED"
    if status != "MITIGATED":
        return False, f"Reconciliation requires MITIGATED (got {status})"

    failed_rule = val_res.get("failedRule")
    if failed_rule != RECOVERY_RULE_R6:
        return False, f"Reconciliation requires R6-only failure (got {failed_rule})"

    checks = val_res.get("checks") or {}
    if checks.get("sshReachable") is not True:
        return False, "SSH verification did not confirm reachability"
    if checks.get("interfaceAdminDown") is not False:
        return False, "SSH verification did not confirm interface administratively UP"

    if is_recovery_active(device_id, interface):
        return False, "Recovery execution already in progress"

    if LockService.is_mitigation_active(device_id, interface):
        return False, "Mitigation execution already in progress"

    # Exactly one RECONCILED audit row per incident.
    existing = _db().storm_recovery_history.find_one(
        {"incidentId": incident_id, "recoveryStatus": RECONCILED_STATUS},
    )
    if existing:
        return False, "Incident already reconciled"

    return True, RECONCILE_REASON


def begin_stabilization_monitoring(
    incident_id: str,
    *,
    device_id: Any,
    interface: str,
    hostname: Optional[str] = None,
    ip_address: Optional[str] = None,
    invalidate_reason: str,
    extra_incident_fields: Optional[dict[str, Any]] = None,
) -> tuple[datetime, datetime]:
    """
    Shared MITIGATED → MONITORING transition (recovery + reconciliation).

    Reuses the same stabilization window and pipeline invalidation as
    successful recovery — does not touch mitigation history.
    """
    settings = get_settings()
    stabilization_seconds = int(settings.get("stabilizationSeconds", 60))
    now = datetime.now(timezone.utc)
    stab_end = now + timedelta(seconds=stabilization_seconds)

    update_fields: dict[str, Any] = {
        "status": "MONITORING",
        "stabilizationEnd": stab_end,
        "recoveredAt": now,
        "updatedAt": now,
        "postRecoveryReMitigationPending": True,
        "postRecoveryReMitigationAttempted": False,
    }
    if extra_incident_fields:
        update_fields.update(extra_incident_fields)

    _db().storm_incidents.update_one(
        {"incidentId": incident_id},
        {"$set": update_fields},
    )

    invalidate_pipeline_after_recovery(
        device_id,
        interface,
        incident_id=incident_id,
        hostname=hostname,
        ip_address=ip_address,
        reason=invalidate_reason,
    )

    return now, stab_end


def reconcile_mitigated_incident(
    incident_id: str,
    val_res: dict[str, Any],
    *,
    operator: str = "SYSTEM",
) -> dict[str, Any]:
    """
    Reconcile a MITIGATED incident when R6 confirms the port is already UP.

    Returns a result dict; does not raise when conditions are not met.
    """
    incident = get_incident(incident_id)
    if not incident:
        return {
            "success": False,
            "incidentId": incident_id,
            "skipped": True,
            "reason": "Incident not found",
        }

    eligible, eligibility_reason = can_reconcile_r6(incident, val_res)
    if not eligible:
        logger.info(
            "[RECOVERY.RECONCILE] Skipped | incident=%s | %s",
            incident_id,
            eligibility_reason,
        )
        return {
            "success": False,
            "incidentId": incident_id,
            "skipped": True,
            "reason": eligibility_reason,
        }

    device_id = incident.get("deviceId")
    interface = incident.get("interface")
    previous_status = str(incident.get("status") or "MITIGATED").upper()
    new_status = "MONITORING"

    now, _stab_end = begin_stabilization_monitoring(
        incident_id,
        device_id=device_id,
        interface=interface,
        hostname=incident.get("hostname"),
        ip_address=incident.get("ipAddress"),
        invalidate_reason=(
            "Post-reconcile reset — port already up; fresh confirmation/safety "
            "required before any new mitigation"
        ),
        extra_incident_fields={"reconciledAlreadyUp": True},
    )

    timeline_detail = (
        f"scheduler={DETECTED_BY_SCHEDULER}; interface={interface}; "
        f"incident={incident_id}; reason={RECONCILE_REASON}; "
        f"previousStatus={previous_status}; newStatus={new_status}"
    )
    append_timeline_event(
        incident_id,
        "Recovery Reconciled",
        detail=timeline_detail,
    )

    verification_result = {
        "success": True,
        "reconciled": True,
        "engine": ENGINE_SCHEDULER,
        "recoveryRule": RECOVERY_RULE_R6,
        "previousStatus": previous_status,
        "newStatus": new_status,
        "reason": RECONCILE_REASON,
        "note": RECONCILE_NOTE,
        "detectedBy": DETECTED_BY_SCHEDULER,
        "checks": val_res.get("checks") or {},
    }

    record_recovery_history(
        incident_id=incident_id,
        device_id=device_id,
        interface=interface,
        recovery_status=RECONCILED_STATUS,
        verification_result=verification_result,
        retry_count=int(incident.get("recoveryRetryCount") or 0),
    )

    logger.info(
        "[RECOVERY.RECONCILE] Completed | incident=%s | %s | operator=%s",
        incident_id,
        interface,
        operator,
    )
    return {
        "success": True,
        "incidentId": incident_id,
        "status": new_status,
        "reconciled": True,
        "recoveryStatus": RECONCILED_STATUS,
        "reason": RECONCILE_REASON,
        "timestamp": now.isoformat(),
    }


def try_reconcile_from_scheduler(
    incident_id: str,
    val_res: dict[str, Any],
) -> bool:
    """
    Scheduler hook: attempt reconciliation; return True if reconciled
    (caller must skip BLOCKED history).
    """
    result = reconcile_mitigated_incident(incident_id, val_res, operator="SYSTEM")
    return bool(result.get("success"))
