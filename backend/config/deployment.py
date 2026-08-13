"""
Production deployment configuration.

Controls whether this process starts APScheduler and how it identifies itself
at boot. Mongo scheduler leadership remains the final authority when multiple
processes exist; this layer prevents accidental duplicate schedulers in one host.
"""

from __future__ import annotations

import os
import socket
from typing import Any

# Process role:
#   all       — API + scheduler (single-process / dedicated scheduler entry)
#   api       — HTTP only; never start APScheduler
#   scheduler — scheduler only; no HTTP server (run_scheduler.py)
VALID_ROLES = frozenset({"all", "api", "scheduler"})


def _flask_debug_enabled() -> bool:
    return os.getenv("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")


def _werkzeug_reloader_child() -> bool:
    """True when Flask debug reloader child is serving (not the watch parent)."""
    return os.environ.get("WERKZEUG_RUN_MAIN") == "true"


def _detect_gunicorn_multi_worker() -> bool:
    """
    Best-effort detection of multi-worker WSGI without requiring Gunicorn import.

    Gunicorn sets WEB_CONCURRENCY; operators may also set GUNICORN_WORKERS.
    """
    for key in ("GUNICORN_WORKERS", "WEB_CONCURRENCY"):
        raw = (os.getenv(key) or "").strip()
        if not raw:
            continue
        try:
            if int(raw) > 1:
                return True
        except ValueError:
            continue
    return False


def get_process_role() -> str:
    raw = (os.getenv("NETPULSE_ROLE") or "all").strip().lower()
    if raw not in VALID_ROLES:
        return "all"
    return raw


def get_enable_scheduler_setting() -> str:
    """Raw NETPULSE_ENABLE_SCHEDULER value: true | false | auto."""
    return (os.getenv("NETPULSE_ENABLE_SCHEDULER") or "auto").strip().lower()


def should_start_scheduler() -> bool:
    """
    Return True when this process should call ``start_scheduler()``.

    Rules (first match wins):
    - NETPULSE_ROLE=api  → never
    - NETPULSE_ROLE=scheduler → always (after reloader guard)
    - NETPULSE_ENABLE_SCHEDULER=false → never
    - NETPULSE_ENABLE_SCHEDULER=true → yes (after reloader guard)
    - auto + multi-worker WSGI → no (use dedicated scheduler process)
    - auto + FLASK_DEBUG reloader parent → no
    - otherwise → yes
    """
    role = get_process_role()
    if role == "api":
        return False
    if role == "scheduler":
        return _pass_reloader_guard()

    setting = get_enable_scheduler_setting()
    if setting == "false":
        return False
    if setting == "true":
        return _pass_reloader_guard()

    # auto
    if _detect_gunicorn_multi_worker():
        return False
    return _pass_reloader_guard()


def _pass_reloader_guard() -> bool:
    """Skip scheduler in Flask debug reloader parent only."""
    if _flask_debug_enabled() and not _werkzeug_reloader_child():
        return False
    return True


def is_scheduler_process() -> bool:
    """True when this process is expected to run scheduled jobs."""
    return should_start_scheduler()


def get_app_environment() -> str:
    if _flask_debug_enabled():
        return "development"
    raw = (os.getenv("NETPULSE_ENV") or "").strip().lower()
    if not raw:
        return "development"
    return raw


def startup_identity() -> dict[str, Any]:
    """Safe boot metadata for logs and health endpoints (no secrets)."""
    return {
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "role": get_process_role(),
        "environment": get_app_environment(),
        "schedulerEnabled": should_start_scheduler(),
        "enableSchedulerSetting": get_enable_scheduler_setting(),
        "flaskDebug": _flask_debug_enabled(),
        "gunicornMultiWorker": _detect_gunicorn_multi_worker(),
    }
