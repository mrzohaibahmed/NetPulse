"""
interface_routes.py
===================
REST API for discovered switch interfaces and interface statistics.

Routes
------
GET  /api/interfaces
GET  /api/interfaces/<device_id>
GET  /api/interfaces/<device_id>/stats
POST /api/interfaces/<device_id>/<interface>/monitoring
POST /api/interfaces/<device_id>/<interface>/manual-shutdown
POST /api/interfaces/<device_id>/<interface>/manual-recover
GET  /api/interfaces/<device_id>/<interface>/history
"""

from datetime import datetime, timezone
from urllib.parse import unquote

from bson import ObjectId
from flask import Blueprint, g, jsonify, request

from config.database import db
from services.interface_collection.collector import (
    discover_all_switch_interfaces,
    discover_device_interfaces,
    get_interfaces,
)
from services.interface_collection.monitoring_state import (
    MONITORING_MODE_AUTO,
    MONITORING_MODE_DISABLED_BY_USER,
    normalize_monitoring_mode,
    set_interface_monitoring_mode,
)
from services.interface_collection.stats_collector import (
    collect_all_interface_stats,
    collect_device_interface_stats,
    get_interface_stats_history,
    get_latest_device_stats,
)
from services.audit_service import log_audit
from services.storm.incident import create_manual_incident, get_incident
from services.storm.mitigation import execute_mitigation
from services.storm.recovery import execute_manual_recovery
from utils.auth import require_auth
from utils.pagination import clamp_page, pagination_payload, parse_pagination
from utils.serializers import serialize_interface, serialize_interface_stat

interface_bp = Blueprint("interfaces", __name__)


def _build_list_filters():
    return {
        "search": (request.args.get("q") or "").strip() or None,
        "admin_status": (request.args.get("adminStatus") or "").strip() or None,
        "oper_status": (request.args.get("operStatus") or "").strip() or None,
        "mode": (request.args.get("mode") or "").strip() or None,
    }


def _parse_iso_date(value, end_of_day=False):
    if not value:
        return None
    text = value.strip()
    try:
        if len(text) == 10:
            dt = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59)
            return dt
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Invalid date: {value}") from error


def _bool_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return False


def _manual_action_response(
    *,
    success: bool,
    action: str,
    device_id: str,
    interface: str,
    incident_id: str | None,
    incident_status: str | None,
    message: str,
    extra: dict | None = None,
):
    payload = {
        "success": success,
        "action": action,
        "deviceId": device_id,
        "interface": interface,
        "incidentId": incident_id,
        "incidentStatus": incident_status,
        "status": incident_status,
        "message": message,
    }
    if extra:
        payload.update(extra)
    return payload


@interface_bp.route("/interfaces", methods=["GET"])
@require_auth()
def list_interfaces():
    """Return a paginated list of all discovered interfaces."""
    try:
        page, limit = parse_pagination(default_limit=50, max_limit=500)
        filters = _build_list_filters()

        device_id = (request.args.get("deviceId") or "").strip()
        oid = None
        if device_id:
            if not ObjectId.is_valid(device_id):
                return jsonify({
                    "success": False,
                    "message": "Invalid device ID",
                }), 400
            oid = ObjectId(device_id)

        _, total = get_interfaces(oid, skip=0, limit=1, **filters)
        page, skip, total_pages = clamp_page(page, total, limit)

        interfaces, total = get_interfaces(
            oid,
            skip=skip,
            limit=limit,
            **filters,
        )

        return jsonify({
            "success": True,
            "count": len(interfaces),
            "data": [serialize_interface(item) for item in interfaces],
            **pagination_payload(total, page, limit, total_pages),
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to list interfaces",
            "error": str(error),
        }), 500


