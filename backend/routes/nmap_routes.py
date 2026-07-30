"""
nmap_routes.py
==============
Flask blueprint exposing two Nmap scan endpoints.

Routes
------
POST /api/devices/<device_id>/scan-details
    Trigger an Nmap scan for a single device by its MongoDB _id.
    Only works when the device status is Online.

POST /api/devices/scan-all-details
    Trigger Nmap scans for all currently-online devices concurrently.
    Returns a summary of total / scanned / failed counts.

Design Notes
------------
- All business logic lives in nmap_service.py (SOLID: single responsibility).
- Routes handle only HTTP concerns: request parsing, auth, serialisation.
- Errors from the service layer are surfaced as JSON responses; Flask never
  crashes due to an Nmap failure.
"""

from bson import ObjectId
from flask import Blueprint, jsonify

from config.database import db
from services.nmap_service import scan_all_online_devices, scan_and_update_device
from utils.auth import require_auth
from utils.serializers import serialize_device

nmap_bp = Blueprint("nmap", __name__)


@nmap_bp.route("/devices/<device_id>/scan-details", methods=["POST"])
@require_auth(roles=["operator"])
def scan_device_details(device_id: str):
    """
    Trigger an Nmap detail scan for a single device.

    Workflow
    --------
    1. Validate the device_id is a valid ObjectId.
    2. Fetch the device from MongoDB; 404 if not found.
    3. Confirm the device is Online; 409 if it is offline.
    4. Delegate to scan_and_update_device (nmap_service).
    5. Re-fetch the updated document and return it serialised.

    Returns
    -------
    200  Scan completed (success or partial - check "success" key).
    400  Invalid device ID.
    404  Device not found.
    409  Device is not online; scan skipped.
    500  Unexpected server error.
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

        # Guard: Nmap only makes sense for reachable hosts.
        if device.get("status") != "Online":
            return jsonify({
                "success": False,
                "message": (
                    f"Device is not online (current status: {device.get('status', 'Unknown')}). "
                    "Nmap scan requires an online device."
                ),
            }), 409

        # Delegate all scan logic to the service layer.
        result = scan_and_update_device(device)

        if not result["success"]:
            return jsonify({
                "success": False,
                "message": result.get("error", "Nmap scan failed"),
            }), 500

        # Re-fetch so the response includes the freshly written networkInfo.
        updated_device = db.devices.find_one({"_id": ObjectId(device_id)})
        return jsonify({
            "success": True,
            "message": "Nmap scan completed successfully",
            "data": serialize_device(updated_device),
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to run Nmap scan",
            "error": str(error),
        }), 500


@nmap_bp.route("/devices/scan-all-details", methods=["POST"])
@require_auth(roles=["operator"])
def scan_all_device_details():
    """
    Trigger Nmap detail scans for all currently-online devices.

    Workflow
    --------
    1. Delegate to scan_all_online_devices (nmap_service), which:
       a. Fetches all Online devices.
       b. Scans them concurrently (ThreadPoolExecutor, MAX_SCAN_THREADS).
       c. Updates MongoDB networkInfo for each successful scan.
    2. Return a summary payload.

    Returns
    -------
    200  Summary of the bulk scan (even if some individual scans failed).
    500  Unexpected server error.

    Example response
    ----------------
    {
        "success": true,
        "total": 25,
        "scanned": 23,
        "failed": 2,
        "errors": [
            {"ip": "192.168.1.10", "error": "Host unreachable"},
            ...
        ]
    }
    """
    try:
        summary = scan_all_online_devices()
        return jsonify({
            "success": True,
            "message": (
                f"Nmap bulk scan completed: "
                f"{summary['scanned']}/{summary['total']} device(s) scanned"
            ),
            "total": summary["total"],
            "scanned": summary["scanned"],
            "failed": summary["failed"],
            "errors": summary.get("errors", []),
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to run bulk Nmap scan",
            "error": str(error),
        }), 500
