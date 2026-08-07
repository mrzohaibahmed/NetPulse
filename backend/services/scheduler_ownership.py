"""
MongoDB-backed scheduler leader election (Phase 5).

Only the owning process may run monitoring (and other) scheduled jobs against
a shared database. Ownership heartbeats automatically; expired leases are
stolen by another instance — no manual cleanup required.
"""

from __future__ import annotations

import os
import socket
import uuid
from datetime import timedelta
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services.mongo_retry import assert_update_acknowledged, with_mongo_retry
from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("scheduler_ownership")

LOCK_COLLECTION = "scheduler_locks"
LOCK_ID = "monitor_scheduler"
# Must exceed a typical cycle; renewed during long cycles.
DEFAULT_TTL_SECONDS = int(os.getenv("SCHEDULER_LOCK_TTL_SECONDS", "90"))

# Stable per-process owner identity.
_OWNER_ID = (
    f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
)


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def get_owner_id() -> str:
    return _OWNER_ID


def get_lock_ttl_seconds() -> int:
    try:
        return max(int(os.getenv("SCHEDULER_LOCK_TTL_SECONDS", str(DEFAULT_TTL_SECONDS))), 15)
    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS


def ensure_scheduler_lock_indexes() -> None:
    """Idempotent index setup for the ownership document."""
    try:
        coll = _db()[LOCK_COLLECTION]
        # Unique _id is implicit; index on expiresAt aids ops queries.
        coll.create_index([("expiresAt", 1)], name="idx_scheduler_locks_expiresAt")
        logger.info("Scheduler ownership indexes ensured")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to ensure scheduler lock indexes: %s", exc)


def _expires_at(now):
    return now + timedelta(seconds=get_lock_ttl_seconds())


def try_acquire_or_renew() -> bool:
    """
    Become or remain the scheduler leader.

    Returns True when this process owns the lock after the call.
    """
    now = utc_now()
    expires = _expires_at(now)
    owner = get_owner_id()
    coll = _db()[LOCK_COLLECTION]
    payload = {
        "ownerId": owner,
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "heartbeatAt": now,
        "expiresAt": expires,
        "updatedAt": now,
    }

    def _renew_own():
        return coll.update_one(
            {"_id": LOCK_ID, "ownerId": owner},
            {"$set": payload},
        )

    renew = with_mongo_retry(
        _renew_own,
        action="scheduler_lock_renew",
    )
    if renew.matched_count:
        assert_update_acknowledged(
            renew,
            action="scheduler_lock_renew",
            require_matched=True,
        )
        return True

    def _steal_expired():
        return coll.find_one_and_update(
            {
                "_id": LOCK_ID,
                "$or": [
                    {"expiresAt": {"$lte": now}},
                    {"expiresAt": {"$exists": False}},
                ],
            },
            {
                "$set": {
                    **payload,
                    "acquiredAt": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    stolen = with_mongo_retry(
        _steal_expired,
        action="scheduler_lock_steal",
    )
    if stolen and stolen.get("ownerId") == owner:
        logger.info(
            "Scheduler ownership acquired (failover) | owner=%s | ttl=%ss",
            owner,
            get_lock_ttl_seconds(),
        )
        return True

    def _insert():
        try:
            return coll.insert_one(
                {
                    "_id": LOCK_ID,
                    **payload,
                    "acquiredAt": now,
                    "createdAt": now,
                }
            )
        except DuplicateKeyError:
            return None

    inserted = with_mongo_retry(_insert, action="scheduler_lock_insert")
    if inserted is not None:
        logger.info(
            "Scheduler ownership acquired (initial) | owner=%s | ttl=%ss",
            owner,
            get_lock_ttl_seconds(),
        )
        return True

    # Someone else holds a valid lease.
    current = coll.find_one({"_id": LOCK_ID}, {"ownerId": 1, "expiresAt": 1})
    logger.info(
        "Scheduler ownership held by peer | self=%s | peer=%s | expiresAt=%s",
        owner,
        (current or {}).get("ownerId"),
        (current or {}).get("expiresAt"),
    )
    return False


def is_scheduler_leader() -> bool:
    """Cheap check: renew if we own, else False."""
    try:
        return try_acquire_or_renew()
    except Exception as exc:  # noqa: BLE001
        # Fail closed: do not run duplicate jobs when lock store is unreachable.
        logger.error("Scheduler ownership check failed (skipping jobs): %s", exc)
        return False


def require_scheduler_leadership(job_name: str) -> bool:
    """
    Gate a scheduled job. Returns True when this process should run it.
    """
    if is_scheduler_leader():
        return True
    logger.info(
        "Skipping scheduled job — not scheduler leader | job=%s | owner=%s",
        job_name,
        get_owner_id(),
    )
    return False


def release_scheduler_ownership() -> None:
    """Best-effort release on clean shutdown so failover is immediate."""
    owner = get_owner_id()
    try:
        result = _db()[LOCK_COLLECTION].delete_one(
            {"_id": LOCK_ID, "ownerId": owner}
        )
        if result.deleted_count:
            logger.info("Scheduler ownership released | owner=%s", owner)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scheduler ownership release failed: %s", exc)


def ownership_status() -> dict[str, Any]:
    """Ops helper — current lock document + self identity."""
    doc = _db()[LOCK_COLLECTION].find_one({"_id": LOCK_ID}) or {}
    return {
        "selfOwnerId": get_owner_id(),
        "isLeader": doc.get("ownerId") == get_owner_id()
        and doc.get("expiresAt")
        and ensure_not_expired(doc),
        "lock": {
            "ownerId": doc.get("ownerId"),
            "hostname": doc.get("hostname"),
            "pid": doc.get("pid"),
            "heartbeatAt": doc.get("heartbeatAt"),
            "expiresAt": doc.get("expiresAt"),
        },
    }


def ensure_not_expired(doc: dict) -> bool:
    expires = doc.get("expiresAt")
    if expires is None:
        return False
    from utils.utc import ensure_utc  # noqa: PLC0415

    exp = ensure_utc(expires)
    return exp is not None and exp > utc_now()