@interface_bp.route("/interfaces/discover-all", methods=["POST"])
@require_auth(roles=["admin"])
def discover_all_interfaces():
    """Trigger interface discovery for all eligible online switches."""
    try:
        summary = discover_all_switch_interfaces()
        return jsonify({
            "success": True,
            "message": (
                f"Interface discovery completed: "
                f"{summary['discovered_devices']}/{summary['total']} device(s), "
                f"{summary['interface_count']} interface(s)"
            ),
            "total": summary["total"],
            "discoveredDevices": summary["discovered_devices"],
            "failed": summary["failed"],
            "interfaceCount": summary["interface_count"],
            "errors": summary.get("errors", []),
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to run bulk interface discovery",
            "error": str(error),
        }), 500


@interface_bp.route("/interfaces/stats/collect-all", methods=["POST"])
@require_auth(roles=["admin"])
def collect_all_stats():
    """Manually trigger interface stats collection for all eligible devices."""
    try:
        summary = collect_all_interface_stats()
        return jsonify({
            "success": True,
            "message": (
                f"Interface stats collection completed: "
                f"{summary['succeeded']}/{summary['total']} device(s), "
                f"{summary['samples']} sample(s)"
            ),
            "total": summary["total"],
            "succeeded": summary["succeeded"],
            "failed": summary["failed"],
            "samples": summary["samples"],
            "errors": summary.get("errors", []),
        }), 200
    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to collect interface statistics",
            "error": str(error),
        }), 500


@interface_bp.route("/interfaces/discover/<device_id>", methods=["POST"])
@require_auth(roles=["admin"])
def discover_interfaces_for_device(device_id: str):
    """Trigger SSH interface discovery for a single online device."""
    try:
        if not ObjectId.is_valid(device_id):
            return jsonify({
                "success": False,
                "message": "Invalid device ID",
            }), 400

        device = db.devices.find_one({"_id": ObjectId(device_id)})
        if not device:
            return jsonify({
                "success": False,
                "message": "Device not found",
            }), 404

        if device.get("status") != "Online":
            return jsonify({
                "success": False,
                "message": (
                    f"Device is not online (current status: "
                    f"{device.get('status', 'Unknown')}). "
                    "Interface discovery requires an online device."
                ),
            }), 409

        result = discover_device_interfaces(device)

        if not result["success"]:
            return jsonify({
                "success": False,
                "message": result.get("error") or "Interface discovery failed",
                "data": result,
            }), 500

        interfaces, total = get_interfaces(
            ObjectId(device_id),
            skip=0,
            limit=1000,
        )

        return jsonify({
            "success": True,
            "message": (
                f"Discovered {result['discovered']} interface(s) "
                f"on {device.get('hostname') or device.get('ipAddress')}"
            ),
            "summary": result,
            "count": total,
            "data": [serialize_interface(item) for item in interfaces],
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to discover interfaces",
            "error": str(error),
        }), 500


@interface_bp.route("/interfaces/<device_id>/stats", methods=["GET"])
@require_auth()
def get_device_interface_stats(device_id: str):
    """Return the latest statistics sample for every interface on a device."""
    try:
        if not ObjectId.is_valid(device_id):
            return jsonify({
                "success": False,
                "message": "Invalid device ID",
            }), 400

        oid = ObjectId(device_id)
        device = db.devices.find_one({"_id": oid})
        if not device:
            return jsonify({
                "success": False,
                "message": "Device not found",
            }), 404

        stats = get_latest_device_stats(oid)
        return jsonify({
            "success": True,
            "deviceId": device_id,
            "hostname": device.get("hostname"),
            "ipAddress": device.get("ipAddress"),
            "count": len(stats),
            "data": [serialize_interface_stat(item) for item in stats],
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to get interface statistics",
            "error": str(error),
        }), 500


