"""
Shared Mongo-backed lock helpers for storm mitigation and recovery.

This keeps all active lock semantics in one place so Safety, Mitigation,
and Recovery reason about the same in-flight execution state.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from datetime import timedelta
from typing import Any

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

MITIGATION_LOCKS_COLLECTION = "storm_mitigation_locks"
RECOVERY_LOCKS_COLLECTION = "storm_recovery_locks"

DEFAULT_LOCK_TTL_SECONDS = 300
LOCK_TTL_ENV = "STORM_LOCK_TTL_SECONDS"


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
    def get_lock_ttl_seconds() -> int:
        raw = os.getenv(LOCK_TTL_ENV, str(DEFAULT_LOCK_TTL_SECONDS)).strip()
        try:
            ttl = int(raw)
        except (TypeError, ValueError):
            ttl = DEFAULT_LOCK_TTL_SECONDS
        return max(ttl, 1)

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
    def ensure_lock_ttl_indexes() -> None:
        """
        Ensure TTL indexes on expiresAt so MongoDB automatically deletes expired
        lock documents.
        """
        try:
            LockService.mitigation_collection().create_index(
                [("expiresAt", 1)],
                name="idx_mitigation_locks_expiresAt_ttl",
                expireAfterSeconds=0,
            )
            LockService.recovery_collection().create_index(
                [("expiresAt", 1)],
                name="idx_recovery_locks_expiresAt_ttl",
                expireAfterSeconds=0,
            )
        except Exception:  # noqa: BLE001
            # Idempotent best-effort. Operational errors will surface on use.
            pass

    @staticmethod
    def renew_lock(
        device_lock_id: str,
        interface_lock_id: str,
        *,
        execution_id: str | None = None,
        owner: str | None = None,
    ) -> bool:
        """
        Renew a pair of lock documents by pushing their expiresAt forward.

        If execution_id is provided and lock docs contain `executionId`,
        the renew operation requires an exact match (prevents renewal theft).
        """
        ttl = LockService.get_lock_ttl_seconds()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl)

        is_recovery = str(device_lock_id).startswith("recovery:")
        coll = (
            LockService.recovery_collection()
            if is_recovery
            else LockService.mitigation_collection()
        )

        ids = [device_lock_id, interface_lock_id]
        query: dict[str, Any] = {"_id": {"$in": ids}}
        if execution_id is not None:
            query["executionId"] = execution_id
        if owner is not None:
            query["owner"] = owner

        res = coll.update_many(
            query,
            {"$set": {"expiresAt": expires_at}},
        )
        # Both docs must be present and match the query.
        return int(res.modified_count or 0) == 2

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
    def _cleanup_expired_lock_ids(
        coll,
        device_lock_id: str,
        interface_lock_id: str,
        *,
        now: datetime,
    ) -> None:
        """
        Best-effort cleanup before acquisition to reclaim expired locks
        immediately (tests and fast failover), independent of TTL index timing.
        """
        try:
            coll.delete_many(
                {
                    "_id": {"$in": [device_lock_id, interface_lock_id]},
                    "expiresAt": {"$lte": now},
                }
            )
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def acquire_mitigation_locks(
        device_id: Any,
        interface: str,
        *,
        owner: str | None = None,
        execution_id: str | None = None,
    ) -> tuple[str, str]:
        coll = LockService.mitigation_collection()
        device_lock_id, interface_lock_id = LockService.mitigation_lock_ids(
            device_id, interface
        )

        ttl = LockService.get_lock_ttl_seconds()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl)

        # Reclaim expired locks before attempting insert.
        LockService._cleanup_expired_lock_ids(
            coll,
            device_lock_id,
            interface_lock_id,
            now=now,
        )

        try:
            coll.insert_one(
                {
                    "_id": device_lock_id,
                    "deviceId": _oid(device_id),
                    "createdAt": datetime.now(timezone.utc),
                    "expiresAt": expires_at,
                    **({"owner": owner} if owner is not None else {}),
                    **({"executionId": execution_id} if execution_id is not None else {}),
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
                    "expiresAt": expires_at,
                    **({"owner": owner} if owner is not None else {}),
                    **({"executionId": execution_id} if execution_id is not None else {}),
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
    def acquire_recovery_locks(
        device_id: Any,
        interface: str,
        *,
        owner: str | None = None,
        execution_id: str | None = None,
    ) -> tuple[str, str]:
        coll = LockService.recovery_collection()
        device_lock_id, interface_lock_id = LockService.recovery_lock_ids(
            device_id, interface
        )

        ttl = LockService.get_lock_ttl_seconds()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl)

        # Reclaim expired locks before attempting insert.
        LockService._cleanup_expired_lock_ids(
            coll,
            device_lock_id,
            interface_lock_id,
            now=now,
        )

        try:
            coll.insert_one(
                {
                    "_id": device_lock_id,
                    "deviceId": _oid(device_id),
                    "createdAt": datetime.now(timezone.utc),
                    "expiresAt": expires_at,
                    **({"owner": owner} if owner is not None else {}),
                    **({"executionId": execution_id} if execution_id is not None else {}),
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
                    "expiresAt": expires_at,
                    **({"owner": owner} if owner is not None else {}),
                    **({"executionId": execution_id} if execution_id is not None else {}),
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
