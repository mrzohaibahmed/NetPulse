"""
Post-recovery re-mitigation policy helpers.

Handles one automatic re-mitigation opportunity per recovery cycle,
accurate recovery history, BLOCKED throttling, and ESCALATED terminal state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from config.database import db
from services.storm.incident import append_timeline_event
from services.storm.recovery.audit import record_recovery_history
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.recovery.re_mitigation")

BLOCKED_THROTTLE_SECONDS = 300
ESCALATED_STATUS = "ESCALATED"
RULE_13 = "RULE_13"
MITIGATION_SAFETY_ENGINE = "mitigation_safety"


def is_post_recovery_re_mitigation_pending(incident: dict) -> bool:
    """
    True when this MONITORING incident still has its one post-recovery
    re-mitigation opportunity.

    Backward compatible: legacy MONITORING rows without the flag receive
    exactly one attempt unless postRecoveryReMitigationAttempted is set.
    """
    if incident.get("postRecoveryReMitigationPending") is True:
        return True
    if incident.get("postRecoveryReMitigationAttempted") is True:
        return False
    return str(incident.get("status") or "").upper() == "MONITORING"


def consume_re_mitigation_pending(incident_id: str, *, now: Optional[datetime] = None) -> None:
    """Mark the single post-recovery re-mitigation opportunity as consumed."""
    when = now or datetime.now(timezone.utc)
    db.storm_incidents.update_one(
        {"incidentId": incident_id},
        {
            "$set": {
                "postRecoveryReMitigationPending": False,
                "postRecoveryReMitigationAttempted": True,
                "updatedAt": when,
            }
        },
    )


def should_record_blocked_history(
    incident_id: str,
    *,
    failed_rule: Optional[str],
    reason: str,
    now: Optional[datetime] = None,
) -> bool:
    """Throttle identical BLOCKED recovery history rows for five minutes."""
    when = now or datetime.now(timezone.utc)
    last_blocked = db.storm_recovery_history.find_one(
        {"incidentId": incident_id, "recoveryStatus": "BLOCKED"},
        sort=[("timestamp", -1)],
    )
    if not last_blocked or not last_blocked.get("timestamp"):
        return True

    ts = last_blocked["timestamp"]
    if getattr(ts, "tzinfo", None) is None:
        ts = ts.replace(tzinfo=timezone.utc)

    prev = last_blocked.get("verificationResult") or {}
    same_rule = prev.get("failedRule") == failed_rule
    same_reason = prev.get("error") == reason
    elapsed = (when - ts).total_seconds()
    if same_rule and same_reason and elapsed < BLOCKED_THROTTLE_SECONDS:
        return False
    return True


def _record_blocked_history(
    *,
    incident_id: str,
    device_id: Any,
    interface: str,
    reason: str,
    failed_rule: Optional[str],
    checks: dict[str, Any],
    retry_count: int,
    now: Optional[datetime] = None,
) -> None:
    when = now or datetime.now(timezone.utc)
    if not should_record_blocked_history(
        incident_id,
        failed_rule=failed_rule,
        reason=reason,
        now=when,
    ):
        logger.info(
            "[RECOVERY.SCHEDULER] Skipping duplicate BLOCKED history | incident=%s | rule=%s",
            incident_id,
            failed_rule,
        )
        return

    record_recovery_history(
        incident_id=incident_id,
        device_id=device_id,
        interface=interface,
        recovery_status="BLOCKED",
        verification_result={
            "success": False,
            "error": reason,
            "failedRule": failed_rule,
            "checks": checks or {},
            "engine": MITIGATION_SAFETY_ENGINE,
        },
        retry_count=retry_count,
    )


def _escalation_message(failed_rule: Optional[str], reason: str) -> str:
    if failed_rule == RULE_13:
        return (
            "Storm remains active after recovery.\n"
            "Automatic re-mitigation was blocked because the maximum mitigation "
            "attempts were reached.\n"
            "Manual intervention is required."
        )
    detail = reason or "Automatic re-mitigation was blocked."
    return (
        "Storm remains active after recovery.\n"
        f"{detail}\n"
        "Manual intervention is required."
    )


def escalate_remitigation_blocked(
    incident: dict,
    *,
    reason: str,
    failed_rule: Optional[str] = None,
    checks: Optional[dict[str, Any]] = None,
    record_blocked: bool = True,
    now: Optional[datetime] = None,
) -> None:
    """Move incident to ESCALATED and notify operators."""
    incident_id = incident.get("incidentId")
    device_id = incident.get("deviceId")
    interface = incident.get("interface")
    when = now or datetime.now(timezone.utc)

    if str(incident.get("status") or "").upper() == ESCALATED_STATUS:
        logger.info(
            "[RECOVERY.SCHEDULER] Incident already escalated | incident=%s",
            incident_id,
        )
        return

    message = _escalation_message(failed_rule, reason)
    db.storm_incidents.update_one(
        {"incidentId": incident_id},
        {
            "$set": {
                "status": ESCALATED_STATUS,
                "updatedAt": when,
                "escalationReason": reason,
                "escalationFailedRule": failed_rule,
            }
        },
    )
    append_timeline_event(
        incident_id,
        "Re-Mitigation Escalated",
        detail=reason,
    )
    logger.warning(
        "[RECOVERY.SCHEDULER] Incident escalated — automatic re-mitigation stopped | "
        "incident=%s | rule=%s | %s",
        incident_id,
        failed_rule,
        reason,
    )

    if record_blocked:
        _record_blocked_history(
            incident_id=incident_id,
            device_id=device_id,
            interface=interface,
            reason=reason,
            failed_rule=failed_rule,
            checks=checks or {},
            retry_count=int(incident.get("recoveryRetryCount") or 0),
            now=when,
        )

    refreshed = dict(incident)
    refreshed["status"] = ESCALATED_STATUS
    device = None
    try:
        from bson import ObjectId  # noqa: PLC0415

        if device_id is not None:
            oid = device_id if isinstance(device_id, ObjectId) else ObjectId(str(device_id))
            device = db.devices.find_one({"_id": oid})
    except Exception:  # noqa: BLE001
        device = None

    alert_id = None
    try:
        from services.alert_service import (  # noqa: PLC0415
            create_storm_remitigation_blocked_alert,
        )

        alert_id = create_storm_remitigation_blocked_alert(
            refreshed,
            device=device,
            reason=message,
            failed_rule=failed_rule,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Re-mitigation blocked alert failed | incident=%s | %s",
            incident_id,
            exc,
        )

    try:
        from services.alert_service import mark_alert_email_sent  # noqa: PLC0415
        from services.email_service import (  # noqa: PLC0415
            send_storm_remitigation_blocked_notification,
        )

        email_sent = send_storm_remitigation_blocked_notification(
            refreshed,
            reason=message,
            failed_rule=failed_rule,
        )
        if alert_id:
            mark_alert_email_sent(alert_id, bool(email_sent))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Re-mitigation blocked email failed | incident=%s | %s",
            incident_id,
            exc,
        )


def _resolve_after_successful_re_mitigation(
    incident_id: str,
    target_incident_id: str,
    *,
    now: Optional[datetime] = None,
) -> None:
    when = now or datetime.now(timezone.utc)
    db.storm_incidents.update_one(
        {"incidentId": incident_id},
        {
            "$set": {
                "status": "RESOLVED",
                "updatedAt": when,
                "resolveReason": (
                    f"Post-recovery re-mitigation succeeded via {target_incident_id}"
                ),
                "postRecoveryReMitigationAttempted": True,
                "postRecoveryReMitigationPending": False,
            }
        },
    )
    append_timeline_event(
        incident_id,
        "Re-Mitigation Completed",
        detail=f"Port shut down again via incident {target_incident_id}",
    )


def handle_storm_reappearance(
    incident: dict,
    *,
    reason: str,
    trigger_result: dict[str, Any],
    now: Optional[datetime] = None,
) -> None:
    """
    Record accurate recovery history and escalate when automatic re-mitigation
    cannot proceed.
    """
    when = now or datetime.now(timezone.utc)
    incident_id = incident.get("incidentId")
    device_id = incident.get("deviceId")
    interface = incident.get("interface")
    retry_count = int(incident.get("recoveryRetryCount") or 0)
    history_incident_id = trigger_result.get("incidentId") or incident_id
    status = str(trigger_result.get("status") or "").upper()
    success = bool(trigger_result.get("success"))

    if success:
        logger.info(
            "[RECOVERY.SCHEDULER] Post-recovery re-mitigation succeeded | "
            "incident=%s | target=%s",
            incident_id,
            history_incident_id,
        )
        _resolve_after_successful_re_mitigation(
            incident_id,
            history_incident_id,
            now=when,
        )
        record_recovery_history(
            incident_id=history_incident_id,
            device_id=device_id,
            interface=interface,
            recovery_status="REMITIGATED",
            verification_result={
                "success": True,
                "note": f"Storm re-mitigated: {reason}",
            },
            retry_count=retry_count,
        )
        return

    failed_rule = trigger_result.get("failedRule")
    checks = trigger_result.get("checks") or {}
    error = trigger_result.get("error") or reason

    if status == "BLOCKED":
        logger.info(
            "[RECOVERY.SCHEDULER] Post-recovery re-mitigation blocked | "
            "incident=%s | rule=%s | %s",
            incident_id,
            failed_rule,
            error,
        )
        escalate_remitigation_blocked(
            incident,
            reason=error,
            failed_rule=failed_rule,
            checks=checks,
            now=when,
        )
        return

    # MITIGATION_FAILED or other execution failures
    logger.error(
        "[RECOVERY.SCHEDULER] Post-recovery re-mitigation failed | incident=%s | %s",
        incident_id,
        error,
    )
    record_recovery_history(
        incident_id=history_incident_id,
        device_id=device_id,
        interface=interface,
        recovery_status="FAILED",
        verification_result={
            "success": False,
            "error": error,
            "failedRule": failed_rule,
            "checks": checks,
            "engine": trigger_result.get("engine") or MITIGATION_SAFETY_ENGINE,
        },
        retry_count=retry_count,
    )
    escalate_remitigation_blocked(
        incident,
        reason=error,
        failed_rule=failed_rule,
        checks=checks,
        record_blocked=False,
        now=when,
    )


def handle_consumed_re_mitigation_opportunity(
    incident: dict,
    *,
    reason: str,
    now: Optional[datetime] = None,
) -> None:
    """
    Storm still present but the single post-recovery re-mitigation attempt
    was already consumed — escalate without calling trigger_re_mitigation.
    """
    when = now or datetime.now(timezone.utc)
    incident_id = incident.get("incidentId")
    if str(incident.get("status") or "").upper() == ESCALATED_STATUS:
        return
    logger.warning(
        "[RECOVERY.SCHEDULER] Storm reappeared after re-mitigation opportunity "
        "consumed | incident=%s | escalating",
        incident_id,
    )
    escalate_remitigation_blocked(
        incident,
        reason=(
            "Storm remains active after recovery. "
            "The automatic re-mitigation opportunity was already used. "
            "Manual intervention is required."
        ),
        failed_rule=RULE_13 if "maximum mitigation" in reason.lower() else None,
        now=when,
    )
