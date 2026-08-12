"""
Atomic per-device scan claims for the dispatch monitoring architecture.

Semantics:
  - Missing ``nextCheckAt`` ⇒ device is due.
  - Missing claim expiry fields ⇒ device is unclaimed.
  - Claim does not touch ``lastCheckedAt`` or ``lastPingStartedAt``.
  - ``nextCheckAt`` advances from the previous deadline when possible
    (``previous_nextCheckAt + pingInterval``) to avoid completion-time drift.
  - Overdue policy: if that computed next deadline is still <= claim_now,
    schedule ``claim_now + pingInterval`` (skip catch-up storms).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from typing import Any

from pymongo import ReturnDocument

from services.mongo_retry import assert_update_acknowledged, with_mongo_retry
from services.settings_service import get_ping_config
from utils.monitor_logger import get_monitor_logger
from utils.utc import ensure_utc, utc_now

logger = get_monitor_logger("monitor_claim")

CLAIM_TTL_FLOOR_SECONDS = 15
CLAIM_TTL_SAFETY_SECONDS = 10


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def compute_claim_ttl_seconds(device: dict[str, Any] | None = None) -> float:
    """
    Claim lease duration in seconds.

    Base: ``max(15, (timeout_ms/1000) * retries + 10)`` using the device's ping
    config. Optional env ``PING_CLAIM_TTL`` raises the floor when set (seconds).

    Intentionally independent of ``pingInterval``: TTL must cover worst-case
    ICMP execution (timeout × retries) plus apply/scheduling slack, not the
    monitoring cadence.
    """
    config = get_ping_config(device)
    timeout_ms = max(int(config["timeout_ms"]), 100)
    retries = max(int(config["retries"]), 1)
    worst_ping_s = (timeout_ms / 1000.0) * retries
    computed = float(max(CLAIM_TTL_FLOOR_SECONDS, worst_ping_s + CLAIM_TTL_SAFETY_SECONDS))

    raw = os.getenv("PING_CLAIM_TTL")
    if raw is None or str(raw).strip() == "":
        return computed
    try:
        configured = float(raw)
    except (TypeError, ValueError):
        return computed
    if configured <= 0:
        return computed
    return float(max(computed, configured))


def compute_next_check_at(
    *,
    claim_now: datetime,
    previous_next_check_at: datetime | None,
    interval_seconds: int,
) -> datetime:
    """
    Authoritative schedule progression for a successful claim.

    Policy:
      1. Prefer ``previous_nextCheckAt + interval`` (deadline-based cadence).
      2. If previous deadline is missing, use ``claim_now + interval``.
      3. If the device is substantially overdue so that (1) is still <= claim_now,
         use ``claim_now + interval`` — one catch-up at most; no burst of
         back-to-back catch-up claims after restart or prolonged outage.
    """
    interval_s = max(int(interval_seconds), 1)
    if previous_next_check_at is None:
        return claim_now + timedelta(seconds=interval_s)

    candidate = previous_next_check_at + timedelta(seconds=interval_s)
    if candidate <= claim_now:
        return claim_now + timedelta(seconds=interval_s)
    return candidate


def is_claim_active(device: dict[str, Any] | None, *, now=None) -> bool:
    """True when the device document holds a non-expired scan claim."""
    if not device:
        return False
    expires = device.get("scanClaimExpiresAt")
    if expires is None and not device.get("scanClaimId"):
        return False
    if not device.get("scanClaimId"):
        return False
    if expires is None:
        # Claim id without expiry — treat as active until explicitly cleared.
        return True

    exp = ensure_utc(expires)
    if exp is None:
        return True
    stamp = now or utc_now()
    return exp > stamp


def build_claimable_filter(device_id: Any, now) -> dict[str, Any]:
    """Mongo filter: monitored, due, and unclaimed (or claim expired) for one device."""
    return {
        "_id": device_id,
        **build_due_unclaimed_filter(now),
    }


def build_due_unclaimed_filter(now) -> dict[str, Any]:
    """
    Candidate query for the dispatcher (no ``_id``).

    Missing ``nextCheckAt`` / claim expiry fields mean due / unclaimed.
    """
    return {
        "monitor": True,
        "$and": [
            {
                "$or": [
                    {"nextCheckAt": {"$exists": False}},
                    {"nextCheckAt": None},
                    {"nextCheckAt": {"$lte": now}},
                ]
            },
            {
                "$or": [
                    {"scanClaimExpiresAt": {"$exists": False}},
                    {"scanClaimExpiresAt": None},
                    {"scanClaimExpiresAt": {"$lte": now}},
                ]
            },
        ],
    }


def claim_device(
    device_id: Any,
    *,
    device: dict[str, Any] | None = None,
    now=None,
) -> dict[str, Any] | None:
    """
    Atomically claim a due, unclaimed monitored device for one scan.

    Returns the updated device document on success, or ``None`` when another
    claimant won the race / the device is not due / not monitored.
    """
    claim_now = now or utc_now()
    config = get_ping_config(device)
    interval_s = max(int(config["interval"]), 1)
    ttl_s = compute_claim_ttl_seconds(device)
    claim_id = uuid.uuid4().hex

    previous_deadline = None
    if device is not None:
        previous_deadline = ensure_utc(device.get("nextCheckAt"))

    next_check_at = compute_next_check_at(
        claim_now=claim_now,
        previous_next_check_at=previous_deadline,
        interval_seconds=interval_s,
    )
    expires_at = claim_now + timedelta(seconds=ttl_s)

    filt = build_claimable_filter(device_id, claim_now)
    update = {
        "$set": {
            "scanClaimId": claim_id,
            "scanClaimedAt": claim_now,
            "scanClaimExpiresAt": expires_at,
            "nextCheckAt": next_check_at,
        }
    }

    def _claim_once():
        return _db().devices.find_one_and_update(
            filt,
            update,
            return_document=ReturnDocument.AFTER,
        )

    try:
        updated = with_mongo_retry(
            _claim_once,
            action="device_scan_claim",
            device_id=device_id,
            ip_address=(device or {}).get("ipAddress"),
            idempotent=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Device scan claim failed | deviceId=%s | error=%s",
            device_id,
            exc,
        )
        raise

    if updated is None:
        logger.debug(
            "Device scan claim not acquired | deviceId=%s",
            device_id,
        )
        return None

    logger.info(
        "Device scan claimed | deviceId=%s | claimId=%s | claimedAt=%s | "
        "nextCheckAt=%s | scanClaimExpiresAt=%s | ttlSeconds=%.1f | "
        "intervalSeconds=%s | previousNextCheckAt=%s",
        device_id,
        claim_id,
        claim_now.isoformat(),
        next_check_at.isoformat(),
        expires_at.isoformat(),
        ttl_s,
        interval_s,
        previous_deadline.isoformat() if previous_deadline else None,
    )
    return updated


def release_device_claim(device_id: Any, claim_id: str) -> bool:
    """
    Release a claim only when ``scanClaimId`` still matches.

    Clears claim fields; leaves ``nextCheckAt`` intact. Wrong/missing claim id
    is a successful no-op (returns False).
    """
    if not claim_id:
        return False

    def _release_once():
        return _db().devices.update_one(
            {"_id": device_id, "scanClaimId": claim_id},
            {
                "$unset": {
                    "scanClaimId": "",
                    "scanClaimedAt": "",
                    "scanClaimExpiresAt": "",
                }
            },
        )

    try:
        result = with_mongo_retry(
            _release_once,
            action="device_scan_claim_release",
            device_id=device_id,
            idempotent=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Device scan claim release failed | deviceId=%s | claimId=%s | error=%s",
            device_id,
            claim_id,
            exc,
        )
        raise

    # Unmatched claim id is an expected no-op — do not treat as hard failure.
    assert_update_acknowledged(
        result,
        action="device_scan_claim_release",
        device_id=device_id,
        require_matched=False,
    )
    released = int(getattr(result, "matched_count", 0) or 0) == 1
    if released:
        logger.info(
            "Device scan claim released | deviceId=%s | claimId=%s",
            device_id,
            claim_id,
        )
    else:
        logger.debug(
            "Device scan claim release no-op | deviceId=%s | claimId=%s",
            device_id,
            claim_id,
        )
    return released
