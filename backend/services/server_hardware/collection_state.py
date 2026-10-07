"""
HPE iLO Collection State Persistence Primitives (Phase 4A Step 1).

Provides atomic, race-safe storage for operational iLO collection execution state
on `db.devices` (`iloCollectionState` sub-document).

Does NOT perform HTTP requests, Redfish parsing, collector loops, or hardware-health evaluation.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo.errors import PyMongoError

from config.database import db
from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("server_hardware.collection_state")

ALLOWED_POLL_STATUSES = {
    "NEVER_POLLED",
    "SUCCESS",
    "AUTHENTICATION_ERROR",
    "TIMEOUT",
    "TLS_ERROR",
    "CONNECTION_ERROR",
    "REDFISH_ERROR",
    "UNKNOWN_ERROR",
}

MAX_ERROR_LENGTH = 500

# Regex patterns for sanitizing sensitive credentials, headers, and tokens from error strings
REDACTION_PATTERNS = [
    (re.compile(r"(?i)(password|passwd|pwd|secret|auth|token|key|api_key)\s*[:=]\s*['\"]?[^\s'\";,]+['\"]?"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)authorization\s*:\s*(basic|bearer)\s+[^\s]+"), r"Authorization: [REDACTED]"),
    (re.compile(r"(?i)x-auth-token\s*:\s*[^\s]+"), r"X-Auth-Token: [REDACTED]"),
    (re.compile(r"(?i)bearer\s+[a-zA-Z0-9._\-]+"), r"Bearer [REDACTED]"),
    (re.compile(r"(?i)basic\s+[a-zA-Z0-9+/=]+"), r"Basic [REDACTED]"),
]


class CollectionStateError(Exception):
    """Raised when a collection-state persistence or normalization operation fails."""


def _normalize_device_id(device_id: str | ObjectId) -> ObjectId:
    """Normalize device identifier to BSON ObjectId."""
    if isinstance(device_id, ObjectId):
        return device_id
    if isinstance(device_id, str) and ObjectId.is_valid(device_id):
        return ObjectId(device_id)
    raise CollectionStateError(f"Invalid device identifier format: {device_id}")


def sanitize_error_message(error: str | Exception | None) -> str | None:
    """
    Sanitize and truncate error strings for safe persistence and display.

    Redacts credentials, passwords, tokens, auth headers, and raw secrets.
    Limits text length to MAX_ERROR_LENGTH characters.
    """
    if error is None:
        return None

    error_str = str(error).strip()
    if not error_str:
        return None

    # Apply redaction patterns
    for pattern, replacement in REDACTION_PATTERNS:
        error_str = pattern.sub(replacement, error_str)

    # Truncate to MAX_ERROR_LENGTH if necessary
    if len(error_str) > MAX_ERROR_LENGTH:
        error_str = error_str[: MAX_ERROR_LENGTH - 3] + "..."

    return error_str


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Ensure datetime object is timezone-aware in UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def record_ilo_poll_attempt(
    device_id: str | ObjectId,
    attempt_at: datetime | None = None,
    *,
    database: Any = None,
) -> bool:
    """
    Register the start of an iLO collection attempt for device_id.

    Atomic and race-safe. Out-of-order protection ensures an older attempt cannot
    overwrite a newer attempt's lastAttemptAt timestamp.
    """
    target_oid = _normalize_device_id(device_id)
    attempt_time = _ensure_utc(attempt_at) or utc_now()
    now = utc_now()

    target_db = database if database is not None else db

    query = {
        "_id": target_oid,
        "$or": [
            {"iloCollectionState.lastAttemptAt": {"$lte": attempt_time}},
            {"iloCollectionState": {"$exists": False}},
            {"iloCollectionState.lastAttemptAt": None},
        ],
    }

    update = {
        "$set": {
            "iloCollectionState.lastAttemptAt": attempt_time,
            "iloCollectionState.updatedAt": now,
        }
    }

    try:
        res = target_db.devices.update_one(query, update)
        if res.matched_count > 0:
            logger.info(
                "[COLLECTION_STATE] Registered poll attempt | deviceId=%s | attemptAt=%s",
                target_oid,
                attempt_time,
            )
            return True

        logger.info(
            "[COLLECTION_STATE] Poll attempt registration ignored (newer attempt exists) | deviceId=%s | attemptAt=%s",
            target_oid,
            attempt_time,
        )
        return False
    except PyMongoError as exc:
        raise CollectionStateError(
            f"Failed to record poll attempt for device {target_oid}: {exc}"
        ) from exc


def record_ilo_poll_success(
    device_id: str | ObjectId,
    attempt_at: datetime,
    success_at: datetime | None = None,
    *,
    database: Any = None,
) -> bool:
    """
    Record a successful complete iLO collection result for device_id.

    Resets consecutiveFailures to 0, sets lastPollStatus="SUCCESS", clears lastError,
    and updates lastSuccessAt. Protected against out-of-order execution.
    """
    target_oid = _normalize_device_id(device_id)
    attempt_time = _ensure_utc(attempt_at)
    if attempt_time is None:
        raise CollectionStateError("attempt_at must be a valid datetime object")

    success_time = _ensure_utc(success_at) or utc_now()
    now = utc_now()

    target_db = database if database is not None else db

    query = {
        "_id": target_oid,
        "$or": [
            {"iloCollectionState.lastAttemptAt": {"$lte": attempt_time}},
            {"iloCollectionState": {"$exists": False}},
            {"iloCollectionState.lastAttemptAt": None},
        ],
    }

    update = {
        "$set": {
            "iloCollectionState.lastAttemptAt": attempt_time,
            "iloCollectionState.lastSuccessAt": success_time,
            "iloCollectionState.lastPollStatus": "SUCCESS",
            "iloCollectionState.consecutiveFailures": 0,
            "iloCollectionState.lastError": None,
            "iloCollectionState.updatedAt": now,
        }
    }

    try:
        res = target_db.devices.update_one(query, update)
        if res.matched_count > 0:
            logger.info(
                "[COLLECTION_STATE] Recorded poll success | deviceId=%s | successAt=%s",
                target_oid,
                success_time,
            )
            return True

        logger.info(
            "[COLLECTION_STATE] Poll success update ignored (newer attempt exists) | deviceId=%s | attemptAt=%s",
            target_oid,
            attempt_time,
        )
        return False
    except PyMongoError as exc:
        raise CollectionStateError(
            f"Failed to record poll success for device {target_oid}: {exc}"
        ) from exc


def record_ilo_poll_failure(
    device_id: str | ObjectId,
    attempt_at: datetime,
    error_message: str | Exception,
    status: str = "UNKNOWN_ERROR",
    failure_at: datetime | None = None,
    *,
    database: Any = None,
) -> bool:
    """
    Record a failed iLO collection attempt for device_id.

    Increments consecutiveFailures, sets lastPollStatus, saves sanitized lastError,
    and updates lastFailureAt. Preserves previous lastSuccessAt intact. Protected against out-of-order execution.
    """
    target_oid = _normalize_device_id(device_id)
    attempt_time = _ensure_utc(attempt_at)
    if attempt_time is None:
        raise CollectionStateError("attempt_at must be a valid datetime object")

    if status not in ALLOWED_POLL_STATUSES:
        status = "UNKNOWN_ERROR"

    failure_time = _ensure_utc(failure_at) or utc_now()
    sanitized_err = sanitize_error_message(error_message)
    now = utc_now()

    target_db = database if database is not None else db

    query = {
        "_id": target_oid,
        "$or": [
            {"iloCollectionState.lastAttemptAt": {"$lte": attempt_time}},
            {"iloCollectionState": {"$exists": False}},
            {"iloCollectionState.lastAttemptAt": None},
        ],
    }

    update = {
        "$set": {
            "iloCollectionState.lastAttemptAt": attempt_time,
            "iloCollectionState.lastFailureAt": failure_time,
            "iloCollectionState.lastPollStatus": status,
            "iloCollectionState.lastError": sanitized_err,
            "iloCollectionState.updatedAt": now,
        },
        "$inc": {
            "iloCollectionState.consecutiveFailures": 1,
        },
    }

    try:
        res = target_db.devices.update_one(query, update)
        if res.matched_count > 0:
            logger.info(
                "[COLLECTION_STATE] Recorded poll failure | deviceId=%s | status=%s | failureAt=%s",
                target_oid,
                status,
                failure_time,
            )
            return True

        logger.info(
            "[COLLECTION_STATE] Poll failure update ignored (newer attempt exists) | deviceId=%s | attemptAt=%s",
            target_oid,
            attempt_time,
        )
        return False
    except PyMongoError as exc:
        raise CollectionStateError(
            f"Failed to record poll failure for device {target_oid}: {exc}"
        ) from exc


def get_ilo_collection_state(
    device_id: str | ObjectId,
    *,
    database: Any = None,
) -> dict[str, Any]:
    """
    Retrieve operational iLO collection state for device_id from db.devices.

    If device has no iloCollectionState, returns default NEVER_POLLED dict.
    Raises CollectionStateError if device is not found in database.
    """
    target_oid = _normalize_device_id(device_id)
    target_db = database if database is not None else db

    try:
        device = target_db.devices.find_one({"_id": target_oid}, {"iloCollectionState": 1})
    except PyMongoError as exc:
        raise CollectionStateError(
            f"Failed to fetch collection state for device {target_oid}: {exc}"
        ) from exc

    if not device:
        raise CollectionStateError(f"Device not found: {target_oid}")

    raw_state = device.get("iloCollectionState")
    if not isinstance(raw_state, dict):
        return {
            "lastAttemptAt": None,
            "lastSuccessAt": None,
            "lastFailureAt": None,
            "lastPollStatus": "NEVER_POLLED",
            "consecutiveFailures": 0,
            "lastError": None,
            "updatedAt": None,
        }

    status = raw_state.get("lastPollStatus")
    if status not in ALLOWED_POLL_STATUSES:
        status = "NEVER_POLLED" if not status else "UNKNOWN_ERROR"

    return {
        "lastAttemptAt": _ensure_utc(raw_state.get("lastAttemptAt")),
        "lastSuccessAt": _ensure_utc(raw_state.get("lastSuccessAt")),
        "lastFailureAt": _ensure_utc(raw_state.get("lastFailureAt")),
        "lastPollStatus": status,
        "consecutiveFailures": int(raw_state.get("consecutiveFailures") or 0),
        "lastError": sanitize_error_message(raw_state.get("lastError")),
        "updatedAt": _ensure_utc(raw_state.get("updatedAt")),
    }
