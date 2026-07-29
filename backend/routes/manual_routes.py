from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from flask import Blueprint, g, jsonify, request

from config.database import db
from services.audit_service import log_audit
from services.storm.emergency_shutdown import (
    EMERGENCY_CONFIRMATION,
    EmergencyShutdownError,
    execute_emergency_shutdown,
)
from services.storm.incident import (
    create_emergency_incident,
    find_open_incident,
    get_incident,
)
from services.storm.mitigation import execute_mitigation
from services.storm.orchestrator import prepare as prepare_mitigation
from services.storm.recovery import execute_recovery
from services.storm.safety import evaluate as evaluate_safety
from utils.auth import require_auth
from utils.monitor_logger import get_monitor_logger
from utils.rate_limit import EMERGENCY_SHUTDOWN_LIMITER, rate_limit

manual_bp = Blueprint("manual", __name__)
logger = get_monitor_logger("routes.manual")

# Statuses that can be reused for a forced manual recovery.
_MANUAL_RECOVER_REUSE_STATUSES = (
    "MITIGATED",
    "RECOVERY_FAILED",
    "MITIGATION_FAILED",
    "MONITORING",
    "OPEN",
    "PREPARED",
    "READY_FOR_MITIGATION",
)


def _parse_required_str(body: dict[str, Any], key: str) -> Optional[str]:
    value = body.get(key)
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _status_from_error(error: str) -> int:
    lowered = (error or "").lower()
    if "lock conflict" in lowered:
        return 409
    return 400


def _client_ip() -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr


def _log_emergency_failure(operator: str, reason: str, *, status_code: int) -> None:
    logger.warning(
        "Emergency shutdown rejected | operator=%s | status=%s | reason=%s | ip=%s",
        operator,
        status_code,
        reason,
        _client_ip(),
    )
    log_audit(
        action="Emergency Shutdown Denied",
        entity_type="device",
        entity_id=operator,
        details={
            "operator": operator,
            "reason": reason,
            "statusCode": status_code,
            "sourceIp": _client_ip(),
            "sessionId": (g.user or {}).get("id"),
            "emergency": True,
        },
    )


@manual_bp.route("/manual/shutdown", methods=["POST"])
@require_auth(roles=["admin", "operator"])
def manual_shutdown():
    try:
        body = request.get_json(silent=True) or {}
        device_id = body.get("deviceId") or body.get("device_id")
        interface = _parse_required_str(body, "interface")
        reason = _parse_required_str(body, "reason")

        if not device_id or not interface or not reason:
            return jsonify({
                "success": False,
                "message": "deviceId, interface, and reason are required",
            }), 400

        if not ObjectId.is_valid(str(device_id)):
            return jsonify({"success": False, "message": "Invalid device ID"}), 400

        now = datetime.now(timezone.utc)
        operator = g.user.get("username") or "admin"
        device_oid = ObjectId(str(device_id))

        existing = find_open_incident(device_oid, interface)
        if existing:
            return jsonify({
                "success": False,
                "message": "A storm incident is already open for this device/interface",
                "incidentId": existing.get("incidentId"),
            }), 409

        safety_res = evaluate_safety(
            device_oid,
            interface,
            probe_ssh=True,
            persist=True,
        )
        if not safety_res.safe:
            return jsonify({
                "success": False,
                "message": "Safety failed; shutdown blocked",
                "reason": safety_res.reason,
            }), 409

        prepare_res = prepare_mitigation(
            device_oid,
            interface,
            probe_ssh=True,
            require_safety=True,
            persist=True,
            safety=safety_res.to_api_dict(),
            incident_metadata={
                "incidentType": "MANUAL",
                "requestedBy": operator,
                "requestedAt": now,
                "reason": reason,
                "action": "SHUTDOWN",
                "triggerType": "MANUAL",
            },
        )
        if not prepare_res.get("ready"):
            return jsonify({
                "success": False,
                "message": "Manual shutdown preparation blocked",
                "reason": prepare_res.get("reason"),
            }), 409

        incident_id = str(prepare_res.get("incidentId") or "").strip()
        if not incident_id:
            return jsonify({
                "success": False,
                "message": "Incident creation failed",
            }), 500

        res = execute_mitigation(
            incident_id,
            "SHUTDOWN",
            operator=operator,
        )
        success = bool(res.get("success"))
        status_code = 200 if success else _status_from_error(res.get("error") or "")

        hist = db.storm_mitigation_history.find_one(
            {"incidentId": incident_id},
            sort=[("timestamp", -1)],
        )
        verification_passed = bool((hist or {}).get("verificationResult", {}).get("success"))
        rollback_performed = bool((hist or {}).get("rollbackPerformed"))

        log_audit(
            action="Manual Shutdown",
            entity_type="incident",
            entity_id=incident_id,
            details={
                "operator": operator,
                "reason": reason,
                "result": "Success" if success else "Failed",
                "verification": "Passed" if verification_passed else "Failed",
                "rollback": "Yes" if rollback_performed else "No",
                "stormMitigationStatus": res.get("status"),
            },
        )

        return jsonify({
            "success": success,
            "status": res.get("status"),
            "incidentId": incident_id,
            "error": res.get("error"),
        }), status_code

    except Exception:  # noqa: BLE001
        return jsonify({
            "success": False,
            "message": "Manual shutdown failed",
        }), 500


