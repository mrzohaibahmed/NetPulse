"""
Shared Mongo-backed lock helpers for storm mitigation and recovery.

This keeps all active lock semantics in one place so Safety, Mitigation,
and Recovery reason about the same in-flight execution state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

MITIGATION_LOCKS_COLLECTION = "storm_mitigation_locks"
RECOVERY_LOCKS_COLLECTION = "storm_recovery_locks"


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _oid(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    return value


class LockService:
    """Shared lock primitives for storm pipeline execution."""

    @staticmethod
    def mitigation_lock_ids(device_id: Any, interface: str) -> tuple[str, str]:
        return (f"device:{device_id}", f"interface:{device_id}:{interface}")

    @staticmethod
    def recovery_lock_ids(device_id: Any, interface: str) -> tuple[str, str]:
        return (f"recovery:{device_id}", f"recovery:{device_id}:{interface}")

    @staticmethod
    def mitigation_collection():
        return _db()[MITIGATION_LOCKS_COLLECTION]

    @staticmethod
    def recovery_collection():
        return _db()[RECOVERY_LOCKS_COLLECTION]

    @staticmethod
    def is_mitigation_active(device_id: Any, interface: str) -> bool:
        try:
            device_lock_id, interface_lock_id = LockService.mitigation_lock_ids(
                device_id, interface
            )
            lock = LockService.mitigation_collection().find_one(
                {"_id": {"$in": [device_lock_id, interface_lock_id]}}
            )
            return lock is not None
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def acquire_mitigation_locks(device_id: Any, interface: str) -> tuple[str, str]:
        coll = LockService.mitigation_collection()
        device_lock_id, interface_lock_id = LockService.mitigation_lock_ids(
            device_id, interface
        )

        try:
            coll.insert_one(
                {
                    "_id": device_lock_id,
                    "deviceId": _oid(device_id),
                    "createdAt": datetime.now(timezone.utc),
                }
            )
        except DuplicateKeyError as exc:
            raise ValueError(
                f"Mitigation lock conflict: Device {device_id} is currently executing another mitigation."
            ) from exc

        try:
            coll.insert_one(
                {
                    "_id": interface_lock_id,
                    "deviceId": _oid(device_id),
                    "interface": interface,
                    "createdAt": datetime.now(timezone.utc),
                }
            )
        except DuplicateKeyError as exc:
            coll.delete_one({"_id": device_lock_id})
            raise ValueError(
                f"Mitigation lock conflict: Interface {interface} on Device {device_id} "
                f"is currently executing another mitigation."
            ) from exc

        return device_lock_id, interface_lock_id

    @staticmethod
    def release_mitigation_locks(device_lock_id: str, interface_lock_id: str) -> None:
        LockService.mitigation_collection().delete_many(
            {"_id": {"$in": [device_lock_id, interface_lock_id]}}
        )

    @staticmethod
    def acquire_recovery_locks(device_id: Any, interface: str) -> tuple[str, str]:
        coll = LockService.recovery_collection()
        device_lock_id, interface_lock_id = LockService.recovery_lock_ids(
            device_id, interface
        )

        try:
            coll.insert_one(
                {
                    "_id": device_lock_id,
                    "deviceId": _oid(device_id),
                    "createdAt": datetime.now(timezone.utc),
                }
            )
        except DuplicateKeyError as exc:
            raise ValueError(
                f"Recovery lock conflict: Device {device_id} is currently executing recovery."
            ) from exc

        try:
            coll.insert_one(
                {
                    "_id": interface_lock_id,
                    "deviceId": _oid(device_id),
                    "interface": interface,
                    "createdAt": datetime.now(timezone.utc),
                }
            )
        except DuplicateKeyError as exc:
            coll.delete_one({"_id": device_lock_id})
            raise ValueError(
                f"Recovery lock conflict: Interface {interface} on Device {device_id} is currently recovering."
            ) from exc

        return device_lock_id, interface_lock_id

    @staticmethod
    def release_recovery_locks(device_lock_id: str, interface_lock_id: str) -> None:
        LockService.recovery_collection().delete_many(
            {"_id": {"$in": [device_lock_id, interface_lock_id]}}
        )
