"""
Mitigation execution engine.
Coordinates lock acquisition, SSH execution, verification, rollback, and auditing.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from services.storm.incident import append_timeline_event, get_incident
from services.storm.lock_service import LockService
from services.storm.mitigation.audit import record_mitigation_history
from services.storm.mitigation.authorization import validate_mitigation_authorization
from services.storm.mitigation.rollback import execute_rollback
from services.storm.mitigation.ssh_executor import SSHMitigationExecutor
from services.storm.mitigation.strategy import (
    NoShutdownRecoveryStrategy,
    ShutdownInterfaceStrategy,
)
from services.storm.mitigation.verifier import verify_mitigation
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.mitigation.engine")


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _oid(val: Any) -> ObjectId:
    if isinstance(val, ObjectId):
        return val
    return ObjectId(str(val))


def execute_mitigation(
    incident_id: str,
    strategy_name: str,
    operator: str = "SYSTEM",
    *,
    execution_mode: str | None = None,
    audit_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Acquires locks, runs the strategy configuration commands, verifies them,
    and runs auto-rollback if verification or connection fails.
    """
    db = _db()
    incident = get_incident(incident_id)
    if not incident:
        raise ValueError(f"Incident not found: {incident_id}")

    device_id = incident.get("deviceId")
    interface = incident.get("interface")
    if not device_id or not interface:
        raise ValueError(f"Incident {incident_id} is missing deviceId or interface")

    validate_mitigation_authorization(
        strategy_name=strategy_name,
        operator=operator,
        incident=incident,
        execution_mode=execution_mode,
        db=db,
    )

    # Defense in depth: automatic SYSTEM shutdowns require a live CONFIRMED
    # storm with a current SAFE safety result.
    if strategy_name == "SHUTDOWN" and str(operator).upper() == "SYSTEM":
        incident_type = str(
            incident.get("incidentType") or incident.get("type") or "STORM"
        ).upper()
        if incident_type == "STORM":
            latest_confirm = db.storm_confirmation_history.find_one(
                {
                    "deviceId": _oid(device_id),
                    "interface": interface,
                },
                sort=[("timestamp", -1)],
            )
            confirmed = bool(
                latest_confirm
                and (
                    latest_confirm.get("confirmed")
                    or str(latest_confirm.get("state") or "").upper() == "CONFIRMED"
                )
            )
            if not confirmed:
                raise ValueError(
                    "Mitigation rejected: storm is not currently confirmed. "
                    "Automatic shutdown requires a fresh confirmation sequence."
                )

            latest_safety = db.storm_safety_history.find_one(
                {
                    "deviceId": _oid(device_id),
                    "interface": interface,
                },
                sort=[("timestamp", -1)],
            )
            if not latest_safety or not latest_safety.get("safe"):
                raise ValueError(
                    "Mitigation rejected: latest safety result is not SAFE"
                )

    # Instantiate strategy
    if strategy_name == "SHUTDOWN":
        strategy = ShutdownInterfaceStrategy()
    elif strategy_name == "NO_SHUTDOWN":
        strategy = NoShutdownRecoveryStrategy()
    else:
        raise ValueError(f"Unsupported mitigation strategy: {strategy_name}")

    # Fetch device document
    device = db.devices.find_one({"_id": _oid(device_id)})
    if not device:
        raise ValueError(f"Device not found for ID: {device_id}")

    # 1) Concurrency Locking (shared with Safety and Recovery)
    device_lock_id, interface_lock_id = LockService.acquire_mitigation_locks(
        _oid(device_id), interface
    )

    # Execution State variables
    commands_executed: list[str] = []
    verification_passed = False
    verification_output = ""
    rollback_performed = False
    rollback_success = False

    audit_context = audit_context or {}
    is_emergency = execution_mode == "EMERGENCY"
    started = time.perf_counter()
    vendor: str | None = audit_context.get("vendor")

    append_timeline_event(
        incident_id,
        "Emergency Mitigation Started" if is_emergency else "Mitigation Started",
        detail=f"strategy={strategy_name}, operator={operator}, mode={execution_mode or 'STANDARD'}",
    )

    try:
        # 2) Execute configuration change & verify
        with SSHMitigationExecutor(device) as executor:
            vendor = executor.creds.vendor
            cmds = strategy.get_commands(interface, vendor)
            commands_executed.extend(cmds)

            # Enter config mode and apply settings
            executor.execute_commands(cmds, interface)

            if is_emergency:
                LockService.renew_lock(
                    device_lock_id,
                    interface_lock_id,
                    owner=operator,
                    execution_id=incident_id,
                )

            # Verify configuration change
            verification_passed, verification_output = verify_mitigation(
                executor, strategy, interface
            )

            if not verification_passed:
                logger.warning(
                    "Verification failed, executing auto-rollback | incident=%s",
                    incident_id,
                )
                append_timeline_event(
                    incident_id, "Verification Failed", detail=verification_output[:200]
                )

                # 3) Rollback
                rollback_success, _ = execute_rollback(
                    device, strategy, interface, executor
                )
                rollback_performed = True
                if rollback_success:
                    append_timeline_event(
                        incident_id,
                        "Rollback Completed",
                        detail="Interface state reverted successfully",
                    )
                else:
                    append_timeline_event(
                        incident_id,
                        "Rollback Failed",
                        detail="Failed to revert interface state",
                    )
                raise RuntimeError("Mitigation verification failed immediately after execution")

        # Success flow
        logger.info(
            "Mitigation verification passed | incident=%s | strategy=%s",
            incident_id,
            strategy_name,
        )
        append_timeline_event(incident_id, "Verification Passed")

        event_name = "Shutdown Executed" if strategy_name == "SHUTDOWN" else "No Shutdown Executed"
        append_timeline_event(incident_id, event_name)

        now = datetime.now(timezone.utc)
        new_status = "MITIGATED" if strategy_name == "SHUTDOWN" else "RESOLVED"
        status_set: dict[str, Any] = {"status": new_status, "updatedAt": now}
        if strategy_name == "SHUTDOWN":
            status_set["mitigatedAt"] = now
        db.storm_incidents.update_one(
            {"incidentId": incident_id},
            {"$set": status_set},
        )

        record_mitigation_history(
            incident_id=incident_id,
            device_id=device_id,
            interface=interface,
            strategy=strategy_name,
            status="SUCCESS",
            commands_executed=commands_executed,
            verification_result={"success": True, "output": verification_output},
            rollback_performed=False,
            operator=operator,
            emergency=is_emergency,
            execution_mode=execution_mode,
            vendor=vendor,
            execution_time_ms=int((time.perf_counter() - started) * 1000),
            source_ip=audit_context.get("sourceIp"),
            session_id=audit_context.get("sessionId"),
            reason=audit_context.get("reason"),
        )

        # Automatic (SYSTEM) verified shutdown only — never block the workflow.
        # Alert creation and email delivery are independent operations.
        if strategy_name == "SHUTDOWN" and str(operator).upper() == "SYSTEM":
            refreshed = get_incident(incident_id) or incident
            alert_id = None
            try:
                from services.alert_service import (  # noqa: PLC0415
                    create_storm_shutdown_alert,
                )

                alert_id = create_storm_shutdown_alert(refreshed, device=device)
            except Exception as alert_exc:  # noqa: BLE001
                logger.warning(
                    "Storm shutdown alert failed | incident=%s | %s",
                    incident_id,
                    alert_exc,
                )

            try:
                from services.email_service import (  # noqa: PLC0415
                    send_storm_shutdown_notification,
                )
                from services.alert_service import (  # noqa: PLC0415
                    mark_alert_email_sent,
                )

                email_sent = send_storm_shutdown_notification(
                    refreshed,
                    verification_result={
                        "success": True,
                        "output": verification_output,
                    },
                    reason=(
                        (audit_context or {}).get("reason")
                        or refreshed.get("reason")
                        or "Storm confirmed — automatic port shutdown"
                    ),
                    operator=operator,
                )
                if alert_id:
                    mark_alert_email_sent(alert_id, bool(email_sent))
            except Exception as mail_exc:  # noqa: BLE001
                logger.warning(
                    "Storm shutdown email failed | incident=%s | %s",
                    incident_id,
                    mail_exc,
                )

        return {
            "success": True,
            "status": "SUCCESS",
            "incidentId": incident_id,
            "commandsExecuted": commands_executed,
        }

    except Exception as exc:
        # Auto-rollback if anything broke before verification / during execution
        if not rollback_performed:
            logger.warning(
                "Execution error, initiating auto-rollback | incident=%s | err=%s",
                incident_id,
                exc,
            )
            rollback_success, _ = execute_rollback(device, strategy, interface, None)
            rollback_performed = True
            if rollback_success:
                append_timeline_event(
                    incident_id,
                    "Rollback Completed",
                    detail="Interface state reverted successfully after exception",
                )
            else:
                append_timeline_event(
                    incident_id,
                    "Rollback Failed",
                    detail="Failed to revert interface state after exception",
                )

        new_status = "MITIGATION_FAILED"
        db.storm_incidents.update_one(
            {"incidentId": incident_id},
            {"$set": {"status": new_status, "updatedAt": datetime.now(timezone.utc)}},
        )

        history_status = "ROLLBACK_SUCCESS" if rollback_success else "FAILURE"
        record_mitigation_history(
            incident_id=incident_id,
            device_id=device_id,
            interface=interface,
            strategy=strategy_name,
            status=history_status,
            commands_executed=commands_executed,
            verification_result={
                "success": False,
                "error": "Mitigation execution failed",
                "output": verification_output,
            },
            rollback_performed=True,
            operator=operator,
            emergency=is_emergency,
            execution_mode=execution_mode,
            vendor=vendor,
            execution_time_ms=int((time.perf_counter() - started) * 1000),
            source_ip=audit_context.get("sourceIp"),
            session_id=audit_context.get("sessionId"),
            reason=audit_context.get("reason"),
        )

        safe_error = "Mitigation verification failed" if verification_passed is False else "Mitigation execution failed"

        if strategy_name == "SHUTDOWN" and str(operator).upper() == "SYSTEM":
            refreshed = get_incident(incident_id) or incident
            alert_id = None
            try:
                from services.alert_service import (  # noqa: PLC0415
                    create_storm_shutdown_failure_alert,
                )

                alert_id = create_storm_shutdown_failure_alert(
                    refreshed,
                    device=device,
                    action_status=new_status,
                )
            except Exception as alert_exc:  # noqa: BLE001
                logger.warning(
                    "Storm shutdown failure alert failed | incident=%s | %s",
                    incident_id,
                    alert_exc,
                )

            try:
                from services.email_service import (  # noqa: PLC0415
                    send_storm_mitigation_failure,
                )
                from services.alert_service import (  # noqa: PLC0415
                    mark_alert_email_sent,
                )

                email_sent = send_storm_mitigation_failure(
                    refreshed,
                    verification_result={
                        "success": False,
                        "error": safe_error,
                        "output": verification_output,
                    },
                    reason=safe_error,
                    operator=operator,
                    action_status=new_status,
                )
                if alert_id:
                    mark_alert_email_sent(alert_id, bool(email_sent))
            except Exception as mail_exc:  # noqa: BLE001
                logger.warning(
                    "Storm mitigation failure email failed | incident=%s | %s",
                    incident_id,
                    mail_exc,
                )

        return {
            "success": False,
            "status": history_status,
            "incidentId": incident_id,
            "error": safe_error,
            "commandsExecuted": commands_executed,
        }

    finally:
        # Concurrency Release Locks
        LockService.release_mitigation_locks(device_lock_id, interface_lock_id)