@manual_bp.route("/manual/recover", methods=["POST"])
@require_auth(roles=["admin"])
def manual_recover():
    try:
        body = request.get_json(silent=True) or {}
        incident_id = body.get("incidentId") or body.get("incident_id")
        device_id = body.get("deviceId") or body.get("device_id")
        interface = _parse_required_str(body, "interface")
        reason = _parse_required_str(body, "reason")

        if not reason:
            return jsonify({
                "success": False,
                "message": "reason is required",
            }), 400

        operator = g.user.get("username") or "admin"
        now = datetime.now(timezone.utc)
        incident = None

        # Prefer an explicit incident id when still reusable.
        if incident_id:
            candidate = get_incident(str(incident_id).strip())
            if candidate and candidate.get("status") in _MANUAL_RECOVER_REUSE_STATUSES:
                incident = candidate

        # Fall back to latest reusable incident for this device/interface.
        if (
            incident is None
            and device_id
            and interface
            and ObjectId.is_valid(str(device_id))
        ):
            device_oid = ObjectId(str(device_id))
            incident = db.storm_incidents.find_one(
                {
                    "deviceId": device_oid,
                    "interface": interface,
                    "status": {"$in": list(_MANUAL_RECOVER_REUSE_STATUSES)},
                },
                sort=[("updatedAt", -1), ("createdAt", -1)],
            )

        # No reusable incident (e.g. all RESOLVED) — create a fresh MANUAL
        # recovery incident and force no-shutdown immediately.
        if incident is None:
            if not device_id or not interface or not ObjectId.is_valid(str(device_id)):
                return jsonify({
                    "success": False,
                    "message": (
                        "deviceId and interface are required to recover this port "
                        "when no active incident exists"
                    ),
                }), 400

            device_oid = ObjectId(str(device_id))
            device = db.devices.find_one({"_id": device_oid})
            if not device:
                return jsonify({"success": False, "message": "Device not found"}), 404

            incident = create_emergency_incident(
                device_oid,
                interface,
                requested_by=operator,
                requested_at=now,
                reason=reason,
                action="RECOVER",
                incident_type="MANUAL",
                trigger_type="MANUAL",
                approved_by=operator,
                persist=True,
            )
            # Mark as MITIGATED so recovery history/timeline remains coherent.
            db.storm_incidents.update_one(
                {"incidentId": incident["incidentId"]},
                {
                    "$set": {
                        "status": "MITIGATED",
                        "updatedAt": now,
                    },
                    "$push": {
                        "timeline": {
                            "event": "Manual Recovery Prepared",
                            "time": now,
                            "detail": "Created for forced manual port recovery",
                        }
                    },
                },
            )
            incident = get_incident(incident["incidentId"]) or incident

        incident_id = str(incident.get("incidentId") or "").strip()
        if not incident_id:
            return jsonify({"success": False, "message": "Incident not found"}), 404

        # Manual Recover Port: operator-driven — bypass cooldown/safety/storm policy
        # and execute "no shutdown" immediately (still uses locks + SSH + verify).
        res = execute_recovery(
            incident_id,
            force=True,
            operator=operator,
        )

        success = bool(res.get("success"))
        status_code = 200 if success else _status_from_error(res.get("error") or "")
        error_text = res.get("error")

        hist = db.storm_recovery_history.find_one(
            {"incidentId": incident_id},
            sort=[("timestamp", -1)],
        )
        verification_passed = bool((hist or {}).get("verificationResult", {}).get("success"))

        log_audit(
            action="Manual Recovery",
            entity_type="incident",
            entity_id=incident_id,
            details={
                "operator": operator,
                "reason": reason,
                "result": "Success" if success else "Failed",
                "verification": "Passed" if verification_passed else "Failed",
                "rollback": "No",
                "forced": True,
                "stormRecoveryStatus": res.get("status"),
            },
        )

        payload = {
            "success": success,
            "status": res.get("status"),
            "incidentId": incident_id,
            "error": error_text,
            "retryCount": res.get("retryCount"),
        }
        if not success:
            payload["message"] = error_text or "Port recovery failed"
        return jsonify(payload), status_code

    except Exception as error:  # noqa: BLE001
        logger.exception("Manual recovery failed")
        return jsonify({
            "success": False,
            "message": "Manual recovery failed",
            "error": str(error),
        }), 500


