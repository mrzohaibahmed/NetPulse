"""
Enterprise emergency shutdown orchestration.

Skips storm eligibility/risk/confirmation/safety/diagnostics but reuses the
Mitigation Engine (SSH executor, verification, rollback, LockService).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId

from config.database import db
from services.audit_service import log_audit
from services.interface_collection.ssh_collector import resolve_ssh_credentials
from services.storm.incident import create_emergency_incident
from services.storm.lock_service import LockService
from services.storm.mitigation import execute_mitigation
from services.storm.safety_history import load_interface, probe_ssh_readonly
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.emergency_shutdown")

EMERGENCY_CONFIRMATION = "SHUTDOWN"
MIN_REASON_LENGTH = 10


class EmergencyShutdownError(Exception):
    """Validation or pre-flight failure before mitigation execution."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _oid(val: Any) -> ObjectId:
    if isinstance(val, ObjectId):
        return val
    return ObjectId(str(val))


def _device_vendor(device: dict, iface: Optional[dict]) -> str:
    creds = device.get("credentials") or {}
    return (
        str(creds.get("sshVendor") or "")
        or str((iface or {}).get("vendor") or "")
        or str(device.get("deviceType") or "")
    ).strip()


def _device_model(device: dict) -> str:
    return str(device.get("deviceType") or device.get("model") or "").strip()


def _sanitize_execution_error(error: str | None) -> str:
    if not error:
        return "Emergency shutdown failed"
    lowered = error.lower()
    if "lock conflict" in lowered:
        return "Mitigation lock conflict: another operation is in progress"
    if "verification failed" in lowered:
        return "Verification failed after shutdown attempt"
    if "ssh" in lowered or "connection" in lowered or "timeout" in lowered:
        return "SSH execution failed"
    if "not found" in lowered:
        return error
    return "Emergency shutdown failed"


def validate_emergency_request(
    *,
    device_id: str,
    interface: str,
    reason: str | None,
    confirmation: str | None,
) -> tuple[ObjectId, dict, dict]:
    if confirmation != EMERGENCY_CONFIRMATION:
        raise EmergencyShutdownError(
            f'confirmation must be exactly "{EMERGENCY_CONFIRMATION}"',
            status_code=400,
        )
    if not reason:
        raise EmergencyShutdownError("reason is required", status_code=400)
    if len(reason.strip()) < MIN_REASON_LENGTH:
        raise EmergencyShutdownError(
            f"reason must be at least {MIN_REASON_LENGTH} characters",
            status_code=400,
        )
    if not ObjectId.is_valid(device_id):
        raise EmergencyShutdownError("Invalid device ID", status_code=400)

    device_oid = _oid(device_id)
    device = db.devices.find_one({"_id": device_oid})
    if not device:
        raise EmergencyShutdownError("Device not found", status_code=404)

    if str(device.get("status") or "").lower() != "online":
        raise EmergencyShutdownError("Device is offline or unreachable", status_code=409)

    iface = load_interface(device_oid, interface)
    if not iface:
        raise EmergencyShutdownError("Invalid interface for device", status_code=404)

    # Use the same credential resolver as mitigation / interface collection
    # (device.credentials with SSH_DEFAULT_* environment fallbacks).
    try:
        resolve_ssh_credentials(device)
    except Exception as exc:  # noqa: BLE001
        raise EmergencyShutdownError(
            "SSH credentials are not configured",
            status_code=409,
        ) from exc

    ssh_ok, ssh_err = probe_ssh_readonly(device)
    if not ssh_ok:
        logger.warning(
            "Emergency shutdown SSH pre-check failed | device=%s | interface=%s | err=%s",
            device_id,
            interface,
            ssh_err,
        )
        raise EmergencyShutdownError("SSH is unavailable for this device", status_code=503)

    if LockService.is_mitigation_active(device_oid, interface):
        raise EmergencyShutdownError(
            "Mitigation lock conflict: another operation is in progress",
            status_code=409,
        )

    return device_oid, device, iface


def execute_emergency_shutdown(
    *,
    device_id: str,
    interface: str,
    reason: str,
    operator: str,
    source_ip: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """
    Run emergency shutdown: create incident, execute mitigation in EMERGENCY mode,
    append audit records. Locks are acquired/released by the mitigation engine.
    """
    started = time.perf_counter()
    device_oid, device, iface = validate_emergency_request(
        device_id=device_id,
        interface=interface,
        reason=reason,
        confirmation=EMERGENCY_CONFIRMATION,
    )

    now = datetime.now(timezone.utc)
    vendor = _device_vendor(device, iface)

    incident = create_emergency_incident(
        device_oid,
        interface,
        requested_by=operator,
        requested_at=now,
        reason=reason.strip(),
        action="SHUTDOWN",
        incident_type="EMERGENCY",
        trigger_type="MANUAL",
        approved_by=operator,
        interface_snapshot=iface,
        persist=True,
    )
    incident_id = str(incident.get("incidentId") or "").strip()
    if not incident_id:
        raise EmergencyShutdownError("Incident creation failed", status_code=500)

    audit_context = {
        "emergency": True,
        "reason": reason.strip(),
        "vendor": vendor,
        "sourceIp": source_ip,
        "sessionId": session_id,
        "deviceName": device.get("hostname") or device.get("ipAddress"),
    }

    res = execute_mitigation(
        incident_id,
        "SHUTDOWN",
        operator=operator,
        execution_mode="EMERGENCY",
        audit_context=audit_context,
    )

    execution_ms = int((time.perf_counter() - started) * 1000)

    hist = db.storm_mitigation_history.find_one(
        {"incidentId": incident_id},
        sort=[("timestamp", -1)],
    )
    verification_passed = bool((hist or {}).get("verificationResult", {}).get("success"))
    rollback_performed = bool((hist or {}).get("rollbackPerformed"))

    log_audit(
        action="Emergency Shutdown",
        entity_type="incident",
        entity_id=incident_id,
        details={
            "operator": operator,
            "timestamp": now.isoformat(),
            "device": str(device_oid),
            "deviceName": device.get("hostname"),
            "interface": interface,
            "reason": reason.strip(),
            "vendor": vendor,
            "strategy": "SHUTDOWN",
            "verification": "Passed" if verification_passed else "Failed",
            "rollback": "Yes" if rollback_performed else "No",
            "executionTimeMs": execution_ms,
            "sourceIp": source_ip,
            "sessionId": session_id,
            "emergency": True,
            "stormMitigationStatus": res.get("status"),
            "result": "Success" if res.get("success") else "Failed",
        },
    )

    success = bool(res.get("success"))
    return {
        "success": success,
        "status": res.get("status"),
        "incidentId": incident_id,
        "error": None if success else _sanitize_execution_error(res.get("error")),
        "verificationPassed": verification_passed,
        "rollbackPerformed": rollback_performed,
        "executionTimeMs": execution_ms,
    }
