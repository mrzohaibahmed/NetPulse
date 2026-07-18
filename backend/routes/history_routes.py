from datetime import datetime, timezone

from bson import ObjectId
from flask import Blueprint, jsonify, request

from config.database import db
from routes.report_routes import compute_uptime
from utils.auth import require_auth
from utils.pagination import clamp_page, pagination_payload, parse_pagination
from utils.serializers import format_datetime, get_device_type, serialize_network_info

history_bp = Blueprint("history", __name__)


def serialize_ping_history(item):
    return {
        "_id": str(item["_id"]),
        "deviceId": str(item["deviceId"]),
        "hostname": item.get("hostname"),
        "ipAddress": item.get("ipAddress"),
        "status": item.get("status"),
        "responseTime": item.get("responseTime"),
        "scanType": item.get("scanType", "Manual"),
        "timestamp": format_datetime(item.get("timestamp")),
    }


def _parse_date(value, end_of_day=False):
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


def build_history_filter():
    import re

    query = {}

    status = (request.args.get("status") or "").strip()
    if status and status.lower() != "all":
        query["status"] = status

    scan_type = (request.args.get("scanType") or "").strip()
    if scan_type and scan_type.lower() != "all":
        query["scanType"] = scan_type

    device_id = (request.args.get("deviceId") or "").strip()
    device_type = (request.args.get("deviceType") or "").strip()

    if device_type and device_type.lower() != "all":
        type_ids = [
            d["_id"]
            for d in db.devices.find({
                "$or": [{"deviceType": device_type}, {"type": device_type}]
            })
        ]
        if device_id and ObjectId.is_valid(device_id):
            oid = ObjectId(device_id)
            query["deviceId"] = oid if oid in type_ids else {"$in": []}
        else:
            query["deviceId"] = {"$in": type_ids}
    elif device_id and ObjectId.is_valid(device_id):
        query["deviceId"] = ObjectId(device_id)

    start = _parse_date(request.args.get("startDate"))
    end = _parse_date(request.args.get("endDate"), end_of_day=True)
    if start or end:
        query["timestamp"] = {}
        if start:
            query["timestamp"]["$gte"] = start
        if end:
            query["timestamp"]["$lte"] = end

    search = (request.args.get("q") or "").strip()
    if search:
        pattern = re.compile(re.escape(search), re.IGNORECASE)
        query["$or"] = [
            {"hostname": pattern},
            {"ipAddress": pattern},
        ]

    return query


@history_bp.route("/history", methods=["GET"])
@require_auth()
def get_ping_history():
    try:
        page, limit = parse_pagination(default_limit=25, max_limit=100)
        filters = build_history_filter()
        total = db.pingHistory.count_documents(filters)
        page, skip, total_pages = clamp_page(page, total, limit)

        history = [
            serialize_ping_history(item)
            for item in (
                db.pingHistory.find(filters)
                .sort("timestamp", -1)
                .skip(skip)
                .limit(limit)
            )
        ]

        return jsonify({
            "success": True,
            "count": len(history),
            "data": history,
            **pagination_payload(total, page, limit, total_pages),
        }), 200

    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to get ping history",
            "error": str(error),
        }), 500


@history_bp.route("/devices/<device_id>/history", methods=["GET"])
@require_auth()
def get_device_history(device_id):
    try:
        if not ObjectId.is_valid(device_id):
            return jsonify({"success": False, "message": "Invalid device ID"}), 400

        device = db.devices.find_one({"_id": ObjectId(device_id)})
        if not device:
            return jsonify({"success": False, "message": "Device not found"}), 404

        start = _parse_date(request.args.get("startDate"))
        end = _parse_date(request.args.get("endDate"), end_of_day=True)
        try:
            limit = min(int(request.args.get("limit", 200)), 1000)
        except (TypeError, ValueError):
            limit = 200

        match = {"deviceId": ObjectId(device_id)}
        if start or end:
            match["timestamp"] = {}
            if start:
                match["timestamp"]["$gte"] = start
            if end:
                match["timestamp"]["$lte"] = end

        history = [
            serialize_ping_history(item)
            for item in (
                db.pingHistory.find(match)
                .sort("timestamp", 1)
                .limit(limit)
            )
        ]

        # Response-time trend (successful pings only)
        trend = [
            {
                "timestamp": row["timestamp"],
                "responseTime": row["responseTime"],
                "status": row["status"],
            }
            for row in history
            if row.get("responseTime") is not None
        ]

        uptime = compute_uptime(ObjectId(device_id), start, end)

        return jsonify({
            "success": True,
            "device": {
                "_id": str(device["_id"]),
                "hostname": device.get("hostname"),
                "ipAddress": device.get("ipAddress"),
                "deviceType": get_device_type(device),
                "status": device.get("status"),
                "critical": device.get("critical", False),
                "monitor": device.get("monitor", True),
                "lastSeen": format_datetime(device.get("lastSeen")),
                "responseTime": device.get("responseTime"),
                "consecutiveFailures": device.get("consecutiveFailures", 0),
                "networkInfo": serialize_network_info(device.get("networkInfo")),
            },
            "uptime": uptime,
            "history": history,
            "responseTimeTrend": trend,
            "count": len(history),
        }), 200

    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to get device history",
            "error": str(error),
        }), 500
