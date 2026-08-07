"""
Ping history persistence with acknowledged writes (Phases 1 & 6).
"""

from __future__ import annotations

from models.ping_history import create_ping_history
from services.mongo_retry import assert_insert_acknowledged, with_mongo_retry
from services.monitor_events import EVENT_PING_HISTORY_ADDED, publish
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("history")


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def save_ping_history(device, ping_result, scan_type="Manual", *, cycle_id=None):
    """Save one ping result into MongoDB. Raises on hard failure after retries."""
    device_id = device.get("_id")
    ip_address = device.get("ipAddress")
    history = create_ping_history(
        device_id=device_id,
        hostname=device.get("hostname", "Unknown"),
        ip_address=ip_address,
        status=ping_result["status"],
        response_time=ping_result["responseTime"],
        scan_type=scan_type,
    )
    if cycle_id:
        history["cycleId"] = cycle_id

    def _insert():
        return _db().pingHistory.insert_one(history)

    result = with_mongo_retry(
        _insert,
        action="ping_history_insert",
        device_id=device_id,
        ip_address=ip_address,
    )
    assert_insert_acknowledged(
        result,
        action="ping_history_insert",
        device_id=device_id,
        ip_address=ip_address,
    )

    publish(
        EVENT_PING_HISTORY_ADDED,
        {
            "deviceId": str(device_id) if device_id is not None else None,
            "hostname": device.get("hostname"),
            "ipAddress": ip_address,
            "status": ping_result.get("status"),
            "responseTime": ping_result.get("responseTime"),
            "scanType": scan_type,
            "historyId": str(result.inserted_id),
            "cycleId": cycle_id,
        },
    )
    return result.inserted_id
