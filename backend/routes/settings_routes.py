from utils.api_errors import internal_error_response
from flask import Blueprint, jsonify, request

from scheduler import reschedule_monitor_job
from services.audit_service import log_audit
from services.history_deletion_service import (
    HistoryDeletionError,
    VALID_HISTORY_DELETION_SCOPES,
    delete_history,
)
from services.retention_service import RetentionTtlSyncError
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
                "requiredConfirmations": updated.get("requiredConfirmations"),
                "pingHistoryRetentionDays": updated.get("pingHistoryRetentionDays"),
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
    except RetentionTtlSyncError as error:
        public = get_public_settings()
        public["updatedAt"] = format_datetime(public.get("updatedAt"))
        return jsonify({
            "success": False,
            "message": str(error),
            "data": public,
            "ttlSync": error.results,
        }), 409
    except Exception as error:
        return internal_error_response(error, message="Failed to update settings")


@settings_bp.route("/settings/history", methods=["DELETE"])
@require_auth(roles=["admin"])
def delete_history_route():
    try:
        body = request.get_json(silent=True) or {}
        if any(key in body for key in ("collection", "collections", "filter")):
            return jsonify({
                "success": False,
                "message": "Invalid request",
            }), 400

        scope = str(body.get("scope") or "").strip().lower()
        if not scope:
            return jsonify({
                "success": False,
                "message": "scope is required",
            }), 400
        if scope not in VALID_HISTORY_DELETION_SCOPES:
            return jsonify({
                "success": False,
                "message": (
                    "Invalid history deletion scope. "
                    "Allowed values: ping, telemetry, incidents, all"
                ),
            }), 400

        results = delete_history(scope)
        log_audit(
            action="MANUAL_HISTORY_DELETE",
            entity_type="settings",
            entity_id="global",
            details={
                "scope": scope,
                "result": results.get("result", "success"),
                "totalDeleted": results.get("totalDeleted", 0),
                "deleted": results.get("deleted", {}),
            },
        )

        total_deleted = int(results.get("totalDeleted", 0))
        if total_deleted == 0:
            message = "No history records were found to delete."
        else:
            message = f"History deleted successfully. {total_deleted:,} records deleted."

        return jsonify({
            "success": True,
            "message": message,
            "scope": results.get("scope", scope),
            "deleted": results.get("deleted", {}),
            "totalDeleted": total_deleted,
        }), 200

    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except HistoryDeletionError as error:
        results = error.results
        log_audit(
            action="MANUAL_HISTORY_DELETE",
            entity_type="settings",
            entity_id="global",
            details={
                "scope": results.get("scope"),
                "result": results.get("result"),
                "totalDeleted": results.get("totalDeleted", 0),
                "deleted": results.get("deleted", {}),
                "failed": results.get("failed", {}),
            },
        )
        return jsonify({
            "success": False,
            "message": str(error),
            "scope": results.get("scope"),
            "deleted": results.get("deleted", {}),
            "failed": results.get("failed", {}),
            "totalDeleted": results.get("totalDeleted", 0),
        }), 500
    except Exception as error:
        return internal_error_response(error, message="Failed to delete history")


@settings_bp.route("/settings/test-email", methods=["POST"])
@require_auth(roles=["admin"])
def test_email_route():
    """Send a test email using the currently saved SMTP configuration.

    Accepts an optional JSON body ``{"to": "override@example.com"}`` to
    send to a specific recipient instead of the configured one.
    Never returns SMTP credentials in the response.
    """
    try:
        from services.email_service import send_email_with_result  # noqa: PLC0415

        body = request.get_json(silent=True) or {}
        override_to = str(body.get("to") or "").strip() or None

        subject = "NetPulse — Test Email"
        body_text = (
            "This is a test email from NetPulse Network Monitor.\n\n"
            "If you received this message, your SMTP configuration is working correctly."
        )
        body_html = """
<html>
<body style="font-family: Arial, sans-serif; color: #132033; padding: 24px;">
  <h2 style="color: #0b4f9e;">NetPulse — Test Email</h2>
  <p>This is a test email from <strong>NetPulse Network Monitor</strong>.</p>
  <p>If you received this message, your SMTP configuration is working correctly.</p>
  <hr style="border:none;border-top:1px solid #e5eaf0;margin:24px 0;" />
  <p style="font-size:12px;color:#64748b;">
    This message was sent automatically. Please do not reply.
  </p>
</body>
</html>"""

        delivered, error_message = send_email_with_result(
            subject,
            body_text,
            body_html,
            to_address=override_to,
        )

        if delivered:
            log_audit(
                action="test_email_sent",
                entity_type="settings",
                entity_id="global",
                details={"overrideTo": bool(override_to)},
            )
            return jsonify({"success": True, "message": "Test email sent successfully."}), 200

        return jsonify({"success": False, "message": error_message or "Failed to send test email."}), 400

    except Exception as error:
        return internal_error_response(error, message="Failed to send test email")


@settings_bp.route("/settings/test-whatsapp", methods=["POST"])
@require_auth(roles=["admin"])
def test_whatsapp_route():
    """Send a test WhatsApp alert using the configured Cloud API credentials.

    Credentials are read from environment variables only and are never returned.
    """
    try:
        from services.whatsapp_service import send_test_whatsapp_alert  # noqa: PLC0415

        delivered, error_message = send_test_whatsapp_alert()

        if delivered:
            log_audit(
                action="test_whatsapp_sent",
                entity_type="settings",
                entity_id="global",
                details={},
            )
            return jsonify(
                {"success": True, "message": "Test WhatsApp alert sent successfully."}
            ), 200

        return jsonify(
            {
                "success": False,
                "message": error_message or "Failed to send test WhatsApp alert.",
            }
        ), 400

    except Exception as error:
        return internal_error_response(error, message="Failed to send test WhatsApp alert")
