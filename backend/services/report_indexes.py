"""
Minimal MongoDB indexes required for bounded report queries.

Does not drop or alter existing indexes (including TTL). Safe to call
on every bootstrap.
"""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING

from config.database import db
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("reports.indexes")


def ensure_report_indexes() -> None:
    try:
        db.pingHistory.create_index(
            [("deviceId", ASCENDING), ("timestamp", ASCENDING)],
            name="idx_pingHistory_device_timestamp",
        )
        logger.info("Report index ensured | pingHistory.deviceId+timestamp")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to ensure pingHistory report index: %s", exc)

    try:
        db.alerts.create_index(
            [("createdAt", DESCENDING)],
            name="idx_alerts_createdAt",
        )
        logger.info("Report index ensured | alerts.createdAt")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to ensure alerts.createdAt index: %s", exc)

    try:
        db.alerts.create_index(
            [("deviceId", ASCENDING), ("createdAt", DESCENDING)],
            name="idx_alerts_device_createdAt",
        )
        logger.info("Report index ensured | alerts.deviceId+createdAt")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to ensure alerts device index: %s", exc)