@interface_bp.route("/interfaces/<device_id>/stats/collect", methods=["POST"])
@require_auth(roles=["admin"])
def collect_device_stats(device_id: str):
    """Manually poll interface statistics for one device."""
    try:
        if not ObjectId.is_valid(device_id):
            return jsonify({
                "success": False,
                "message": "Invalid device ID",
            }), 400

        device = db.devices.find_one({"_id": ObjectId(device_id)})
        if not device:
            return jsonify({
                "success": False,
                "message": "Device not found",
            }), 404

        if device.get("status") != "Online":
            return jsonify({
                "success": False,
                "message": (
                    f"Device is not online (current status: "
                    f"{device.get('status', 'Unknown')}). "
                    "Interface stats require an online device."
                ),
            }), 409

        result = collect_device_interface_stats(device)
        if not result["success"]:
            return jsonify({
                "success": False,
                "message": result.get("error") or "Stats collection failed",
                "data": result,
            }), 500

        stats = get_latest_device_stats(ObjectId(device_id))
        return jsonify({
            "success": True,
            "message": (
                f"Collected {result['collected']} interface stat sample(s) "
                f"via {result.get('method')}"
            ),
            "summary": result,
            "count": len(stats),
            "data": [serialize_interface_stat(item) for item in stats],
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to collect interface statistics",
            "error": str(error),
        }), 500


@interface_bp.route(
    "/interfaces/<device_id>/<path:interface_name>/monitoring",
    methods=["POST"],
)
@require_auth(roles=["operator"])
def set_interface_monitoring(device_id: str, interface_name: str):
    """
    Set administrator monitoring intent for one interface.

    Body (either form accepted)::

        { "monitoringMode": "AUTO" | "DISABLED_BY_USER" }
        { "enabled": true | false }
    """
    try:
        body = request.get_json(silent=True) or {}
        if not ObjectId.is_valid(device_id):
            return jsonify({
                "success": False,
                "message": "Invalid device ID",
            }), 400

        name = unquote(interface_name).strip()
        if not name:
            return jsonify({
                "success": False,
                "message": "Interface name is required",
            }), 400

        mode = normalize_monitoring_mode(body.get("monitoringMode"))
        if mode is None and "enabled" in body:
            mode = (
                MONITORING_MODE_AUTO
                if _bool_flag(body.get("enabled"))
                else MONITORING_MODE_DISABLED_BY_USER
            )
        if mode is None:
            return jsonify({
                "success": False,
                "message": (
                    "monitoringMode must be AUTO or DISABLED_BY_USER "
                    "(or pass enabled=true/false)"
                ),
            }), 400

        updated = set_interface_monitoring_mode(device_id, name, mode)
        if not updated:
            return jsonify({
                "success": False,
                "message": "Interface not found",
            }), 404

        username = (getattr(g, "user", {}) or {}).get("username") or "SYSTEM"
        log_audit(
            action="interface_monitoring_update",
            entity_type="interface",
            entity_id=f"{device_id}:{name}",
            details={
                "deviceId": device_id,
                "interface": name,
                "monitoringMode": mode,
                "monitoringEnabled": updated.get("monitoringEnabled"),
                "requestedBy": username,
            },
        )

        return jsonify({
            "success": True,
            "message": "Interface monitoring preference updated",
            "data": serialize_interface(updated),
        }), 200

    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error),
        }), 400
    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to update interface monitoring",
            "error": str(error),
        }), 500