def rollback_mitigation(
    incident_id: str,
    operator: str = "SYSTEM",
) -> dict[str, Any]:
    """
    Manually roll back applied configuration change on device/interface.
    Locates the applied strategy (SHUTDOWN or NO_SHUTDOWN) and triggers rollback.
    """
    db = _db()
    incident = get_incident(incident_id)
    if not incident:
        raise ValueError(f"Incident not found: {incident_id}")

    device_id = incident.get("deviceId")
    interface = incident.get("interface")
    if not device_id or not interface:
        raise ValueError(f"Incident missing deviceId or interface")

    device = db.devices.find_one({"_id": _oid(device_id)})
    if not device:
        raise ValueError(f"Device not found for ID: {device_id}")

    # Concurrency Locking (shared with Safety and Recovery)
    device_lock_id, interface_lock_id = LockService.acquire_mitigation_locks(
        _oid(device_id), interface
    )

    append_timeline_event(
        incident_id,
        "Manual Rollback Started",
        detail=f"operator={operator}",
    )

    try:
        # Look up last applied strategy from history
        from services.storm.mitigation.audit import COLLECTION as HIST_COLL  # noqa: PLC0415
        hist = db[HIST_COLL].find_one(
            {"incidentId": incident_id, "status": "SUCCESS"},
            sort=[("timestamp", -1)],
        )

        if hist and hist.get("strategy") == "NO_SHUTDOWN":
            strategy = NoShutdownRecoveryStrategy()
        else:
            # Default fallback to ShutdownInterfaceStrategy (so rollback does no shutdown)
            strategy = ShutdownInterfaceStrategy()

        success, commands = execute_rollback(device, strategy, interface, None)

        if success:
            append_timeline_event(incident_id, "Manual Rollback Completed")
            # If we rolled back Shutdown, it is now no shutdown, so status can be OPEN.
            # If we rolled back No Shutdown, it is now shutdown, so status can be MITIGATED.
            new_status = "OPEN" if strategy.name == "SHUTDOWN" else "MITIGATED"
            db.storm_incidents.update_one(
                {"incidentId": incident_id},
                {
                    "$set": {
                        "status": new_status,
                        "updatedAt": datetime.now(timezone.utc),
                    }
                },
            )

            record_mitigation_history(
                incident_id=incident_id,
                device_id=device_id,
                interface=interface,
                strategy=strategy.name,
                status="ROLLBACK_SUCCESS",
                commands_executed=commands,
                verification_result={
                    "success": True,
                    "note": "Manual rollback succeeded",
                },
                rollback_performed=True,
                operator=operator,
            )
            return {
                "success": True,
                "status": "ROLLBACK_SUCCESS",
                "incidentId": incident_id,
            }
        else:
            append_timeline_event(incident_id, "Manual Rollback Failed")
            record_mitigation_history(
                incident_id=incident_id,
                device_id=device_id,
                interface=interface,
                strategy=strategy.name,
                status="ROLLBACK_FAILURE",
                commands_executed=commands,
                verification_result={
                    "success": False,
                    "error": "Manual rollback command execution failed",
                },
                rollback_performed=True,
                operator=operator,
            )
            return {
                "success": False,
                "status": "ROLLBACK_FAILURE",
                "incidentId": incident_id,
            }

    finally:
        LockService.release_mitigation_locks(device_lock_id, interface_lock_id)

