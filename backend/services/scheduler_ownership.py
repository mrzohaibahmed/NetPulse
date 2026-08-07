"""
MongoDB-backed scheduler leader election with cycle leadership guards.

Only the owning process may run monitoring (and other) scheduled jobs against
a shared database. Ownership heartbeats on elapsed time and device count;
expired leases are stolen atomically — no manual cleanup required.

Lease operations use atomic Mongo filters so renew cannot overwrite a peer
and release cannot delete another owner's lock.
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
from utils.utc import ensure_utc, utc_now

logger = get_monitor_logger("scheduler_ownership")

LOCK_COLLECTION = "scheduler_locks"
LOCK_ID = "monitor_scheduler"
DEFAULT_TTL_SECONDS = int(os.getenv("SCHEDULER_LOCK_TTL_SECONDS", "90"))

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


def get_heartbeat_interval_seconds() -> int:
    """Renew well before expiry — default one-third of TTL (min 5s)."""
    return max(get_lock_ttl_seconds() // 3, 5)


def ensure_scheduler_lock_indexes() -> None:
    """Idempotent index setup for the ownership document."""
    try:
        coll = _db()[LOCK_COLLECTION]
        coll.create_index([("expiresAt", 1)], name="idx_scheduler_locks_expiresAt")
        logger.info("Scheduler ownership indexes ensured")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to ensure scheduler lock indexes: %s", exc)


def _expires_at(now):
    return now + timedelta(seconds=get_lock_ttl_seconds())


def try_acquire_or_renew() -> bool:
    """
    Become or remain the scheduler leader.

    Returns True only when this process owns the lock after the call.
    All paths use atomic Mongo operations (filtered update / findAndModify / insert).
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

    # 1) Atomic renew — ownerId filter prevents overwriting a peer lease.
    def _renew_own():
        return coll.update_one(
            {"_id": LOCK_ID, "ownerId": owner},
            {"$set": payload},
        )

    renew = with_mongo_retry(
        _renew_own,
        action="scheduler_lock_renew",
        idempotent=True,
    )
    if renew.matched_count:
        assert_update_acknowledged(
            renew,
            action="scheduler_lock_renew",
            require_matched=True,
        )
        return True

    # 2) Atomic steal of expired / malformed lease (single document findAndModify).
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
        idempotent=True,
    )
    if stolen and stolen.get("ownerId") == owner:
        logger.info(
            "Scheduler ownership acquired (failover) | owner=%s | ttl=%ss",
            owner,
            get_lock_ttl_seconds(),
        )
        return True

    # 3) Initial insert — DuplicateKeyError means a peer won the startup race.
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

    inserted = with_mongo_retry(
        _insert,
        action="scheduler_lock_insert",
        idempotent=True,
    )
    if inserted is not None:
        logger.info(
            "Scheduler ownership acquired (initial) | owner=%s | ttl=%ss",
            owner,
            get_lock_ttl_seconds(),
        )
        return True

    current = coll.find_one({"_id": LOCK_ID}, {"ownerId": 1, "expiresAt": 1})
    logger.info(
        "Scheduler ownership held by peer | self=%s | peer=%s | expiresAt=%s",
        owner,
        (current or {}).get("ownerId"),
        (current or {}).get("expiresAt"),
    )
    return False


def is_scheduler_leader() -> bool:
    """Renew if we own; False on Mongo failure (fail closed)."""
    try:
        return try_acquire_or_renew()
    except Exception as exc:  # noqa: BLE001
        logger.error("Scheduler ownership check failed (skipping jobs): %s", exc)
        return False


def require_scheduler_leadership(job_name: str) -> bool:
    """Gate a scheduled job. Returns True when this process should run it."""
    if is_scheduler_leader():
        return True
    logger.info(
        "Skipping scheduled job — not scheduler leader | job=%s | owner=%s",
        job_name,
        get_owner_id(),
    )
    return False


def release_scheduler_ownership() -> None:
    """Best-effort release on clean shutdown — only deletes our own lease."""
    owner = get_owner_id()
    try:
        result = _db()[LOCK_COLLECTION].delete_one(
            {"_id": LOCK_ID, "ownerId": owner}
        )
        if result.deleted_count:
            logger.info("Scheduler ownership released | owner=%s", owner)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scheduler ownership release failed: %s", exc)


def ensure_not_expired(doc: dict) -> bool:
    expires = doc.get("expiresAt")
    if expires is None:
        return False
    exp = ensure_utc(expires)
    return exp is not None and exp > utc_now()


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


class CycleLeadershipGuard:
    """
    Per-cycle leadership watchdog (Phases 1–2).

    Renews on elapsed time (TTL/3) and every N devices. Any failed renew
    must abort the monitoring cycle — never continue without the lease.
    """

    def __init__(self, *, device_renew_every: int = 10, cycle_id: str | None = None):
        self.cycle_id = cycle_id
        self.device_renew_every = max(int(device_renew_every), 1)
        self.heartbeat_s = get_heartbeat_interval_seconds()
        self._last_renew_at = utc_now()
        self._devices_since_renew = 0
        self.aborted = False
        self.abort_reason: str | None = None

    def ensure(self, *, force: bool = False, reason: str = "heartbeat") -> bool:
        """
        Return True while we still own the lease.

        On failure sets ``aborted`` and returns False — caller must stop.
        """
        if self.aborted:
            return False

        now = utc_now()
        elapsed = (now - self._last_renew_at).total_seconds()
        due_time = elapsed >= self.heartbeat_s
        due_devices = self._devices_since_renew >= self.device_renew_every

        if not force and not due_time and not due_devices:
            return True

        owned = try_acquire_or_renew()
        if not owned:
            self.aborted = True
            self.abort_reason = (
                f"leadership_lost:{reason}|elapsed={elapsed:.1f}s|"
                f"devices_since_renew={self._devices_since_renew}|"
                f"cycleId={self.cycle_id}"
            )
            logger.error(
                "Monitoring cycle abort — scheduler leadership lost | %s",
                self.abort_reason,
            )
            return False

        self._last_renew_at = utc_now()
        self._devices_since_renew = 0
        logger.debug(
            "Leadership heartbeat ok | cycleId=%s | reason=%s | heartbeat_s=%s",
            self.cycle_id,
            reason,
            self.heartbeat_s,
        )
        return True

    def note_device_visited(self) -> None:
        """Count a loop iteration toward device-based renewal."""
        self._devices_since_renew += 1
