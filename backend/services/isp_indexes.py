"""MongoDB indexes for the ``ispConnections`` collection."""

from __future__ import annotations

from pymongo import ASCENDING

from config.database import db
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("isp")


def ensure_isp_indexes() -> None:
    """Create indexes on ``ispConnections`` (idempotent)."""
    try:
        collection = db.ispConnections
        collection.create_index(
            [("monitor", ASCENDING), ("status", ASCENDING)],
            name="idx_isp_monitor_status",
        )
        collection.create_index(
            [("lastCheckedAt", ASCENDING)],
            name="idx_isp_lastCheckedAt",
        )
        collection.create_index(
            [("lastSeen", ASCENDING)],
            name="idx_isp_lastSeen",
        )
        logger.info("[ISP] MongoDB indexes ensured on ispConnections collection")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ISP] Failed to ensure indexes: %s", exc)
