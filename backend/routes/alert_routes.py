from bson import ObjectId
from utils.api_errors import internal_error_response
from flask import Blueprint, jsonify, request

from config.database import db
from utils.auth import require_auth
from utils.pagination import clamp_page, pagination_payload, parse_pagination
from utils.serializers import format_datetime
from utils.utc import utc_now

alert_bp = Blueprint("alerts", __name__)


def serialize_alert(alert):
    return {
        "_id": str(alert["_id"]),
        "deviceId": str(alert["deviceId"]) if alert.get("deviceId") else None,
        "hostname": alert.get("hostname"),
        "ipAddress": alert.get("ipAddress"),
        "deviceType": alert.get("deviceType"),
        "deviceName": alert.get("deviceName") or alert.get("hostname"),
        "status": alert.get("status"),
        "message": alert.get("message"),
        "title": alert.get("title"),
        "scanType": alert.get("scanType"),
        "alertType": alert.get("alertType"),
        "category": alert.get("category") or alert.get("alertType") or alert.get("scanType"),
        "severity": alert.get("severity"),
        "interface": alert.get("interface"),
        "incidentId": alert.get("incidentId"),
        "riskScore": alert.get("riskScore"),
        "action": alert.get("action"),
        "generatedBy": alert.get("generatedBy"),
        "recoveryDuration": alert.get("recoveryDuration"),
        "emailSent": alert.get("emailSent", False),
        "acknowledged": alert.get("acknowledged", False),
        "dismissed": alert.get("dismissed", False),
        # Phase 4 — automatic recovery fields (additive; null for legacy alerts).
        "resolved": bool(alert.get("resolved", False)),
        "resolvedAt": format_datetime(alert.get("resolvedAt")),
        "resolvedBy": alert.get("resolvedBy"),
        "resolvedReason": alert.get("resolvedReason"),
        "acknowledgedAt": format_datetime(alert.get("acknowledgedAt")),
        "dismissedAt": format_datetime(alert.get("dismissedAt")),
        "createdAt": format_datetime(alert.get("createdAt")),
    }


@alert_bp.route("/alerts", methods=["GET"])
@require_auth()
def list_alerts():
    try:
        page, limit = parse_pagination(default_limit=25, max_limit=100)
        query = {}

        status_filter = (request.args.get("status") or "").strip().lower()
        if status_filter == "active":
            query["dismissed"] = {"$ne": True}
            query["acknowledged"] = {"$ne": True}
            # Phase 4 — recovered alerts leave the active list (backward compatible).
            query["resolved"] = {"$ne": True}
        elif status_filter == "acknowledged":
            query["acknowledged"] = True
        elif status_filter == "dismissed":
            query["dismissed"] = True
        elif status_filter == "resolved":
            query["resolved"] = True

        total = db.alerts.count_documents(query)
        page, skip, total_pages = clamp_page(page, total, limit)

        alerts = [
            serialize_alert(item)
            for item in (
                db.alerts.find(query)
                .sort("createdAt", -1)
                .skip(skip)
                .limit(limit)
            )
        ]

        return jsonify({
            "success": True,
            "count": len(alerts),
            "data": alerts,
            **pagination_payload(total, page, limit, total_pages),
        }), 200

    except Exception as error:
        return internal_error_response(error, message="Failed to get alerts")


@alert_bp.route("/alerts/<alert_id>/acknowledge", methods=["POST"])
@require_auth(roles=["operator"])
def acknowledge_alert(alert_id):
    try:
        if not ObjectId.is_valid(alert_id):
            return jsonify({"success": False, "message": "Invalid alert ID"}), 400

        result = db.alerts.update_one(
            {"_id": ObjectId(alert_id)},
            {
                "$set": {
                    "acknowledged": True,
                    "acknowledgedAt": utc_now(),
                }
            },
        )

        if result.matched_count == 0:
            return jsonify({"success": False, "message": "Alert not found"}), 404

        alert = db.alerts.find_one({"_id": ObjectId(alert_id)})
        return jsonify({
            "success": True,
            "message": "Alert acknowledged",
            "data": serialize_alert(alert),
        }), 200

    except Exception as error:
        return internal_error_response(error, message="Failed to acknowledge alert")


@alert_bp.route("/alerts/<alert_id>/dismiss", methods=["POST"])
@require_auth(roles=["operator"])
def dismiss_alert(alert_id):
    try:
        if not ObjectId.is_valid(alert_id):
            return jsonify({"success": False, "message": "Invalid alert ID"}), 400

        result = db.alerts.update_one(
            {"_id": ObjectId(alert_id)},
            {
                "$set": {
                    "dismissed": True,
                    "dismissedAt": utc_now(),
                }
            },
        )

        if result.matched_count == 0:
            return jsonify({"success": False, "message": "Alert not found"}), 404

        alert = db.alerts.find_one({"_id": ObjectId(alert_id)})
        return jsonify({
            "success": True,
            "message": "Alert dismissed",
            "data": serialize_alert(alert),
        }), 200

    except Exception as error:
        return internal_error_response(error, message="Failed to dismiss alert")
