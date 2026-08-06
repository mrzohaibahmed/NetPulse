"""
Scheduler functions for periodic recovery and stabilization checking.
Runs inside APScheduler context.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from config.database import db
from services.settings_service import get_settings
from services.storm.incident import append_timeline_event
from services.storm.recovery.audit import record_recovery_history
from services.storm.recovery.engine import execute_recovery, trigger_re_mitigation
from services.storm.recovery.reconciliation import try_reconcile_from_scheduler
from services.storm.recovery.policy import validate_recovery_policy
from services.storm.recovery.re_mitigation import (
    consume_re_mitigation_pending,
    handle_consumed_re_mitigation_opportunity,
    handle_storm_reappearance,
    is_post_recovery_re_mitigation_pending,
)
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.recovery.scheduler")


def _incident_sort_key(inc: dict) -> tuple:
    """Newest-first key using createdAt, then incidentId as tie-breaker."""
    created = inc.get("createdAt") or inc.get("timestamp") or datetime.min.replace(
        tzinfo=timezone.utc
    )
    if getattr(created, "tzinfo", None) is None:
        created = created.replace(tzinfo=timezone.utc)
    return (created, str(inc.get("incidentId") or ""))


def _newest_mitigated_per_interface(
    incidents: list[dict],
    *,
    now: Optional[datetime] = None,
) -> list[dict]:
    """
    Keep only the newest MITIGATED incident per (deviceId, interface).

    Older duplicates are auto-resolved as superseded so they stop competing
    for recovery SSH and saturating the switch (which blocks rediscovery).
    """
    if not incidents:
        return []

    when = now or datetime.now(timezone.utc)
    groups: dict[tuple[str, str], list[dict]] = {}
    for inc in incidents:
        device_id = inc.get("deviceId")
        interface = str(inc.get("interface") or "").strip()
        if device_id is None or not interface:
            continue
        key = (str(device_id), interface)
        groups.setdefault(key, []).append(inc)

    selected: list[dict] = []
    for (_device_key, interface), group in groups.items():
        ordered = sorted(group, key=_incident_sort_key, reverse=True)
        newest = ordered[0]
        selected.append(newest)
        newest_id = newest.get("incidentId") or "unknown"
        for stale in ordered[1:]:
            stale_id = stale.get("incidentId")
            if not stale_id:
                continue
            try:
                append_timeline_event(
                    stale_id,
                    "Superseded",
                    detail=(
                        f"Older MITIGATED incident closed; recovery continues on "
                        f"{newest_id}."
                    ),
                )
                db.storm_incidents.update_one(
                    {"incidentId": stale_id, "status": "MITIGATED"},
                    {
                        "$set": {
                            "status": "RESOLVED",
                            "updatedAt": when,
                            "supersededBy": newest_id,
                            "resolveReason": (
                                f"Superseded by newer mitigated incident {newest_id}"
                            ),
                        }
                    },
                )
                logger.info(
                    "[RECOVERY.SCHEDULER] Superseded stale MITIGATED %s → %s | %s",
                    stale_id,
                    newest_id,
                    interface,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[RECOVERY.SCHEDULER] Failed to supersede %s: %s",
                    stale_id,
                    exc,
                )
    return selected


def run_recovery_cycle() -> None:
    """
    APScheduler periodic entry point.
    Runs every 30 seconds to handle stabilization monitoring and auto-recovery.
    """
    settings = get_settings()
    auto_recovery = bool(settings.get("autoRecovery", True))
    risk_threshold = float(settings.get("reMitigationThreshold", 25.0))

    now = datetime.now(timezone.utc)

    # 1) Manage Stabilization / MONITORING state
    try:
        monitoring_incidents = list(db.storm_incidents.find({"status": "MONITORING"}))
        for inc in monitoring_incidents:
            incident_id = inc.get("incidentId")
            device_id = inc.get("deviceId")
            interface = inc.get("interface")

            # Only evidence produced AFTER recovery may trigger remmitigation.
            recovered_at = inc.get("recoveredAt") or inc.get("updatedAt")
            if recovered_at is not None and getattr(recovered_at, "tzinfo", None) is None:
                recovered_at = recovered_at.replace(tzinfo=timezone.utc)

            latest_confirm = db.storm_confirmation_history.find_one(
                {"deviceId": device_id, "interface": interface},
                sort=[("timestamp", -1)],
            )
            latest_risk = db.storm_risk_history.find_one(
                {"deviceId": device_id, "interface": interface},
                sort=[("timestamp", -1)],
            )

            def _is_post_recovery(doc: dict | None) -> bool:
                if not doc or recovered_at is None:
                    return False
                ts = doc.get("timestamp")
                if ts is None:
                    return False
                if getattr(ts, "tzinfo", None) is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                return ts > recovered_at

            storm_reappeared = False
            reason = ""

            confirm_active = bool(
                latest_confirm
                and (
                    latest_confirm.get("confirmed")
                    or str(latest_confirm.get("state") or "").upper() == "CONFIRMED"
                )
            )
            if confirm_active and _is_post_recovery(latest_confirm):
                # Ignore post-recovery reset rows (reset=True, not confirmed).
                if not latest_confirm.get("reset"):
                    storm_reappeared = True
                    reason = "Storm confirmed by confirmation engine after recovery."
            elif (
                latest_risk
                and _is_post_recovery(latest_risk)
                and float(latest_risk.get("riskScore", 0)) >= risk_threshold
            ):
                storm_reappeared = True
                reason = (
                    f"Traffic risk score {latest_risk.get('riskScore')} "
                    "exceeded threshold after recovery."
                )

            if storm_reappeared:
                if not is_post_recovery_re_mitigation_pending(inc):
                    handle_consumed_re_mitigation_opportunity(inc, reason=reason, now=now)
                    continue

                logger.info(
                    "[RECOVERY.SCHEDULER] Storm reappeared — attempting post-recovery "
                    "re-mitigation | incident=%s | %s",
                    incident_id,
                    reason,
                )
                res = trigger_re_mitigation(
                    incident_id,
                    reason,
                    post_recovery_allowance=True,
                )
                consume_re_mitigation_pending(incident_id, now=now)
                handle_storm_reappearance(inc, reason=reason, trigger_result=res, now=now)
                continue

            # Check if stabilization period has completed cleanly
            stab_end = inc.get("stabilizationEnd")
            if stab_end:
                if stab_end.tzinfo is None:
                    stab_end = stab_end.replace(tzinfo=timezone.utc)

                if now >= stab_end:
                    logger.info("Stabilization finished cleanly | incident=%s", incident_id)
                    append_timeline_event(
                        incident_id,
                        "Recovery Completed",
                        detail="Stabilization monitoring period complete, port resolved.",
                    )
                    db.storm_incidents.update_one(
                        {"incidentId": incident_id},
                        {"$set": {"status": "RESOLVED", "updatedAt": now}},
                    )
                    # Belt-and-suspenders: keep pipeline invalidated at close.
                    from services.storm.recovery.post_recovery import (  # noqa: PLC0415
                        invalidate_pipeline_after_recovery,
                    )

                    invalidate_pipeline_after_recovery(
                        device_id,
                        interface,
                        incident_id=incident_id,
                        hostname=inc.get("hostname"),
                        ip_address=inc.get("ipAddress"),
                        reason=(
                            "Recovery resolved — monitoring baseline restored; "
                            "new storm required for mitigation"
                        ),
                    )
                    record_recovery_history(
                        incident_id=incident_id,
                        device_id=device_id,
                        interface=interface,
                        recovery_status="RECOVERED",
                        verification_result={
                            "success": True,
                            "note": "Stabilization finished cleanly",
                        },
                        retry_count=0,
                    )
    except Exception as exc:  # noqa: BLE001
        logger.error("[RECOVERY.SCHEDULER] Stabilization monitoring checks failed: %s", exc)

    # 2) Manage Auto-Recovery / MITIGATED state
    if not auto_recovery:
        return

    try:
        mitigated_incidents = list(
            db.storm_incidents.find({"status": "MITIGATED"})
        )
        # One recovery candidate per interface — older duplicates only burn SSH
        # budget (and can wedge the switch SSH daemon, starving rediscovery).
        mitigated_incidents = _newest_mitigated_per_interface(
            mitigated_incidents, now=now
        )
        for inc in mitigated_incidents:
            incident_id = inc.get("incidentId")
            device_id = inc.get("deviceId")
            interface = inc.get("interface")

            # Check if maximum recovery attempts exceeded
            max_attempts = int(settings.get("maximumRecoveryAttempts", 3))
            attempts = inc.get("recoveryRetryCount", 0)
            if attempts >= max_attempts:
                # Retries exceeded. Do not try auto-recovery again.
                continue

            # Recovery Safety first (SSH deferred until cheap gates pass).
            val_res = validate_recovery_policy(incident_id)
            if not val_res.get("passed"):
                failed_rule = val_res.get("failedRule")
                logger.info(
                    "[RECOVERY.SCHEDULER] Recovery safety blocked %s | rule=%s | %s",
                    incident_id,
                    failed_rule,
                    val_res.get("reason"),
                )
                # R6-only: reconcile out-of-sync MITIGATED incidents (port already UP).
                if failed_rule == "R6":
                    try:
                        try_reconcile_from_scheduler(incident_id, val_res)
                    except Exception as exc:  # noqa: BLE001
                        logger.error(
                            "[RECOVERY.SCHEDULER] R6 reconciliation failed | %s | %s",
                            incident_id,
                            exc,
                        )
                    continue

                # Throttle BLOCKED history: one row per incident / 5 minutes.
                # Do not key only on failedRule — R1/R2/R3 can oscillate each
                # 30s cycle and would otherwise spam identical-looking rows.
                last_blocked = db.storm_recovery_history.find_one(
                    {"incidentId": incident_id, "recoveryStatus": "BLOCKED"},
                    sort=[("timestamp", -1)],
                )
                should_record = True
                if last_blocked and last_blocked.get("timestamp"):
                    ts = last_blocked["timestamp"]
                    if getattr(ts, "tzinfo", None) is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if (now - ts).total_seconds() < 300:
                        should_record = False
                if should_record:
                    record_recovery_history(
                        incident_id=incident_id,
                        device_id=inc.get("deviceId"),
                        interface=inc.get("interface"),
                        recovery_status="BLOCKED",
                        verification_result={
                            "success": False,
                            "error": val_res.get("reason"),
                            "failedRule": failed_rule,
                            "checks": val_res.get("checks") or {},
                            "engine": "recovery_safety",
                        },
                        retry_count=attempts,
                    )
                continue

            logger.info(
                "[RECOVERY.SCHEDULER] Auto-recovery policy passed for %s", incident_id
            )
            try:
                # Policy already evaluated — skip re-validation to avoid a second SSH.
                res = execute_recovery(
                    incident_id,
                    force=False,
                    operator="SYSTEM",
                    skip_policy_validation=True,
                )
                logger.info(
                    "[RECOVERY.SCHEDULER] Auto-recovery executed for %s: %s",
                    incident_id,
                    res,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[RECOVERY.SCHEDULER] Auto-recovery run error | %s | %s",
                    incident_id,
                    exc,
                )
    except Exception as exc:  # noqa: BLE001
        logger.error("[RECOVERY.SCHEDULER] Auto-recovery checks failed: %s", exc)
