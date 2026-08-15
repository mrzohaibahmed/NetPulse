from utils.api_errors import internal_error_response
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
        return internal_error_response(error, message="Failed to get settings")


@settings_bp.route("/settings", methods=["PUT"])
@require_auth(roles=["admin"])
def update_settings_route():
    try:
        data = request.get_json() or {}
        updated = update_settings(data)

        # pingInterval: legacy reschedules APScheduler to the new period;
        # dispatch mode leaves MONITOR_DISPATCHER_INTERVAL_SECONDS alone and
        # only affects nextCheckAt on future claims (no mass rewrite).
        if "pingInterval" in data:
            reschedule_monitor_job(int(updated.get("pingInterval", 60)))

        # pingConcurrency: safe idle rebuild of the dispatch runtime only —
        # never recreate the pool on every dispatcher tick.
        if "pingConcurrency" in data:
            try:
                from services.monitor_runtime import (  # noqa: PLC0415
                    reconfigure_monitor_runtime_concurrency,
                )

                reconfigure_monitor_runtime_concurrency()
            except Exception:
                # Settings write already succeeded; runtime refresh is best-effort.
                pass

        log_audit(
            action="settings_updated",
            entity_type="settings",
            entity_id="global",
            details={
                "pingInterval": updated.get("pingInterval"),
                "pingTimeoutMs": updated.get("pingTimeoutMs"),
                "pingRetries": updated.get("pingRetries"),
                "pingConcurrency": updated.get("pingConcurrency"),
                "smtpUpdated": "smtp" in data,
                "mitigationMode": updated.get("mitigationMode"),
                "autoRecovery": updated.get("autoRecovery"),
                "cooldownMinutes": updated.get("cooldownMinutes"),
                "stabilizationSeconds": updated.get("stabilizationSeconds"),
                "maximumRecoveryAttempts": updated.get("maximumRecoveryAttempts"),
                "reMitigationThreshold": updated.get("reMitigationThreshold"),
                "dataRetentionDays": updated.get("dataRetentionDays"),
                "incidentRetentionDays": updated.get("incidentRetentionDays"),
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
        return internal_error_response(error, message="Failed to update settings")
