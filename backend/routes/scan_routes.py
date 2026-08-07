from flask import Blueprint, jsonify

from bson import ObjectId

from config.database import db
from services.monitor_service import apply_ping_result
from services.ping_service import ping_device
from utils.auth import require_auth
from utils.serializers import serialize_device

scan_bp = Blueprint("scan", __name__)


@scan_bp.route("/devices/<device_id>/scan", methods=["POST"])
@require_auth()
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
        return jsonify({
            "success": False,
            "message": "Failed to scan device",
            "error": str(error),
        }), 500
