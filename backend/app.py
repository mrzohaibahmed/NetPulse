import os

from pathlib import Path

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from config.database import db, DATABASE_NAME
from routes.alert_routes import alert_bp
from routes.auth_routes import auth_bp
from routes.dashboard_routes import dashboard_bp
from routes.device_routes import device_bp
from routes.discovery_routes import discovery_bp
from routes.history_routes import history_bp
from routes.isp_routes import isp_bp
from routes.interface_routes import interface_bp
from routes.nmap_routes import nmap_bp
from routes.report_routes import report_bp
from routes.scan_routes import scan_bp
from routes.settings_routes import settings_bp
from routes.storm_routes import storm_bp
from routes.topology import topology_bp
from scheduler import start_scheduler
from services.device_indexes import ensure_device_indexes
from services.isp_indexes import ensure_isp_indexes
from services.isp_service import ensure_isp_connections
from services.interface_collection.collector import ensure_interface_indexes
from services.interface_collection.stats_collector import ensure_interface_stats_indexes
from services.settings_service import ensure_settings
from services.scheduler_ownership import ensure_scheduler_lock_indexes
from services.monitor_indexes import ensure_monitoring_idempotency_indexes
from services.storm.confirmation import ensure_confirmation_indexes
from services.storm.eligibility import ensure_eligibility_indexes
from services.storm.incident import ensure_incident_indexes
from services.storm.risk_engine import ensure_risk_indexes
from services.storm.safety import ensure_safety_indexes
from services.storm.mitigation import ensure_mitigation_indexes
from services.storm.recovery import ensure_recovery_indexes
from services.user_service import ensure_default_admin
from services.storm.lock_service import LockService
from utils.secret_crypto import ensure_secrets_encryption_configured
from services.retention_service import ensure_retention_ttl_indexes
from services.interface_collection.monitoring_state import (
    migrate_interface_monitoring_state,
)
from utils.monitor_logger import get_monitor_logger

_bootstrap_logger = get_monitor_logger("app.bootstrap")

# Serve built frontend (Vite) if present.
BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIST = (BASE_DIR.parent / "frontend" / "dist").resolve()
HAS_FRONTEND_DIST = FRONTEND_DIST.exists()

app = Flask(
    __name__,
    static_folder=str(FRONTEND_DIST) if HAS_FRONTEND_DIST else None,
    static_url_path="" if HAS_FRONTEND_DIST else None,
)
CORS(app, supports_credentials=True)

app.register_blueprint(auth_bp, url_prefix="/api")
app.register_blueprint(device_bp, url_prefix="/api")
app.register_blueprint(isp_bp, url_prefix="/api")
app.register_blueprint(nmap_bp, url_prefix="/api")
app.register_blueprint(scan_bp, url_prefix="/api")
app.register_blueprint(history_bp, url_prefix="/api")
app.register_blueprint(dashboard_bp, url_prefix="/api")
app.register_blueprint(discovery_bp, url_prefix="/api")
app.register_blueprint(interface_bp, url_prefix="/api")
app.register_blueprint(storm_bp, url_prefix="/api")
app.register_blueprint(alert_bp, url_prefix="/api")
app.register_blueprint(settings_bp, url_prefix="/api")
app.register_blueprint(report_bp, url_prefix="/api")
app.register_blueprint(topology_bp, url_prefix="/api/topology")


@app.route("/")
def home():
    if HAS_FRONTEND_DIST:
        return send_from_directory(app.static_folder, "index.html")
    return jsonify({
        "message": "Network Monitor API is running",
        "database": DATABASE_NAME,
        "status": "Connected",
    })


@app.route("/<path:path>")
def spa_fallback(path: str):
    """Serve frontend routes (React Router) when built assets exist."""
    if not HAS_FRONTEND_DIST:
        return jsonify({"success": False, "message": "Frontend build not found"}), 404

    full_path = FRONTEND_DIST / path
    if full_path.exists() and full_path.is_file():
        return send_from_directory(app.static_folder, path)

    return send_from_directory(app.static_folder, "index.html")