@manual_bp.route("/manual/emergency-shutdown", methods=["POST"])
@require_auth(roles=["super-admin"])
@rate_limit(EMERGENCY_SHUTDOWN_LIMITER, prefix="emergency-shutdown")
def manual_emergency_shutdown():
    operator = (g.user or {}).get("username") or "admin"
    try:
        body = request.get_json(silent=True) or {}
        device_id = body.get("deviceId") or body.get("device_id")
        interface = _parse_required_str(body, "interface")
        reason = _parse_required_str(body, "reason")
        confirmation = _parse_required_str(body, "confirmation")

        if not device_id or not interface:
            _log_emergency_failure(operator, "deviceId and interface are required", status_code=400)
            return jsonify({
                "success": False,
                "message": "deviceId, interface, reason, and confirmation are required",
            }), 400

        if confirmation != EMERGENCY_CONFIRMATION:
            _log_emergency_failure(operator, "Invalid confirmation token", status_code=400)
            return jsonify({
                "success": False,
                "message": f'confirmation must be exactly "{EMERGENCY_CONFIRMATION}"',
            }), 400

        if not reason:
            _log_emergency_failure(operator, "Missing reason", status_code=400)
            return jsonify({"success": False, "message": "reason is required"}), 400

        result = execute_emergency_shutdown(
            device_id=str(device_id),
            interface=interface,
            reason=reason,
            operator=operator,
            source_ip=_client_ip(),
            session_id=(g.user or {}).get("id"),
        )

        status_code = 200 if result.get("success") else _status_from_error(result.get("error") or "")
        return jsonify(result), status_code

    except EmergencyShutdownError as exc:
        _log_emergency_failure(operator, exc.message, status_code=exc.status_code)
        return jsonify({"success": False, "message": exc.message}), exc.status_code
    except Exception:  # noqa: BLE001
        logger.exception("Emergency shutdown failed unexpectedly | operator=%s", operator)
        _log_emergency_failure(operator, "Unexpected server error", status_code=500)
        return jsonify({
            "success": False,
            "message": "Emergency shutdown failed",
        }), 500
