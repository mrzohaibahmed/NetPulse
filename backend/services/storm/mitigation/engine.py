"""
Mitigation execution engine.
Coordinates lock acquisition, SSH execution, verification, rollback, and auditing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from services.storm.incident import append_timeline_event, get_incident
from services.storm.mitigation.audit import record_mitigation_history
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

    # Allowed incident status checks per strategy
    allowed_statuses = {
        "SHUTDOWN": ["READY_FOR_MITIGATION", "PREPARED", "OPEN", "MITIGATION_FAILED"],
        "NO_SHUTDOWN": ["MITIGATED", "MITIGATION_FAILED", "READY_FOR_MITIGATION", "PREPARED", "OPEN"],
    }
    current_status = incident.get("status", "OPEN")
    if current_status not in allowed_statuses.get(strategy_name, []):
        raise ValueError(
            f"Mitigation rejected: Incident {incident_id} is in status '{current_status}', "
            f"which is not allowed for strategy '{strategy_name}'."
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

    # 1) Concurrency Locking (Distributed Mongo Lock)
    lock_coll = db.storm_mitigation_locks
    device_lock_id = f"device:{device_id}"
    interface_lock_id = f"interface:{device_id}:{interface}"

    try:
        lock_coll.insert_one(
            {
                "_id": device_lock_id,
                "deviceId": _oid(device_id),
                "createdAt": datetime.now(timezone.utc),
            }
        )
    except DuplicateKeyError as exc:
        raise ValueError(
            f"Mitigation lock conflict: Device {device_id} is currently executing another mitigation."
        ) from exc

    try:
        lock_coll.insert_one(
            {
                "_id": interface_lock_id,
                "deviceId": _oid(device_id),
                "interface": interface,
                "createdAt": datetime.now(timezone.utc),
            }
        )
    except DuplicateKeyError as exc:
        # Revert device lock
        lock_coll.delete_one({"_id": device_lock_id})
        raise ValueError(
            f"Mitigation lock conflict: Interface {interface} on Device {device_id} "
            f"is currently executing another mitigation."
        ) from exc

    # Execution State variables
    commands_executed: list[str] = []
    verification_passed = False
    verification_output = ""
    rollback_performed = False
    rollback_success = False

    append_timeline_event(
        incident_id,
        "Mitigation Started",
        detail=f"strategy={strategy_name}, operator={operator}",
    )

    try:
        # 2) Execute configuration change & verify
        with SSHMitigationExecutor(device) as executor:
            vendor = executor.creds.vendor
            cmds = strategy.get_commands(interface, vendor)
            commands_executed.extend(cmds)

            # Enter config mode and apply settings
            executor.execute_commands(cmds, interface)

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

        new_status = "MITIGATED" if strategy_name == "SHUTDOWN" else "RESOLVED"
        db.storm_incidents.update_one(
            {"incidentId": incident_id},
            {"$set": {"status": new_status, "updatedAt": datetime.now(timezone.utc)}},
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
                "error": str(exc),
                "output": verification_output,
            },
            rollback_performed=True,
            operator=operator,
        )

        return {
            "success": False,
            "status": history_status,
            "incidentId": incident_id,
            "error": str(exc),
            "commandsExecuted": commands_executed,
        }

    finally:
        # Concurrency Release Locks
        lock_coll.delete_many({"_id": {"$in": [device_lock_id, interface_lock_id]}})


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

    # Concurrency Locking (Distributed Mongo Lock)
    lock_coll = db.storm_mitigation_locks
    device_lock_id = f"device:{device_id}"
    interface_lock_id = f"interface:{device_id}:{interface}"

    try:
        lock_coll.insert_one(
            {
                "_id": device_lock_id,
                "deviceId": _oid(device_id),
                "createdAt": datetime.now(timezone.utc),
            }
        )
    except DuplicateKeyError as exc:
        raise ValueError(
            f"Mitigation lock conflict: Device {device_id} is busy with another mitigation."
        ) from exc

    try:
        lock_coll.insert_one(
            {
                "_id": interface_lock_id,
                "deviceId": _oid(device_id),
                "interface": interface,
                "createdAt": datetime.now(timezone.utc),
            }
        )
    except DuplicateKeyError as exc:
        lock_coll.delete_one({"_id": device_lock_id})
        raise ValueError(
            f"Mitigation lock conflict: Interface {interface} on Device {device_id} is busy."
        ) from exc

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
        lock_coll.delete_many({"_id": {"$in": [device_lock_id, interface_lock_id]}})

