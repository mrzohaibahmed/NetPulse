"""
Bounded login brute-force protection (username + IP).

State is kept in MongoDB with a TTL index so records expire automatically.
Successful logins clear the relevant counters.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("auth.rate_limit")

COLLECTION = "login_rate_limits"
_INDEXES_ENSURED = False

# Defaults: 8 failures → 15 minute lockout; window tracks last 15 minutes of attempts.
DEFAULT_MAX_FAILURES = 8
DEFAULT_WINDOW_SECONDS = 15 * 60
DEFAULT_LOCKOUT_SECONDS = 15 * 60


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def get_max_failures() -> int:
    return _int_env("LOGIN_MAX_FAILURES", DEFAULT_MAX_FAILURES)


def get_window_seconds() -> int:
    return _int_env("LOGIN_WINDOW_SECONDS", DEFAULT_WINDOW_SECONDS)


def get_lockout_seconds() -> int:
    return _int_env("LOGIN_LOCKOUT_SECONDS", DEFAULT_LOCKOUT_SECONDS)


def _normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def _normalize_ip(ip: str | None) -> str:
    value = (ip or "").strip()
    return value or "unknown"


def _bucket_key(username: str, ip: str) -> str:
    """
    Combined username+IP key so attackers must rotate both dimensions.

    Username-only and IP-only secondary counters also apply (see check_login_allowed).
    """
    material = f"{_normalize_username(username)}|{_normalize_ip(ip)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _username_key(username: str) -> str:
    return "u:" + hashlib.sha256(_normalize_username(username).encode("utf-8")).hexdigest()


def _ip_key(ip: str) -> str:
    return "i:" + hashlib.sha256(_normalize_ip(ip).encode("utf-8")).hexdigest()


def ensure_login_rate_limit_indexes() -> None:
    """Idempotent TTL + key indexes for login_rate_limits."""
    global _INDEXES_ENSURED
    if _INDEXES_ENSURED:
        return
    try:
        coll = _db()[COLLECTION]
        coll.create_index([("key", 1)], unique=True, name="uniq_login_rate_key")
        # expireAfterSeconds=0 deletes when expiresAt is in the past.
        coll.create_index(
            [("expiresAt", 1)],
            name="idx_login_rate_expiresAt_ttl",
            expireAfterSeconds=0,
        )
        _INDEXES_ENSURED = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to ensure login rate-limit indexes: %s", exc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load(key: str) -> dict[str, Any] | None:
    ensure_login_rate_limit_indexes()
    return _db()[COLLECTION].find_one({"key": key})


def _is_locked(doc: dict[str, Any] | None, now: datetime) -> bool:
    if not doc:
        return False
    locked_until = doc.get("lockedUntil")
    if locked_until is None:
        return False
    if getattr(locked_until, "tzinfo", None) is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    return locked_until > now


def check_login_allowed(username: str, ip: str | None) -> tuple[bool, int]:
    """
    Return (allowed, retry_after_seconds).

    Checks combined username+IP, username-only, and IP-only buckets.
    """
    now = _now()
    keys = (
        _bucket_key(username, ip or ""),
        _username_key(username),
        _ip_key(ip or ""),
    )
    retry_after = 0
    for key in keys:
        doc = _load(key)
        if _is_locked(doc, now):
            locked_until = doc["lockedUntil"]
            if getattr(locked_until, "tzinfo", None) is None:
                locked_until = locked_until.replace(tzinfo=timezone.utc)
            retry_after = max(retry_after, int((locked_until - now).total_seconds()))
    if retry_after > 0:
        return False, max(retry_after, 1)
    return True, 0


def record_login_failure(username: str, ip: str | None) -> dict[str, Any]:
    """Increment failure counters; lock when threshold exceeded."""
    ensure_login_rate_limit_indexes()
    now = _now()
    window = get_window_seconds()
    lockout = get_lockout_seconds()
    max_failures = get_max_failures()
    expires = now + timedelta(seconds=max(window, lockout) + 60)

    results: dict[str, Any] = {"locked": False, "retryAfterSeconds": 0}

    for key, weight in (
        (_bucket_key(username, ip or ""), 1),
        (_username_key(username), 1),
        (_ip_key(ip or ""), 1),
    ):
        coll = _db()[COLLECTION]
        doc = coll.find_one({"key": key})
        failures = 0
        window_started = now
        if doc:
            started = doc.get("windowStartedAt") or now
            if getattr(started, "tzinfo", None) is None:
                started = started.replace(tzinfo=timezone.utc)
            if (now - started).total_seconds() <= window:
                failures = int(doc.get("failures") or 0)
                window_started = started
            else:
                failures = 0
                window_started = now

        failures += weight
        update: dict[str, Any] = {
            "key": key,
            "failures": failures,
            "windowStartedAt": window_started,
            "updatedAt": now,
            "expiresAt": expires,
        }
        if failures >= max_failures:
            locked_until = now + timedelta(seconds=lockout)
            update["lockedUntil"] = locked_until
            results["locked"] = True
            results["retryAfterSeconds"] = max(
                int(results["retryAfterSeconds"]), lockout
            )
            logger.warning(
                "Login rate limit lockout | keyPrefix=%s | failures=%s",
                key[:12],
                failures,
            )
        else:
            # Clear stale lock if under threshold in a fresh window.
            update["lockedUntil"] = None

        coll.update_one({"key": key}, {"$set": update}, upsert=True)

    return results


def clear_login_failures(username: str, ip: str | None) -> None:
    """Clear counters after a successful authentication."""
    ensure_login_rate_limit_indexes()
    keys = [
        _bucket_key(username, ip or ""),
        _username_key(username),
        _ip_key(ip or ""),
    ]
    try:
        _db()[COLLECTION].delete_many({"key": {"$in": keys}})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to clear login rate-limit state: %s", exc)
