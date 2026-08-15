from utils.api_errors import internal_error_response
from flask import Blueprint, jsonify, request

from services.audit_service import log_audit
from services.isp_monitor_service import scan_isp_connection
from services.isp_service import (
    create_isp_record,
    delete_isp_record,
    get_isp_connection,
    list_isp_connections,
    update_isp_record,
)
from utils.auth import require_auth
from utils.serializers import serialize_isp_connection

isp_bp = Blueprint("isps", __name__)


@isp_bp.route("/isps", methods=["GET"])
@require_auth()
def list_isps():
    try:
        isps = list_isp_connections()
        return jsonify({
            "success": True,
            "count": len(isps),
            "data": [serialize_isp_connection(isp) for isp in isps],
        }), 200
    except Exception as error:  # noqa: BLE001
        return internal_error_response(error, message="Failed to list ISP connections")


@isp_bp.route("/isps/<isp_id>", methods=["GET"])
@require_auth()
def get_isp(isp_id):
    try:
        isp = get_isp_connection(isp_id)
        if isp is None:
            return jsonify({
                "success": False,
                "message": "ISP connection not found",
            }), 404
        return jsonify({
            "success": True,
            "data": serialize_isp_connection(isp),
        }), 200
    except Exception as error:  # noqa: BLE001
        return internal_error_response(error, message="Failed to get ISP connection")


@isp_bp.route("/isps", methods=["POST"])
@require_auth(roles=["admin"])
def create_isp():
    try:
        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({
                "success": False,
                "message": "name is required",
            }), 400

        try:
            created = create_isp_record(
                name=name,
                target=data.get("target") or "",
                monitor=bool(data.get("monitor", False)),
            )
        except ValueError as error:
            return jsonify({
                "success": False,
                "message": str(error),
            }), 400

        log_audit(
            action="isp_created",
            entity_type="isp_connection",
            entity_id=created["_id"],
            details={"name": created["name"], "target": created.get("target")},
        )

        return jsonify({
            "success": True,
            "message": "ISP connection created",
            "data": serialize_isp_connection(created),
        }), 201
    except Exception as error:  # noqa: BLE001
        return internal_error_response(error, message="Failed to create ISP connection")


@isp_bp.route("/isps/<isp_id>", methods=["PUT"])
@require_auth(roles=["admin"])
def update_isp(isp_id):
    try:
        data = request.get_json() or {}
        if not data:
            return jsonify({
                "success": False,
                "message": "Request body is required",
            }), 400

        try:
            updated = update_isp_record(
                isp_id,
                name=data["name"] if "name" in data else None,
                target=data["target"] if "target" in data else None,
                monitor=data["monitor"] if "monitor" in data else None,
            )
        except ValueError as error:
            return jsonify({
                "success": False,
                "message": str(error),
            }), 400

        if updated is None:
            return jsonify({
                "success": False,
                "message": "ISP connection not found",
            }), 404

        log_audit(
            action="isp_updated",
            entity_type="isp_connection",
            entity_id=isp_id,
            details={
                "name": updated.get("name"),
                "target": updated.get("target"),
                "monitor": updated.get("monitor"),
            },
        )

        return jsonify({
            "success": True,
            "message": "ISP connection updated",
            "data": serialize_isp_connection(updated),
        }), 200
    except Exception as error:  # noqa: BLE001
        return internal_error_response(error, message="Failed to update ISP connection")


@isp_bp.route("/isps/<isp_id>", methods=["DELETE"])
@require_auth(roles=["admin"])
def delete_isp(isp_id):
    try:
        existing = get_isp_connection(isp_id)
        if existing is None:
            return jsonify({
                "success": False,
                "message": "ISP connection not found",
            }), 404

        delete_isp_record(isp_id)
        log_audit(
            action="isp_deleted",
            entity_type="isp_connection",
            entity_id=isp_id,
            details={"name": existing.get("name")},
        )

        return jsonify({
            "success": True,
            "message": "ISP connection deleted",
        }), 200
    except Exception as error:  # noqa: BLE001
        return internal_error_response(error, message="Failed to delete ISP connection")


@isp_bp.route("/isps/<isp_id>/scan", methods=["POST"])
@require_auth(roles=["operator"])
def manual_scan_isp(isp_id):
    try:
        isp = get_isp_connection(isp_id)
        if isp is None:
            return jsonify({
                "success": False,
                "message": "ISP connection not found",
            }), 404

        if not (isp.get("target") or "").strip():
            return jsonify({
                "success": False,
                "message": "ISP target is not configured",
            }), 400

        result = scan_isp_connection(isp, scan_type="Manual")
        updated = get_isp_connection(isp_id)

        return jsonify({
            "success": bool(result.get("success")),
            "message": result.get("message", "Scan complete"),
            "data": serialize_isp_connection(updated),
        }), 200
    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error),
        }), 400
    except Exception as error:  # noqa: BLE001
        return internal_error_response(error, message="Failed to scan ISP connection")
