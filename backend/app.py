import os

from pathlib import Path

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

from config.cors_config import build_cors_kwargs
from config.deployment import should_start_scheduler, startup_identity
from config.database import db
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
from services.interface_collection.mac_arp_collector import ensure_mac_arp_indexes
from services.interface_collection.stats_collector import ensure_interface_stats_indexes
from services.settings_service import ensure_settings
from services.scheduler_ownership import ensure_scheduler_lock_indexes
from services.monitor_indexes import ensure_monitoring_idempotency_indexes
from services.monitor_schedule_migration import ensure_monitor_schedule_migration
from services.storm.pipeline_cycles import ensure_pipeline_cycle_indexes
from services.storm.confirmation import ensure_confirmation_indexes
from services.storm.eligibility import ensure_eligibility_indexes
from services.storm.incident import ensure_incident_indexes
from services.storm.risk_engine import ensure_risk_indexes
from services.storm.risk_latest import (
    ensure_risk_latest_indexes,
    rebuild_risk_latest,
    risk_latest_enabled,
)
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
from services.ops_health import legacy_health_payload, liveness_payload, readiness_payload
from utils.monitor_logger import get_monitor_logger

_bootstrap_logger = get_monitor_logger("app.bootstrap")

# Serve built frontend (Vite) if present.
BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIST = (BASE_DIR.parent / "frontend" / "dist").resolve()
HAS_FRONTEND_DIST = FRONTEND_DIST.exists()

# Do not set static_url_path="" — that registers a catch-all static route and breaks
# React Router deep links (/dashboard, etc.) with 404 before spa_fallback runs.
app = Flask(__name__)

# Bound request bodies (CSV import + JSON APIs). Override via MAX_CONTENT_LENGTH bytes.
_max_content = int(os.getenv("MAX_CONTENT_LENGTH", str(2 * 1024 * 1024)))
app.config["MAX_CONTENT_LENGTH"] = max(_max_content, 64 * 1024)

CORS(app, **build_cors_kwargs())

from utils.security_headers import register_security_headers  # noqa: E402

register_security_headers(app)

from utils.api_errors import ensure_request_id  # noqa: E402


@app.before_request
def _assign_request_id():
    ensure_request_id()


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
        return send_from_directory(FRONTEND_DIST, "index.html")
    return jsonify({
        "message": "Network Monitor API is running",
        "status": "Connected",
    })


@app.route("/assets/<path:filename>")
def frontend_assets(filename: str):
    """Production JS/CSS chunks from Vite build output."""
    if not HAS_FRONTEND_DIST:
        return jsonify({"success": False, "message": "Frontend build not found"}), 404
    return send_from_directory(FRONTEND_DIST / "assets", filename)


def _is_api_path(path: str) -> bool:
    """True when the path belongs to the REST API (never SPA fallback)."""
    return path == "api" or path.startswith("api/")


@app.route("/<path:path>")
def spa_fallback(path: str):
    """Serve frontend routes (React Router) when built assets exist."""
    if _is_api_path(path):
        return jsonify({"success": False, "message": "Not found"}), 404

    if not HAS_FRONTEND_DIST:
        return jsonify({"success": False, "message": "Frontend build not found"}), 404

    full_path = FRONTEND_DIST / path
    if full_path.exists() and full_path.is_file():
        return send_from_directory(FRONTEND_DIST, path)

    return send_from_directory(FRONTEND_DIST, "index.html")


@app.route("/health/live")
def health_live():
    return jsonify(liveness_payload()), 200


@app.route("/health/ready")
def health_ready():
    body, code = readiness_payload()
    return jsonify(body), code


@app.route("/health")
def health():
    body, code = legacy_health_payload()
    return jsonify(body), code


