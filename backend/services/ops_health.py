"""
Operational health and metrics snapshots for production monitoring.

No secrets, credentials, or raw exception strings in API responses.
"""

from __future__ import annotations

from typing import Any

from config.deployment import is_scheduler_process, startup_identity
from utils.utc import format_utc, utc_now


def _mongo_ping() -> tuple[bool, str | None]:
    try:
        from config.database import db  # noqa: PLC0415

        db.command("ping")
        return True, None
    except Exception:  # noqa: BLE001
        return False, "database_unreachable"


def liveness_payload() -> dict[str, Any]:
    ident = startup_identity()
    return {
        "status": "alive",
        "timestamp": format_utc(utc_now()),
        "hostname": ident["hostname"],
        "pid": ident["pid"],
    }


def readiness_payload() -> tuple[dict[str, Any], int]:
    ident = startup_identity()
    mongo_ok, mongo_reason = _mongo_ping()
    scheduler_expected = is_scheduler_process()
    scheduler_running = False
    scheduler_leader = False
    owner_id = None

    if scheduler_expected:
        try:
            from scheduler import scheduler  # noqa: PLC0415
            from services.scheduler_ownership import (  # noqa: PLC0415
                get_owner_id,
                is_scheduler_leader,
            )

            scheduler_running = bool(scheduler.running)
            scheduler_leader = is_scheduler_leader()
            owner_id = get_owner_id()
        except Exception:  # noqa: BLE001
            scheduler_running = False

    ready = mongo_ok and (not scheduler_expected or scheduler_running)
    status_code = 200 if ready else 503

    body: dict[str, Any] = {
        "status": "ready" if ready else "not_ready",
        "timestamp": format_utc(utc_now()),
        "environment": ident["environment"],
        "role": ident["role"],
        "schedulerExpected": scheduler_expected,
        "schedulerRunning": scheduler_running,
        "schedulerLeader": scheduler_leader,
        "ownerId": owner_id,
        "checks": {
            "mongodb": "ok" if mongo_ok else mongo_reason,
        },
    }
    if scheduler_expected and not scheduler_running:
        body["checks"]["scheduler"] = "not_running"

    return body, status_code


def legacy_health_payload() -> tuple[dict[str, Any], int]:
    """Backward-compatible ``/health`` — mirrors readiness without error strings."""
    body, code = readiness_payload()
    simplified = {
        "server": "Running" if body["status"] == "ready" else "Degraded",
        "database": "Connected" if body["checks"].get("mongodb") == "ok" else "Disconnected",
    }
    return simplified, code


def ops_metrics_snapshot() -> dict[str, Any]:
    """Aggregate non-secret operational metrics for admin inspection."""
    snap: dict[str, Any] = {
        "identity": startup_identity(),
        "timestamp": format_utc(utc_now()),
    }

    try:
        from services.monitor_metrics import get_dispatch_metrics  # noqa: PLC0415
        from services.monitor_runtime import get_monitor_runtime_stats  # noqa: PLC0415

        runtime = get_monitor_runtime_stats()
        snap["ping"] = get_dispatch_metrics().snapshot(
            workers_active=int(runtime.get("workers_active") or 0),
            queue_depth=int(runtime.get("queue_depth") or 0),
            workers_total=int(runtime.get("workers_total") or 0),
        )
        snap["ping"]["runtime"] = runtime
    except Exception:  # noqa: BLE001
        snap["ping"] = {}

    try:
        from services.collector_concurrency import collector_slot_stats  # noqa: PLC0415

        snap["ssh"] = collector_slot_stats()
    except Exception:  # noqa: BLE001
        snap["ssh"] = {}

    try:
        from config.database import db  # noqa: PLC0415
        from services.scheduler_ownership import get_scheduler_status  # noqa: PLC0415

        snap["scheduler"] = get_scheduler_status()
        snap["storm"] = {
            "readyForMitigation": db.storm_incidents.count_documents(
                {"status": "READY_FOR_MITIGATION"}
            ),
            "failedRecoveryRequired": db.storm_pipeline_cycles.count_documents(
                {"status": "failed_recovery_required"}
            ),
        }
    except Exception:  # noqa: BLE001
        snap["scheduler"] = {}
        snap["storm"] = {}

    try:
        from config.mongo_config import build_mongo_client_kwargs  # noqa: PLC0415
        from config.database import MONGO_URI  # noqa: PLC0415
        from config.mongo_config import safe_mongo_log_summary  # noqa: PLC0415

        snap["mongodb"] = safe_mongo_log_summary(
            MONGO_URI, build_mongo_client_kwargs()
        )
    except Exception:  # noqa: BLE001
        snap["mongodb"] = {}

    return snap
