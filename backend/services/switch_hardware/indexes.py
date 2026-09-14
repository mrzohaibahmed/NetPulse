"""MongoDB indexes for switch hardware monitoring collections."""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING

from config.database import db
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("switch_hardware.indexes")


def ensure_switch_hardware_indexes() -> None:
    try:
        db.switch_hardware_current.create_index(
            [("deviceId", ASCENDING)],
            unique=True,
            name="uniq_switch_hardware_current_deviceId",
        )
        db.switch_hardware_history.create_index(
            [("deviceId", ASCENDING), ("timestamp", DESCENDING)],
            name="idx_switch_hardware_history_device_ts",
        )
        db.switch_hardware_events.create_index(
            [("deviceId", ASCENDING), ("timestamp", DESCENDING)],
            name="idx_switch_hardware_events_device_ts",
        )
        db.switch_hardware_events.create_index(
            [
                ("deviceId", ASCENDING),
                ("eventType", ASCENDING),
                ("component", ASCENDING),
                ("resolved", ASCENDING),
            ],
            name="idx_switch_hardware_events_active_lookup",
        )
        db.switch_outage_incidents.create_index(
            [("deviceId", ASCENDING), ("startedAt", DESCENDING)],
            name="idx_switch_outage_incidents_device_started",
        )
        db.switch_outage_incidents.create_index(
            [("deviceId", ASCENDING), ("status", ASCENDING)],
            name="idx_switch_outage_incidents_device_status",
        )
        logger.info("Switch hardware indexes ensured")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Switch hardware index ensure failed: %s", exc)
