"""
interface_routes.py
===================
REST API for discovered switch interfaces and interface statistics.

Routes
------
GET  /api/interfaces
GET  /api/interfaces/<device_id>
GET  /api/interfaces/<device_id>/stats
GET  /api/interfaces/<device_id>/<interface>/history
POST /api/interfaces/discover/<device_id>
POST /api/interfaces/discover-all
POST /api/interfaces/<device_id>/stats/collect
POST /api/interfaces/stats/collect-all
"""

from datetime import datetime, timezone
from urllib.parse import unquote

from bson import ObjectId
from flask import Blueprint, jsonify, request

from config.database import db
from services.interface_collection.collector import (
    discover_all_switch_interfaces,
    discover_device_interfaces,
    get_interfaces,
)
from services.interface_collection.stats_collector import (
    collect_all_interface_stats,
    collect_device_interface_stats,
    get_interface_stats_history,
    get_latest_device_stats,
)
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
