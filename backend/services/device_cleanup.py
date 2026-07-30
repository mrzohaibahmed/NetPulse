"""
Device deletion cascade — purge all documents that reference a device.

Transactions are used when the MongoDB deployment supports them (replica set /
mongos). Otherwise deletes run sequentially with per-collection error logging.
"""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from pymongo.errors import ConnectionFailure, OperationFailure, PyMongoError

from config.database import client, db
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("device.cleanup")

# Collections keyed by deviceId (ObjectId). auditLogs intentionally excluded.
DEVICE_ID_COLLECTIONS: tuple[str, ...] = (
    "pingHistory",
    "interfaces",
    "interface_stats",
    "eligibility_results",
    "storm_risk_history",
    "storm_confirmation_history",
    "storm_safety_history",
    "storm_incidents",
    "storm_mitigation_history",
    "storm_recovery_history",
    "storm_mitigation_locks",
    "storm_recovery_locks",
    "alerts",
)


def _device_id_filter(device_oid: ObjectId) -> dict[str, Any]:
    """Match ObjectId or legacy string form of the same id."""
    return {"deviceId": {"$in": [device_oid, str(device_oid)]}}


def _purge_related(device_oid: ObjectId, *, session=None) -> dict[str, int]:
    """Delete related docs; return deleted counts per collection."""
    deleted: dict[str, int] = {}
    filt = _device_id_filter(device_oid)
    for name in DEVICE_ID_COLLECTIONS:
        kwargs = {"session": session} if session is not None else {}
        result = db[name].delete_many(filt, **kwargs)
        deleted[name] = int(result.deleted_count)
    return deleted


def _delete_device_doc(device_oid: ObjectId, *, session=None) -> int:
    kwargs = {"session": session} if session is not None else {}
    result = db.devices.delete_one({"_id": device_oid}, **kwargs)
    return int(result.deleted_count)


def _cascade_transactional(device_oid: ObjectId) -> dict[str, Any]:
    with client.start_session() as session:
        with session.start_transaction():
            deleted = _purge_related(device_oid, session=session)
            device_deleted = _delete_device_doc(device_oid, session=session)
            if device_deleted < 1:
                # Abort: device vanished mid-flight.
                raise RuntimeError("Device document missing during transactional delete")
    return {
        "mode": "transaction",
        "deviceDeleted": device_deleted,
        "relatedDeleted": deleted,
        "errors": [],
    }


def _cascade_sequential(device_oid: ObjectId) -> dict[str, Any]:
    """
    Best-effort cascade without a multi-document transaction.

    Related collections are cleared first, then the device. Individual failures
    are logged and collected; later collections still run.
    """
    deleted: dict[str, int] = {}
    errors: list[dict[str, str]] = []
    filt = _device_id_filter(device_oid)

    for name in DEVICE_ID_COLLECTIONS:
        try:
            result = db[name].delete_many(filt)
            deleted[name] = int(result.deleted_count)
        except PyMongoError as exc:
            deleted[name] = 0
            errors.append({"collection": name, "error": str(exc)})
            logger.error(
                "Cascade delete failed | collection=%s deviceId=%s error=%s",
                name,
                device_oid,
                exc,
            )

    device_deleted = 0
    try:
        device_deleted = _delete_device_doc(device_oid)
    except PyMongoError as exc:
        errors.append({"collection": "devices", "error": str(exc)})
        logger.error(
            "Cascade delete failed | collection=devices deviceId=%s error=%s",
            device_oid,
            exc,
        )
        raise

    if errors:
        logger.warning(
            "Cascade delete completed with partial failures | deviceId=%s errors=%s",
            device_oid,
            errors,
        )

    return {
        "mode": "sequential",
        "deviceDeleted": device_deleted,
        "relatedDeleted": deleted,
        "errors": errors,
    }


def cascade_delete_device(device_oid: ObjectId) -> dict[str, Any]:
    """
    Remove a device and all documents that reference it.

    Prefers a MongoDB multi-document transaction; falls back to sequential
    deletes with per-collection error logging when transactions are unavailable
    (standalone mongod, etc.).
    """
    if not isinstance(device_oid, ObjectId):
        raise TypeError("device_oid must be an ObjectId")

    try:
        result = _cascade_transactional(device_oid)
        logger.info(
            "Device cascade delete (transaction) | deviceId=%s related=%s",
            device_oid,
            result["relatedDeleted"],
        )
        return result
    except (OperationFailure, ConnectionFailure, PyMongoError, RuntimeError) as exc:
        # OperationFailure code 20 / IllegalOperation often means no replica set.
        logger.warning(
            "Transactional device delete unavailable (%s); using sequential cascade | deviceId=%s",
            exc,
            device_oid,
        )
        return _cascade_sequential(device_oid)
