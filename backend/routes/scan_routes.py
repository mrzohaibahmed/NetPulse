from utils.api_errors import internal_error_response
from flask import Blueprint, jsonify

from bson import ObjectId

from config.database import db
from services.monitor_service import apply_ping_result, manual_ping_all_devices
from services.ping_service import ping_device
from utils.auth import require_auth
from utils.serializers import serialize_device

scan_bp = Blueprint("scan", __name__)


@scan_bp.route("/devices/ping-all", methods=["POST"])
@require_auth(roles=["operator"])
def ping_all_devices():
    """
    Manually ping every device in inventory (bounded concurrency).

    Same Manual history / alert path as single-device /scan.
    """
    try:
        summary = manual_ping_all_devices()
        total = summary.get("total", 0)
        online = summary.get("online", 0)
        failed = summary.get("failed", 0)
        return jsonify({
            "success": True,
            "message": (
                f"Bulk ping completed: {online}/{total} online"
                + (f", {failed} unreachable" if failed else "")
            ),
            "total": total,
            "online": online,
            "failed": failed,
            "errors": summary.get("errors", []),
        }), 200
    except Exception as error:
        return internal_error_response(error, message="Failed to ping all devices")


@scan_bp.route("/devices/<device_id>/scan", methods=["POST"])
@require_auth(roles=["operator"])
def scan_device(device_id):
    """
    Manual ping — shares apply_ping_result with the scheduler (Phase 13).

    Same atomic failure counter, history, alert create/resolve semantics.
    """
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

        result = ping_device(
            device["ipAddress"],
            critical=bool(device.get("critical")),
            device=device,
        )
        # Manual scans never use partition suppression — operator intent wins.
        apply_ping_result(device, result, scan_type="Manual")

        updated_device = db.devices.find_one({"_id": ObjectId(device_id)})

        return jsonify({
            "success": result["success"],
            "message": result["message"],
            "data": serialize_device(updated_device),
        }), 200

    except Exception as error:
        return internal_error_response(error, message="Failed to scan device")