@interface_bp.route(
    "/interfaces/<device_id>/<path:interface_name>/manual-shutdown",
    methods=["POST"],
)
@require_auth(roles=["operator"])
def manual_shutdown_interface(device_id: str, interface_name: str):
    """Create a MANUAL incident and execute shutdown without storm pipeline gates."""
    try:
        body = request.get_json(silent=True) or {}
        if not _bool_flag(body.get("confirm")):
            return jsonify({
                "success": False,
                "message": "confirm=true is required for manual shutdown",
            }), 400

        if not ObjectId.is_valid(device_id):
            return jsonify({
                "success": False,
                "message": "Invalid device ID",
            }), 400

        oid = ObjectId(device_id)
        device = db.devices.find_one({"_id": oid})
        if not device:
            return jsonify({
                "success": False,
                "message": "Device not found",
            }), 404

        name = unquote(interface_name).strip()
        if not name:
            return jsonify({
                "success": False,
                "message": "Interface name is required",
            }), 400

        username = (getattr(g, "user", {}) or {}).get("username") or "SYSTEM"
        role = (getattr(g, "user", {}) or {}).get("role") or "viewer"
        reason = (body.get("reason") or "").strip() or None

        incident = create_manual_incident(
            device_id=oid,
            interface=name,
            hostname=device.get("hostname"),
            ip_address=device.get("ipAddress"),
            requested_by=username,
            action="MANUAL_SHUTDOWN",
            reason=reason,
            persist=True,
            force_new=True,
        )

        res = execute_mitigation(
            str(incident["incidentId"]),
            "SHUTDOWN",
            operator=username,
            audit_context={"reason": reason},
        )

        log_audit(
            action="manual_shutdown",
            entity_type="incident",
            entity_id=incident.get("incidentId"),
            details={
                "deviceId": device_id,
                "interface": name,
                "role": role,
                "incidentId": incident.get("incidentId"),
                "reason": reason,
            },
        )

        status_code = 200 if res.get("success") else 400
        return jsonify(_manual_action_response(
            success=bool(res.get("success")),
            action="manual_shutdown",
            device_id=device_id,
            interface=name,
            incident_id=incident.get("incidentId"),
            incident_status=res.get("status"),
            message=(
                "Manual shutdown executed successfully"
                if res.get("success")
                else (res.get("error") or "Manual shutdown failed")
            ),
            extra={
                "incidentType": incident.get("incidentType"),
                "commandsExecuted": res.get("commandsExecuted") or [],
            },
        )), status_code

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to execute manual shutdown",
            "error": str(error),
        }), 500


@interface_bp.route(
    "/interfaces/<device_id>/<path:interface_name>/manual-recover",
    methods=["POST"],
)
@require_auth(roles=["operator"])
def manual_recover_interface(device_id: str, interface_name: str):
    """Execute recovery for a currently mitigated incident on one interface."""
    try:
        body = request.get_json(silent=True) or {}
        if not _bool_flag(body.get("confirm")):
            return jsonify({
                "success": False,
                "message": "confirm=true is required for manual recovery",
            }), 400

        if not ObjectId.is_valid(device_id):
            return jsonify({
                "success": False,
                "message": "Invalid device ID",
            }), 400

        oid = ObjectId(device_id)
        device = db.devices.find_one({"_id": oid})
        if not device:
            return jsonify({
                "success": False,
                "message": "Device not found",
            }), 404

        name = unquote(interface_name).strip()
        if not name:
            return jsonify({
                "success": False,
                "message": "Interface name is required",
            }), 400

        incident_id = (body.get("incidentId") or body.get("incident_id") or "").strip()
        incident = None
        if incident_id:
            incident = get_incident(incident_id)
            if not incident:
                return jsonify({
                    "success": False,
                    "message": "Incident not found",
                }), 404
        else:
            incident = db.storm_incidents.find_one(
                {"deviceId": oid, "interface": name},
                sort=[("createdAt", -1)],
            )
            if not incident:
                return jsonify({
                    "success": False,
                    "message": "No incident found for this interface",
                }), 404

        if incident.get("deviceId") != oid or str(incident.get("interface") or "").strip() != name:
            return jsonify({
                "success": False,
                "message": "Incident does not match the requested device/interface",
            }), 400

        if incident.get("status") != "MITIGATED":
            return jsonify(_manual_action_response(
                success=False,
                action="manual_recover",
                device_id=device_id,
                interface=name,
                incident_id=incident.get("incidentId"),
                incident_status=incident.get("status"),
                message="Manual recovery is only allowed for incidents in MITIGATED status",
                extra={"allowedIncidentStatuses": ["MITIGATED"]},
            )), 400

        username = (getattr(g, "user", {}) or {}).get("username") or "SYSTEM"
        role = (getattr(g, "user", {}) or {}).get("role") or "viewer"

        # Operator override: bypass Recovery Safety (R1–R8); execution checks only.
        res = execute_manual_recovery(
            str(incident["incidentId"]),
            operator=username,
        )

        log_audit(
            action="manual_recover",
            entity_type="incident",
            entity_id=incident.get("incidentId"),
            details={
                "deviceId": device_id,
                "interface": name,
                "role": role,
                "incidentId": incident.get("incidentId"),
                "recoveryType": "MANUAL",
                "trigger": "OPERATOR",
                "safetyRules": "BYPASSED",
                "status": res.get("status"),
            },
        )

        status_code = 200 if res.get("success") else 400
        return jsonify(_manual_action_response(
            success=bool(res.get("success")),
            action="manual_recover",
            device_id=device_id,
            interface=name,
            incident_id=incident.get("incidentId"),
            incident_status=res.get("status"),
            message=(
                "Manual recovery executed successfully"
                if res.get("success")
                else (res.get("error") or "Manual recovery failed")
            ),
            extra={
                "retryCount": res.get("retryCount"),
                "recoveryType": "MANUAL",
                "safetyRules": "BYPASSED",
            },
        )), status_code

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to execute manual recovery",
            "error": str(error),
        }), 500


