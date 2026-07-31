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
from routes.interface_routes import interface_bp
from routes.nmap_routes import nmap_bp
from routes.report_routes import report_bp
from routes.scan_routes import scan_bp
from routes.settings_routes import settings_bp
from routes.storm_routes import storm_bp
from scheduler import start_scheduler
from services.interface_collection.collector import ensure_interface_indexes
from services.interface_collection.stats_collector import ensure_interface_stats_indexes
from services.settings_service import ensure_settings
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
    db.devices.create_index(
        [("ipAddress", 1)],
        unique=True,
        name="uniq_devices_ipAddress",
    )
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
