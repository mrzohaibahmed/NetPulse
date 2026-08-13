"""
MongoDB-backed scheduler leader election with cycle leadership guards.

Only the owning process may run monitoring (and other) scheduled jobs against
a shared database. Ownership heartbeats on elapsed time and device count;
expired leases are stolen atomically — no manual cleanup required.

Lease operations use atomic Mongo filters so renew cannot overwrite a peer
and release cannot delete another owner's lock.

Internal timestamps are timezone-aware UTC only (see utils.utc).
"""

from __future__ import annotations

import os
import socket
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services.mongo_retry import assert_update_acknowledged, with_mongo_retry
from utils.monitor_logger import get_monitor_logger
from utils.utc import ensure_utc, format_utc, require_utc_aware, utc_now

logger = get_monitor_logger("scheduler_ownership")

LOCK_COLLECTION = "scheduler_locks"
LOCK_ID = "monitor_scheduler"
DEFAULT_TTL_SECONDS = int(os.getenv("SCHEDULER_LOCK_TTL_SECONDS", "90"))

# Process-unique identity: hostname:pid:instance_uuid (UUID fixed at import).
_OWNER_ID = (
    f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
)

# Tracks whether this process previously held leadership (for loss logging).
_held_leadership = False
_held_lock = threading.Lock()


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


def _expires_at(now: datetime) -> datetime:
    now = require_utc_aware(now, field="now")
    return now + timedelta(seconds=get_lock_ttl_seconds())


def _lease_payload(now: datetime, owner: str) -> dict[str, Any]:
    """UTC-aware fields written on renew / steal / insert."""
    now = require_utc_aware(now, field="now")
    return {
        "ownerId": owner,
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "heartbeatAt": now,
        "expiresAt": _expires_at(now),
        "updatedAt": now,
    }


def _mark_held(held: bool) -> None:
    global _held_leadership
    with _held_lock:
        _held_leadership = held


def _was_held() -> bool:
    with _held_lock:
        return _held_leadership


def _log_peer_or_lost(owner: str, current: dict | None, *, reason: str) -> None:
    peer = (current or {}).get("ownerId")
    expires = (current or {}).get("expiresAt")
    heartbeat = (current or {}).get("heartbeatAt")
    now_s = format_utc(utc_now())
    if _was_held() and peer != owner:
        _mark_held(False)
        logger.error(
            "Scheduler leadership lost | ts=%s | self=%s | peer=%s | "
            "heartbeatAt=%s | expiresAt=%s | reason=%s",
            now_s,
            owner,
            peer,
            format_utc(ensure_utc(heartbeat)) if isinstance(heartbeat, datetime) else heartbeat,
            format_utc(ensure_utc(expires)) if isinstance(expires, datetime) else expires,
            reason,
        )
        return

    logger.info(
        "Scheduler ownership held by peer | ts=%s | self=%s | peer=%s | "
        "heartbeatAt=%s | expiresAt=%s | leadership=follower",
        now_s,
        owner,
        peer,
        format_utc(ensure_utc(heartbeat)) if isinstance(heartbeat, datetime) else heartbeat,
        format_utc(ensure_utc(expires)) if isinstance(expires, datetime) else expires,
    )