@interface_bp.route(
    "/interfaces/<device_id>/<path:interface_name>/history",
    methods=["GET"],
)
@require_auth()
def get_interface_history(device_id: str, interface_name: str):
    """
    Return historical statistics for one interface.

    ``interface_name`` accepts slashes (e.g. Gi1/0/1) via the path converter.
    Query params: page, limit, startDate, endDate.
    """
    try:
        if not ObjectId.is_valid(device_id):
            return jsonify({
                "success": False,
                "message": "Invalid device ID",
            }), 400

        oid = ObjectId(device_id)
        device = db.devices.find_one({"_id": oid})
        if not device:
            return jsonify({
                "success": False,
                "message": "Device not found",
            }), 404

        name = unquote(interface_name).strip()
        if not name:
            return jsonify({
                "success": False,
                "message": "Interface name is required",
            }), 400

        try:
            start = _parse_iso_date(request.args.get("startDate"))
            end = _parse_iso_date(request.args.get("endDate"), end_of_day=True)
        except ValueError as error:
            return jsonify({
                "success": False,
                "message": str(error),
            }), 400

        page, limit = parse_pagination(default_limit=100, max_limit=1000)

        _, total = get_interface_stats_history(
            oid,
            name,
            skip=0,
            limit=1,
            start=start,
            end=end,
        )
        page, skip, total_pages = clamp_page(page, total, limit)

        history, total = get_interface_stats_history(
            oid,
            name,
            skip=skip,
            limit=limit,
            start=start,
            end=end,
        )

        return jsonify({
            "success": True,
            "deviceId": device_id,
            "interfaceName": name,
            "hostname": device.get("hostname"),
            "ipAddress": device.get("ipAddress"),
            "count": len(history),
            "data": [serialize_interface_stat(item) for item in history],
            **pagination_payload(total, page, limit, total_pages),
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to get interface history",
            "error": str(error),
        }), 500


@interface_bp.route("/interfaces/<device_id>", methods=["GET"])
@require_auth()
def list_device_interfaces(device_id: str):
    """Return interfaces belonging to a specific device."""
    try:
        if not ObjectId.is_valid(device_id):
            return jsonify({
                "success": False,
                "message": "Invalid device ID",
            }), 400

        oid = ObjectId(device_id)
        device = db.devices.find_one({"_id": oid})
        if not device:
            return jsonify({
                "success": False,
                "message": "Device not found",
            }), 404

        page, limit = parse_pagination(default_limit=100, max_limit=1000)
        filters = _build_list_filters()

        _, total = get_interfaces(oid, skip=0, limit=1, **filters)
        page, skip, total_pages = clamp_page(page, total, limit)

        interfaces, total = get_interfaces(
            oid,
            skip=skip,
            limit=limit,
            **filters,
        )

        return jsonify({
            "success": True,
            "deviceId": device_id,
            "hostname": device.get("hostname"),
            "ipAddress": device.get("ipAddress"),
            "count": len(interfaces),
            "data": [serialize_interface(item) for item in interfaces],
            **pagination_payload(total, page, limit, total_pages),
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to get device interfaces",
            "error": str(error),
        }), 500
