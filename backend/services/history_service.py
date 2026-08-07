"""
Ping history persistence with attempt-scoped idempotency.

Each ping attempt carries a unique ``attemptId``. Retrying the same insert
after an ambiguous Mongo acknowledgement never creates a second history row
(DuplicateKeyError is treated as success).
"""

from __future__ import annotations

from pymongo.errors import DuplicateKeyError

from models.ping_history import create_ping_history
from services.mongo_retry import assert_insert_acknowledged, with_mongo_retry
from services.monitor_events import EVENT_PING_HISTORY_ADDED, publish
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("history")


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def save_ping_history(
    device,
    ping_result,
    scan_type="Manual",
    *,
    cycle_id=None,
    attempt_id: str | None = None,
):
    """
    Save one ping result into MongoDB.

    When ``attempt_id`` is provided it is stored as a unique key so retries
    are idempotent.
    """
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
    if attempt_id:
        history["attemptId"] = attempt_id

    def _insert_or_existing():
        try:
            return _db().pingHistory.insert_one(history)
        except DuplicateKeyError:
            # Prior attempt already committed — treat as success.
            if attempt_id:
                existing = _db().pingHistory.find_one(
                    {"attemptId": attempt_id},
                    {"_id": 1},
                )
                if existing is not None:
                    class _ExistingInsert:
                        acknowledged = True
                        inserted_id = existing["_id"]

                    logger.info(
                        "Ping history insert idempotent hit | attemptId=%s | "
                        "historyId=%s | deviceId=%s",
                        attempt_id,
                        existing["_id"],
                        device_id,
                    )
                    return _ExistingInsert()
            raise

    result = with_mongo_retry(
        _insert_or_existing,
        action="ping_history_insert",
        device_id=device_id,
        ip_address=ip_address,
        idempotent=bool(attempt_id),
    )
    assert_insert_acknowledged(
        result,
        action="ping_history_insert",
        device_id=device_id,
        ip_address=ip_address,
    )

    # Publish only when we have a durable id (advisory — never source of truth).
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
            "attemptId": attempt_id,
            "cycleId": cycle_id,
        },
    )
    return result.inserted_id
