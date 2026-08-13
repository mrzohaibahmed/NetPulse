# NetPulse Gunicorn configuration — Option B (single worker owns scheduler).
#
# Usage (API + scheduler in one process):
#   cd backend
#   gunicorn -c gunicorn.conf.py "app:app"
#
# For Option A (recommended at scale): run API workers with NETPULSE_ROLE=api
# and start run_scheduler.py separately.

import multiprocessing
import os

bind = os.getenv("GUNICORN_BIND", "0.0.0.0:5000")
workers = int(os.getenv("GUNICORN_WORKERS", "1"))
worker_class = "sync"
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))

# Expose worker count to deployment.should_start_scheduler() auto mode.
os.environ["GUNICORN_WORKERS"] = str(workers)

# Only a single worker may start APScheduler when using this combined model.
if workers > 1:
    os.environ.setdefault("NETPULSE_ROLE", "api")
    os.environ.setdefault("NETPULSE_ENABLE_SCHEDULER", "false")

proc_name = "netpulse-api"
preload_app = False

def on_starting(server):
    server.log.info(
        "Gunicorn starting | workers=%s bind=%s schedulerRole=%s",
        workers,
        bind,
        os.getenv("NETPULSE_ROLE", "all"),
    )

def when_ready(server):
    server.log.info("Gunicorn ready | pid=%s", os.getpid())

def on_exit(server):
    try:
        from scheduler import stop_scheduler

        stop_scheduler()
    except Exception:
        pass
