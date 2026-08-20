"""
Indexes that make ping monitoring writes idempotent under retries.
"""

from __future__ import annotations

from pymongo import ASCENDING
from pymongo.errors import OperationFailure

from config.database import db
from services.ping_service import STATUS_OFFLINE_CRITICAL
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("monitor_indexes")


def ensure_monitoring_idempotency_indexes() -> None:
    """
    Idempotent index setup for history attemptIds and active critical alerts.

    Safe to call on every bootstrap.
    """
    try:
        db.pingHistory.create_index(
            [("attemptId", ASCENDING)],
            unique=True,
            name="uniq_pingHistory_attemptId",
            # Legacy rows lack attemptId; sparse unique ignores those.
            sparse=True,
        )
        logger.info("pingHistory.attemptId unique index ensured")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to ensure pingHistory.attemptId index: %s", exc)

    # At most one unrecovered Offline (Critical) alert per device.
    try:
        db.alerts.create_index(
            [("deviceId", ASCENDING)],
            unique=True,
            name="uniq_alerts_active_critical_offline",
            # Partial indexes reject $ne ($not). Active alerts always store
            # resolved/dismissed as False at insert time (see alert_service).
            partialFilterExpression={
                "status": STATUS_OFFLINE_CRITICAL,
                "resolved": False,
                "dismissed": False,
                "deviceId": {"$type": "objectId"},
            },
        )
        logger.info("Active critical-offline alert unique index ensured")
    except OperationFailure as exc:
        # Duplicate existing alerts would block index creation — log loudly.
        logger.error(
            "Failed to ensure active critical alert unique index "
            "(resolve duplicate active alerts if present): %s",
            exc,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to ensure critical alert unique index: %s", exc)
