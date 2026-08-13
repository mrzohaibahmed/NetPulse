"""
Dedicated scheduler process entrypoint (Option A deployment).

Importing ``app`` runs bootstrap once. APScheduler starts because
``NETPULSE_ROLE=scheduler`` is set before import.
"""

from __future__ import annotations

import os
import threading

os.environ.setdefault("NETPULSE_ROLE", "scheduler")
os.environ.setdefault("NETPULSE_ENABLE_SCHEDULER", "true")

from config.deployment import startup_identity  # noqa: E402
from utils.monitor_logger import get_monitor_logger  # noqa: E402

import app  # noqa: F401,E402 — bootstrap + start_scheduler side effects

_logger = get_monitor_logger("run_scheduler")


def main() -> None:
    boot = startup_identity()
    _logger.info(
        "NetPulse scheduler process blocking | hostname=%s | pid=%s | role=%s",
        boot["hostname"],
        boot["pid"],
        boot["role"],
    )
    threading.Event().wait()


if __name__ == "__main__":
    main()