def try_acquire_or_renew() -> bool:
    """
    Become or remain the scheduler leader.

    Returns True only when this process owns the lock after the call.
    All paths use atomic Mongo operations (filtered update / findAndModify / insert).

    Same-process concurrent callers that lose an insert/steal race against
    *this* ownerId retry renew so they do not falsely report follower status.
    """
    now = utc_now()
    owner = get_owner_id()
    coll = _db()[LOCK_COLLECTION]
    payload = _lease_payload(now, owner)

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
        _mark_held(True)
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
        _mark_held(True)
        logger.info(
            "Scheduler ownership acquired (failover) | ts=%s | self=%s | "
            "ttl=%ss | expiresAt=%s | leadership=leader",
            format_utc(now),
            owner,
            get_lock_ttl_seconds(),
            format_utc(payload["expiresAt"]),
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
        _mark_held(True)
        logger.info(
            "Scheduler ownership acquired (initial) | ts=%s | self=%s | "
            "ttl=%ss | expiresAt=%s | leadership=leader",
            format_utc(now),
            owner,
            get_lock_ttl_seconds(),
            format_utc(payload["expiresAt"]),
        )
        return True

    # 4) Same-process race recovery: a concurrent caller may have just
    # inserted/stolen as *this* ownerId. Retry renew before conceding.
    renew_again = with_mongo_retry(
        _renew_own,
        action="scheduler_lock_renew_race",
        idempotent=True,
    )
    if renew_again.matched_count:
        _mark_held(True)
        return True

    current = coll.find_one(
        {"_id": LOCK_ID},
        {"ownerId": 1, "expiresAt": 1, "heartbeatAt": 1},
    )
    if current and current.get("ownerId") == owner and ensure_not_expired(current):
        _mark_held(True)
        return True

    _log_peer_or_lost(owner, current, reason="acquire_or_renew_failed")
    return False


def is_scheduler_leader() -> bool:
    """Renew if we own; False on Mongo failure (fail closed)."""
    try:
        return try_acquire_or_renew()
    except Exception as exc:  # noqa: BLE001
        if _was_held():
            _mark_held(False)
            logger.error(
                "Scheduler leadership lost | ts=%s | self=%s | reason=mongo_failure | error=%s",
                format_utc(utc_now()),
                get_owner_id(),
                exc,
            )
        else:
            logger.error(
                "Scheduler ownership check failed (skipping jobs) | ts=%s | self=%s | error=%s",
                format_utc(utc_now()),
                get_owner_id(),
                exc,
            )
        return False


def require_scheduler_leadership(job_name: str) -> bool:
    """Gate a scheduled job. Returns True when this process should run it."""
    if is_scheduler_leader():
        return True
    logger.info(
        "Skipping scheduled job — not scheduler leader | ts=%s | job=%s | self=%s",
        format_utc(utc_now()),
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
            _mark_held(False)
            logger.info(
                "Scheduler ownership released | ts=%s | self=%s",
                format_utc(utc_now()),
                owner,
            )
        else:
            logger.info(
                "Scheduler ownership release skipped (not owner or absent) | "
                "ts=%s | self=%s",
                format_utc(utc_now()),
                owner,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Scheduler ownership release failed | ts=%s | self=%s | error=%s",
            format_utc(utc_now()),
            owner,
            exc,
        )


def ensure_not_expired(doc: dict) -> bool:
    expires = doc.get("expiresAt")
    if expires is None:
        return False
    if not isinstance(expires, datetime):
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
            "acquiredAt": doc.get("acquiredAt"),
            "createdAt": doc.get("createdAt"),
            "updatedAt": doc.get("updatedAt"),
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
                "Monitoring cycle abort — scheduler leadership lost | ts=%s | %s",
                format_utc(utc_now()),
                self.abort_reason,
            )
            return False

        self._last_renew_at = utc_now()
        self._devices_since_renew = 0
        logger.debug(
            "Leadership heartbeat ok | ts=%s | cycleId=%s | reason=%s | heartbeat_s=%s",
            format_utc(utc_now()),
            self.cycle_id,
            reason,
            self.heartbeat_s,
        )
        return True

    def note_device_visited(self) -> None:
        """Count a loop iteration toward device-based renewal."""
        self._devices_since_renew += 1


def get_scheduler_status() -> dict[str, Any]:
    """Safe scheduler ownership snapshot for health/metrics (no secrets)."""
    owner = get_owner_id()
    try:
        doc = _db()[LOCK_COLLECTION].find_one({"_id": LOCK_ID}) or {}
    except Exception as exc:  # noqa: BLE001
        return {
            "self": owner,
            "isLeader": False,
            "error": "mongo_unavailable",
            "detail": type(exc).__name__,
        }

    peer = doc.get("ownerId")
    is_leader = bool(peer == owner and ensure_not_expired(doc))
    return {
        "self": owner,
        "peer": peer,
        "isLeader": is_leader,
        "heartbeatAt": format_utc(ensure_utc(doc.get("heartbeatAt")))
        if doc.get("heartbeatAt")
        else None,
        "expiresAt": format_utc(ensure_utc(doc.get("expiresAt")))
        if doc.get("expiresAt")
        else None,
        "ttlSeconds": get_lock_ttl_seconds(),
    }
