"""
Scheduler functions for periodic recovery and stabilization checking.
Runs inside APScheduler context.
"""

from __future__ import annotations

from datetime import datetime, timezone

from config.database import db
from services.settings_service import get_settings
from services.storm.incident import append_timeline_event
from services.storm.recovery.audit import record_recovery_history
from services.storm.recovery.engine import execute_recovery, trigger_re_mitigation
from services.storm.recovery.policy import validate_recovery_policy
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.recovery.scheduler")


def run_recovery_cycle() -> None:
    """
    APScheduler periodic entry point.
    Runs every 30 seconds to handle stabilization monitoring and auto-recovery.
    """
    settings = get_settings()
    auto_recovery = bool(settings.get("autoRecovery", True))
    risk_threshold = float(settings.get("reMitigationThreshold", 75.0))

    now = datetime.now(timezone.utc)

    # 1) Manage Stabilization / MONITORING state
    try:
        monitoring_incidents = list(db.storm_incidents.find({"status": "MONITORING"}))
        for inc in monitoring_incidents:
            incident_id = inc.get("incidentId")
            device_id = inc.get("deviceId")
            interface = inc.get("interface")

            # Check if a storm returned in the meantime
            latest_confirm = db.storm_confirmation_history.find_one(
                {"deviceId": device_id, "interface": interface},
                sort=[("timestamp", -1)],
            )
            latest_risk = db.storm_risk_history.find_one(
                {"deviceId": device_id, "interface": interface},
                sort=[("timestamp", -1)],
            )

            storm_reappeared = False
            reason = ""

            if latest_confirm and latest_confirm.get("confirmed"):
                storm_reappeared = True
                reason = "Storm confirmed by confirmation engine."
            elif latest_risk and float(latest_risk.get("riskScore", 0)) >= risk_threshold:
                storm_reappeared = True
                reason = f"Traffic risk score {latest_risk.get('riskScore')} exceeded threshold."

            if storm_reappeared:
                res = trigger_re_mitigation(incident_id, reason)
                history_incident_id = res.get("incidentId") or incident_id
                record_recovery_history(
                    incident_id=history_incident_id,
                    device_id=device_id,
                    interface=interface,
                    recovery_status="REMITIGATED",
                    verification_result={
                        "success": False,
                        "error": f"Storm re-mitigated: {reason}",
                    },
                    retry_count=inc.get("recoveryRetryCount", 0),
                )
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
            db.storm_incidents.find({"status": {"$in": ["MITIGATED", "MITIGATION_FAILED"]}})
        )
        for inc in mitigated_incidents:
            incident_id = inc.get("incidentId")

            # Check if maximum recovery attempts exceeded
            max_attempts = int(settings.get("maximumRecoveryAttempts", 3))
            attempts = inc.get("recoveryRetryCount", 0)
            if attempts >= max_attempts:
                # Retries exceeded. Do not try auto-recovery again.
                continue

            # Verify conditions
            val_res = validate_recovery_policy(incident_id)
            if val_res.get("passed"):
                logger.info("[RECOVERY.SCHEDULER] Auto-recovery policy passed for %s", incident_id)
                try:
                    res = execute_recovery(incident_id, force=False, operator="SYSTEM")
                    logger.info("[RECOVERY.SCHEDULER] Auto-recovery executed for %s: %s", incident_id, res)
                except Exception as exc:  # noqa: BLE001
                    logger.error("[RECOVERY.SCHEDULER] Auto-recovery run error | %s | %s", incident_id, exc)
    except Exception as exc:  # noqa: BLE001
        logger.error("[RECOVERY.SCHEDULER] Auto-recovery checks failed: %s", exc)
