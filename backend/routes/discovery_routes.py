from flask import Blueprint, jsonify, request

from services.discovery_service import discover_devices, get_local_network_hint
from utils.auth import require_auth

discovery_bp = Blueprint("discovery", __name__)


@discovery_bp.route("/discovery/network-hint", methods=["GET"])
@require_auth()
def network_hint():
    try:
        hint = get_local_network_hint()

        if hint is None:
            return jsonify({
                "success": False,
                "message": "Could not detect local network",
            }), 404

        return jsonify({
            "success": True,
            "hint": hint,
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to detect local network",
            "error": str(error),
        }), 500


@discovery_bp.route("/discovery/scan-range", methods=["POST"])
@require_auth(roles=["admin"])
def scan_range():
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                "success": False,
                "message": "Request body is required",
            }), 400

        start_ip = data.get("startIP")
        end_ip = data.get("endIP")

        if not start_ip or not end_ip:
            return jsonify({
                "success": False,
                "message": "startIP and endIP are required",
            }), 400

        devices = discover_devices(start_ip, end_ip)

        online = sum(1 for device in devices if device["status"] == "Online")
        offline = sum(1 for device in devices if device["status"] == "Offline")
        newly_saved = sum(1 for device in devices if device["saved"])

        return jsonify({
            "success": True,
            "summary": {
                "totalScanned": len(devices),
                "online": online,
                "offline": offline,
                "newlySaved": newly_saved,
            },
            "devices": devices,
        }), 200

    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error),
        }), 400

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to scan IP range",
            "error": str(error),
        }), 500
