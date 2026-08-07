"""MongoDB indexes for the device inventory collection."""

from __future__ import annotations

from pymongo import ASCENDING

from config.database import db
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("devices")


def ensure_device_indexes() -> None:
    """Create indexes on ``devices`` (safe to call repeatedly)."""
    try:
        db.devices.create_index(
            [("ipAddress", ASCENDING)],
            unique=True,
            name="uniq_devices_ipAddress",
        )
        logger.info("[DEVICES] MongoDB indexes ensured on devices collection")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[DEVICES] Failed to ensure indexes: %s", exc)
