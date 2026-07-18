from flask import Blueprint, jsonify, request

from scheduler import reschedule_monitor_job
from services.audit_service import log_audit
from services.settings_service import get_public_settings, update_settings
from utils.auth import require_auth
from utils.serializers import format_datetime

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/settings", methods=["GET"])
@require_auth()
def get_settings_route():
    try:
        settings = get_public_settings()
        settings["updatedAt"] = format_datetime(settings.get("updatedAt"))
        return jsonify({"success": True, "data": settings}), 200
    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to get settings",
            "error": str(error),
        }), 500


@settings_bp.route("/settings", methods=["PUT"])
@require_auth(roles=["admin"])
def update_settings_route():
    try:
        data = request.get_json() or {}
        updated = update_settings(data)

        if "pingInterval" in data:
            reschedule_monitor_job(int(updated.get("pingInterval", 30)))

        log_audit(
            action="settings_updated",
            entity_type="settings",
            entity_id="global",
            details={
                "pingInterval": updated.get("pingInterval"),
                "pingTimeoutMs": updated.get("pingTimeoutMs"),
                "pingRetries": updated.get("pingRetries"),
                "smtpUpdated": "smtp" in data,
            },
        )

        public = get_public_settings()
        public["updatedAt"] = format_datetime(public.get("updatedAt"))
        return jsonify({
            "success": True,
            "message": "Settings updated",
            "data": public,
        }), 200

    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to update settings",
            "error": str(error),
        }), 500
