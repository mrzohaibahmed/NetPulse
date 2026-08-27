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

ACTIVE_STORM_CONFIRMED_INDEX = "uniq_alerts_active_storm_confirmed"


def _log_active_storm_confirmed_duplicates() -> None:
    """Report duplicate active Storm Confirmed rows that block the unique index."""
    try:
        pipeline = [
            {
                "$match": {
                    "title": "Storm Confirmed",
                    "resolved": False,
                    "dismissed": False,
                    "deviceId": {"$type": "objectId"},
                }
            },
            {
                "$group": {
                    "_id": {
                        "deviceId": "$deviceId",
                        "interface": "$interface",
                        "title": "$title",
                    },
                    "count": {"$sum": 1},
                    "ids": {"$push": "$_id"},
                }
            },
            {"$match": {"count": {"$gt": 1}}},
            {"$limit": 25},
        ]
        dupes = list(db.alerts.aggregate(pipeline))
        if not dupes:
            logger.error(
                "Active Storm Confirmed unique index failed, but no "
                "exact duplicate groups matched the partial filter "
                "(check resolved/dismissed field types)"
            )
            return
        logger.error(
            "Active Storm Confirmed unique index blocked by %s duplicate "
            "group(s). Resolve manually before restart. Example query: "
            "db.alerts.find({title:'Storm Confirmed', resolved:false, "
            "dismissed:false}). Sample groups=%s",
            len(dupes),
            [
                {
                    "deviceId": str((d.get("_id") or {}).get("deviceId")),
                    "interface": (d.get("_id") or {}).get("interface"),
                    "count": d.get("count"),
                    "ids": [str(i) for i in (d.get("ids") or [])[:5]],
                }
                for d in dupes[:5]
            ],
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to enumerate duplicate Storm Confirmed alerts: %s",
            exc,
        )


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

    # At most one active Storm Confirmed alert per device interface.
    try:
        db.alerts.create_index(
            [
                ("deviceId", ASCENDING),
                ("interface", ASCENDING),
                ("title", ASCENDING),
            ],
            unique=True,
            name=ACTIVE_STORM_CONFIRMED_INDEX,
            # Partial indexes reject $ne. New Storm Confirmed inserts store
            # resolved/dismissed as False (see alert_service).
            partialFilterExpression={
                "title": "Storm Confirmed",
                "resolved": False,
                "dismissed": False,
                "deviceId": {"$type": "objectId"},
            },
        )
        logger.info("Active Storm Confirmed alert unique index ensured")
    except OperationFailure as exc:
        logger.error(
            "Failed to ensure active Storm Confirmed unique index "
            "(resolve duplicate active alerts if present): %s",
            exc,
        )
        _log_active_storm_confirmed_duplicates()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to ensure Storm Confirmed unique index: %s",
            exc,
        )