def bootstrap():
    ensure_secrets_encryption_configured()
    ensure_settings()
    # 60s dispatch cutover: cadence defaults + staggered nextCheckAt backfill.
    try:
        ensure_monitor_schedule_migration()
    except Exception as exc:  # noqa: BLE001
        _bootstrap_logger.warning(
            "[MONITOR-SCHEDULE] migration failed (non-fatal): %s", exc
        )

    # Database-level uniqueness constraints (idempotent).
    # MongoDB enforces these under concurrency; application checks are kept
    # for user-friendly errors.
    ensure_device_indexes()
    ensure_isp_indexes()
    ensure_isp_connections()
    # Phase 5 — scheduler leader-election lock collection.
    ensure_scheduler_lock_indexes()
    # Login brute-force protection indexes (TTL).
    try:
        from services.login_rate_limit import ensure_login_rate_limit_indexes  # noqa: PLC0415

        ensure_login_rate_limit_indexes()
    except Exception as exc:  # noqa: BLE001
        _bootstrap_logger.warning(
            "[LOGIN-RATE-LIMIT] index ensure failed (non-fatal): %s", exc
        )
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
    ensure_mac_arp_indexes()
    ensure_pipeline_cycle_indexes()
    ensure_eligibility_indexes()
    ensure_risk_indexes()
    ensure_risk_latest_indexes()
    # Rebuild empty projection once so Confirmation can skip history $topN.
    if risk_latest_enabled():
        try:
            if db.storm_risk_latest.estimated_document_count() <= 0:
                if db.storm_risk_history.estimated_document_count() > 0:
                    summary = rebuild_risk_latest()
                    _bootstrap_logger.info(
                        "[RISK_LATEST] initial rebuild %s", summary
                    )
        except Exception as exc:  # noqa: BLE001
            _bootstrap_logger.warning(
                "[RISK_LATEST] initial rebuild failed (non-fatal): %s", exc
            )
    ensure_confirmation_indexes()
    ensure_safety_indexes()
    ensure_incident_indexes()
    ensure_mitigation_indexes()
    ensure_recovery_indexes()
    ensure_retention_ttl_indexes()
    try:
        from services.report_indexes import ensure_report_indexes  # noqa: PLC0415

        ensure_report_indexes()
    except Exception as exc:  # noqa: BLE001
        _bootstrap_logger.warning("[REPORTS] index ensure failed (non-fatal): %s", exc)
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

_boot = startup_identity()
_bootstrap_logger.info(
    "NetPulse process boot | hostname=%s | pid=%s | role=%s | env=%s | "
    "schedulerEnabled=%s | enableSchedulerSetting=%s | gunicornMultiWorker=%s",
    _boot["hostname"],
    _boot["pid"],
    _boot["role"],
    _boot["environment"],
    _boot["schedulerEnabled"],
    _boot["enableSchedulerSetting"],
    _boot["gunicornMultiWorker"],
)

if should_start_scheduler():
    start_scheduler()
    _bootstrap_logger.info(
        "Scheduler started in this process | pid=%s | role=%s",
        _boot["pid"],
        _boot["role"],
    )
else:
    _bootstrap_logger.info(
        "Scheduler not started in this process | pid=%s | role=%s | reason=%s",
        _boot["pid"],
        _boot["role"],
        "NETPULSE_ROLE=api"
        if _boot["role"] == "api"
        else (
            "multi_worker_wsgi"
            if _boot["gunicornMultiWorker"]
            else "explicit_disable_or_reloader_parent"
        ),
    )


if __name__ == "__main__":
    DEBUG = os.getenv("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")
    # Prefer localhost bind; override with FLASK_RUN_HOST when intentionally LAN-exposed
    # behind a reverse proxy is NOT used (not recommended for production).
    default_host = "127.0.0.1"
    run_host = (os.getenv("FLASK_RUN_HOST") or default_host).strip() or default_host
    if DEBUG:
        run_host = "127.0.0.1"
        print(
            "WARNING: FLASK_DEBUG is enabled — binding to 127.0.0.1 only. "
            "Do not expose the Werkzeug debugger on a network interface.",
            flush=True,
        )
    elif run_host in ("0.0.0.0", "::"):
        print(
            "WARNING: Binding to all interfaces without a reverse proxy exposes "
            "HTTP plaintext. Prefer FLASK_RUN_HOST=127.0.0.1 behind HTTPS.",
            flush=True,
        )
    app.run(
        host=run_host,
        port=int(os.getenv("FLASK_RUN_PORT", "5000")),
        debug=DEBUG,
    )
