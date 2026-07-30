"""
Recovery coordination engine.
Manages locking, validation, recovery execution, retries, and re-mitigation triggers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId

from services.settings_service import get_settings
from services.storm.incident import append_timeline_event, get_incident
from services.storm.lock_service import LockService
from services.storm.mitigation.ssh_executor import SSHMitigationExecutor
from services.storm.mitigation.strategy import NoShutdownRecoveryStrategy
from services.storm.recovery.audit import record_recovery_history
from services.storm.recovery.policy import validate_recovery_policy
from services.storm.recovery.verifier import (
    collect_post_recovery_stats,
    verify_interface_up,
)
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.recovery.engine")


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _oid(val: Any) -> ObjectId:
    if isinstance(val, ObjectId):
        return val
    return ObjectId(str(val))


def execute_recovery(
    incident_id: str,
    *,
    force: bool = False,
    operator: str = "SYSTEM",
) -> dict[str, Any]:
    """
    Acquires recovery locks, runs policy validation (unless forced),
    executes recovery strategy, runs verification, and handles retry incrementation.
    """
    db = _db()
    incident = get_incident(incident_id)
    if not incident:
        raise ValueError(f"Incident not found: {incident_id}")

    device_id = incident.get("deviceId")
    interface = incident.get("interface")
    if not device_id or not interface:
        raise ValueError(f"Incident {incident_id} is missing deviceId or interface")

    settings = get_settings()
    max_attempts = int(settings.get("maximumRecoveryAttempts", 3))
    stabilization_seconds = int(settings.get("stabilizationSeconds", 60))

    # Concurrency Lock (shared with Safety and Mitigation)
    device_lock_id, interface_lock_id = LockService.acquire_recovery_locks(
        _oid(device_id), interface
    )

    # Execution state
    commands_run: list[str] = []
    verification_passed = False
    verification_output = ""
    retry_count = incident.get("recoveryRetryCount", 0)

    append_timeline_event(
        incident_id,
        "Recovery Started",
        detail=f"force={force}, operator={operator}",
    )

    try:
        # 1) Validate conditions (unless forced)
        if not force:
            val_res = validate_recovery_policy(incident_id)
            if not val_res.get("passed"):
                reason = val_res.get("reason") or "Policy rejected"
                logger.warning("Recovery policy rejected | incident=%s | %s", incident_id, reason)
                append_timeline_event(incident_id, "Recovery Blocked", detail=reason)
                raise ValueError(f"Recovery policy validation failed: {reason}")

        # Fetch device document
        device = db.devices.find_one({"_id": _oid(device_id)})
        if not device:
            raise ValueError(f"Device not found for ID: {device_id}")

        # 2) Execute Recovery Strategy ("no shutdown")
        strategy = NoShutdownRecoveryStrategy()
        with SSHMitigationExecutor(device) as executor:
            vendor = executor.creds.vendor
            cmds = strategy.get_commands(interface, vendor)
            commands_run.extend(cmds)

            # Apply config change
            executor.execute_commands(cmds, interface)

            # 3) Verify configuration immediately
            verification_passed, verification_output = verify_interface_up(executor, interface)

        # 4) Handle verification outcome
        if verification_passed:
            logger.info("Recovery verification passed | incident=%s", incident_id)
            append_timeline_event(incident_id, "Recovery Verified")

            # Calculate stabilization end timestamp
            now = datetime.now(timezone.utc)
            stab_end = now + timedelta(seconds=stabilization_seconds)

            # Update incident status to MONITORING for stabilization period
            db.storm_incidents.update_one(
                {"incidentId": incident_id},
                {
                    "$set": {
                        "status": "MONITORING",
                        "stabilizationEnd": stab_end,
                        "updatedAt": now,
                    }
                },
            )

            # Collect stats
            stats = collect_post_recovery_stats(device, interface)

            record_recovery_history(
                incident_id=incident_id,
                device_id=device_id,
                interface=interface,
                recovery_status="MONITORING",
                verification_result={
                    "success": True,
                    "output": verification_output,
                    "stats": stats,
                },
                retry_count=retry_count,
            )

            return {
                "success": True,
                "status": "MONITORING",
                "incidentId": incident_id,
                "stats": stats,
            }
        else:
            # Verification failed
            logger.warning("Recovery verification failed | incident=%s", incident_id)
            append_timeline_event(incident_id, "Recovery Verification Failed")
            raise RuntimeError("Interface state is not administratively UP after command execution")

    except Exception as exc:
        # Increment retry count
        retry_count += 1
        db.storm_incidents.update_one(
            {"incidentId": incident_id},
            {
                "$set": {
                    "recoveryRetryCount": retry_count,
                    "updatedAt": datetime.now(timezone.utc),
                }
            },
        )

        history_status = "FAILURE"
        if retry_count >= max_attempts:
            # Escalation
            history_status = "RECOVERY_FAILED"
            db.storm_incidents.update_one(
                {"incidentId": incident_id},
                {
                    "$set": {
                        "status": "RECOVERY_FAILED",
                        "updatedAt": datetime.now(timezone.utc),
                    }
                },
            )
            append_timeline_event(
                incident_id,
                "Recovery Failed",
                detail=f"Maximum recovery attempts exceeded ({retry_count}/{max_attempts}). Escalating incident.",
            )
        else:
            # Port is still shut down — leave status as MITIGATED so auto-recovery
            # can retry. Do not mislabel as MITIGATION_FAILED (that means the
            # shutdown itself never succeeded).
            append_timeline_event(
                incident_id,
                "Recovery Failed",
                detail=f"Attempt {retry_count} of {max_attempts} failed: {exc}",
            )

        record_recovery_history(
            incident_id=incident_id,
            device_id=device_id,
            interface=interface,
            recovery_status=history_status,
            verification_result={
                "success": False,
                "error": str(exc),
                "output": verification_output,
            },
            retry_count=retry_count,
        )

        return {
            "success": False,
            "status": history_status,
            "incidentId": incident_id,
            "error": str(exc),
            "retryCount": retry_count,
        }

    finally:
        # Release Recovery locks
        LockService.release_recovery_locks(device_lock_id, interface_lock_id)


def trigger_re_mitigation(incident_id: str, reason: str) -> dict[str, Any]:
    """Invokes Mitigation Orchestrator to shut down the interface again when storm reappears."""
    db = _db()
    incident = get_incident(incident_id)
    if not incident:
        return {"success": False, "incidentId": None, "error": "Incident not found"}

    device_id = incident.get("deviceId")
    interface = incident.get("interface")

    logger.warning("Storm reappeared! Re-mitigation triggered | incident=%s | %s", incident_id, reason)
    append_timeline_event(incident_id, "Storm Reappeared", detail=reason)
    target_incident_id = incident_id

    try:
        # 1) Call orchestrator prepare
        from services.storm.orchestrator import prepare as prepare_mitigation  # noqa: PLC0415
        prep_res = prepare_mitigation(
            device_id=device_id,
            interface=interface,
            probe_ssh=True,
            require_safety=True,
        )
        target_incident_id = str(prep_res.get("incidentId") or incident_id)

        # 2) Call mitigation engine
        if prep_res.get("ready"):
            append_timeline_event(
                target_incident_id,
                "Re-Mitigation Started",
                detail=f"Triggered by recovery incident {incident_id}",
            )
            from services.storm.mitigation.engine import execute_mitigation  # noqa: PLC0415
            res = execute_mitigation(
                target_incident_id, "SHUTDOWN", operator="SYSTEM"
            )
            logger.info(
                "Re-mitigation shutdown complete | incident=%s | status=%s",
                target_incident_id,
                res.get("status"),
            )
            return {
                "success": bool(res.get("success")),
                "incidentId": target_incident_id,
                "status": res.get("status"),
            }
        else:
            logger.error("Re-mitigation blocked by orchestrator preparation: %s", prep_res.get("reason"))
            db.storm_incidents.update_one(
                {"incidentId": target_incident_id},
                {
                    "$set": {
                        "status": "MITIGATION_FAILED",
                        "updatedAt": datetime.now(timezone.utc),
                    }
                },
            )
            append_timeline_event(
                target_incident_id,
                "Re-Mitigation Failed",
                detail=prep_res.get("reason"),
            )
            return {
                "success": False,
                "incidentId": target_incident_id,
                "status": "MITIGATION_FAILED",
                "error": prep_res.get("reason"),
            }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to execute re-mitigation | incident=%s: %s", incident_id, exc)
        db.storm_incidents.update_one(
            {"incidentId": target_incident_id},
            {
                "$set": {
                    "status": "MITIGATION_FAILED",
                    "updatedAt": datetime.now(timezone.utc),
                }
            },
        )
        append_timeline_event(
            target_incident_id,
            "Re-Mitigation Failed",
            detail=str(exc),
        )
        return {
            "success": False,
            "incidentId": target_incident_id,
            "status": "MITIGATION_FAILED",
            "error": str(exc),
        }


def retry_recovery(incident_id: str, operator: str = "SYSTEM") -> dict[str, Any]:
    """Manually resets retry count and triggers recovery execution."""
    db = _db()
    incident = get_incident(incident_id)
    if not incident:
        raise ValueError(f"Incident not found: {incident_id}")

    db.storm_incidents.update_one(
        {"incidentId": incident_id},
        {
            "$set": {
                "recoveryRetryCount": 0,
                "status": "OPEN",
                "updatedAt": datetime.now(timezone.utc),
            }
        },
    )

    return execute_recovery(incident_id, force=False, operator=operator)