@app.route("/health")
def health():
    try:
        db.command("ping")
        return jsonify({
            "server": "Running",
            "database": "Connected",
        }), 200
    except Exception as e:
        return jsonify({
            "server": "Running",
            "database": "Disconnected",
            "error": str(e),
        }), 500


def bootstrap():
    ensure_secrets_encryption_configured()
    ensure_settings()

    # Database-level uniqueness constraints (idempotent).
    # MongoDB enforces these under concurrency; application checks are kept
    # for user-friendly errors.
    ensure_device_indexes()
    ensure_isp_indexes()
    ensure_isp_connections()
    # Phase 5 — scheduler leader-election lock collection.
    ensure_scheduler_lock_indexes()
    # Idempotent history + active critical-alert uniqueness.
    ensure_monitoring_idempotency_indexes()
    db.users.create_index(
        [("username", 1)],
        unique=True,
        name="uniq_users_username",
    )

    # Enterprise lock lease TTL indexes (idempotent).
    LockService.ensure_lock_ttl_indexes()

    ensure_default_admin()
    ensure_interface_indexes()
    ensure_interface_stats_indexes()
    ensure_eligibility_indexes()
    ensure_risk_indexes()
    ensure_confirmation_indexes()
    ensure_safety_indexes()
    ensure_incident_indexes()
    ensure_mitigation_indexes()
    ensure_recovery_indexes()
    ensure_retention_ttl_indexes()
    # Repair sticky monitoringEnabled=false latch (idempotent).
    migrate_interface_monitoring_state(apply=True)
    # Remove retired pipelineGeneration field + indexes (idempotent).
    _remove_pipeline_generation_artifacts()


def _remove_pipeline_generation_artifacts() -> None:
    """Unset pipelineGeneration from storm collections and drop related indexes."""
    field = "pipelineGeneration"
    collections = (
        "interfaces",
        "storm_risk_history",
        "storm_confirmation_history",
        "storm_safety_history",
        "storm_incidents",
        "storm_mitigation_history",
        "storm_recovery_history",
    )
    for name in collections:
        try:
            result = db[name].update_many(
                {field: {"$exists": True}},
                {"$unset": {field: ""}},
            )
            if result.modified_count:
                _bootstrap_logger.info(
                    "[PIPELINE-GEN] unset %s from %s (%s docs)",
                    field,
                    name,
                    result.modified_count,
                )
        except Exception as exc:  # noqa: BLE001
            _bootstrap_logger.warning(
                "[PIPELINE-GEN] unset failed on %s: %s", name, exc
            )

    index_drops = (
        ("interfaces", "idx_iface_pipeline_generation"),
        ("storm_risk_history", "idx_risk_gen"),
        ("storm_confirmation_history", "idx_confirm_gen"),
        ("storm_safety_history", "idx_safety_gen"),
        ("storm_mitigation_history", "idx_mitigation_gen"),
        ("storm_recovery_history", "idx_recovery_gen"),
        ("storm_incidents", "idx_incident_pipeline_generation_status"),
        ("storm_incidents", "idx_incident_pipeline_generation_created"),
    )
    for collection, index_name in index_drops:
        try:
            db[collection].drop_index(index_name)
            _bootstrap_logger.info(
                "[PIPELINE-GEN] dropped index %s.%s", collection, index_name
            )
        except Exception:  # noqa: BLE001
            pass


bootstrap()

# Start monitoring unless we're the Flask debug reloader parent process.
# Default OFF — production must not run the interactive debugger.
DEBUG = os.getenv("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")
if not DEBUG or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    start_scheduler()


if __name__ == "__main__":
    # Debug mode must never bind all interfaces (Werkzeug debugger exposure).
    # Non-debug keeps LAN-reachable 0.0.0.0 for normal deployments.
    run_host = "127.0.0.1" if DEBUG else "0.0.0.0"
    if DEBUG:
        print(
            "WARNING: FLASK_DEBUG is enabled — binding to 127.0.0.1 only. "
            "Do not expose the Werkzeug debugger on a network interface.",
            flush=True,
        )
    app.run(
        host=run_host,
        port=5000,
        debug=DEBUG,
    )
