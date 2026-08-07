"""
MongoDB write helpers with result verification and safe retry policy.

Retry classification (Phase 4):
  Safe to retry (idempotent=True):
    - Pure $set updates with attempt-id compare-and-set
    - Lock renew/steal/insert (atomic filters / DuplicateKeyError)
    - Inserts with predetermined unique attemptId (DuplicateKeyError = success)
  Unsafe without idempotency token (idempotent=False → single attempt):
    - Bare $inc
    - Bare insert_one without unique key
  Conditionally safe:
    - find_one_and_update with attempt-id filter + post-read recovery
      (wrapped as an idempotent function, then retries are allowed)
"""

from __future__ import annotations

import time
from typing import Any, Callable, TypeVar

from pymongo.errors import (
    AutoReconnect,
    ConnectionFailure,
    NetworkTimeout,
    ServerSelectionTimeoutError,
)

from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("mongo_retry")

T = TypeVar("T")

_TRANSIENT = (
    AutoReconnect,
    ConnectionFailure,
    NetworkTimeout,
    ServerSelectionTimeoutError,
)


def with_mongo_retry(
    operation: Callable[[], T],
    *,
    action: str,
    device_id: Any = None,
    ip_address: str | None = None,
    max_attempts: int = 5,
    base_delay_s: float = 0.25,
    idempotent: bool = False,
) -> T:
    """
    Execute ``operation`` with exponential backoff on transient Mongo errors.

    Non-idempotent operations are attempted exactly once (no blind retry).
    Non-transient exceptions always propagate immediately after logging.
    """
    attempts = max_attempts if idempotent else 1
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except _TRANSIENT as exc:
            last_error = exc
            if not idempotent or attempt >= attempts:
                logger.error(
                    "Mongo transient failure (no retry) | action=%s | "
                    "idempotent=%s | attempt=%s/%s | deviceId=%s | ip=%s | error=%s",
                    action,
                    idempotent,
                    attempt,
                    attempts,
                    device_id,
                    ip_address,
                    exc,
                )
                break
            delay = base_delay_s * (2 ** (attempt - 1))
            logger.warning(
                "Mongo transient failure | action=%s | attempt=%s/%s | "
                "deviceId=%s | ip=%s | retry_in=%.2fs | error=%s",
                action,
                attempt,
                attempts,
                device_id,
                ip_address,
                delay,
                exc,
            )
            time.sleep(delay)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Mongo write failed | action=%s | deviceId=%s | ip=%s | error=%s",
                action,
                device_id,
                ip_address,
                exc,
            )
            raise

    logger.error(
        "Mongo retries exhausted | action=%s | deviceId=%s | ip=%s | error=%s",
        action,
        device_id,
        ip_address,
        last_error,
    )
    assert last_error is not None
    raise last_error


def assert_update_acknowledged(
    result: Any,
    *,
    action: str,
    device_id: Any = None,
    ip_address: str | None = None,
    require_matched: bool = True,
) -> bool:
    """Verify an UpdateResult was acknowledged."""
    if getattr(result, "acknowledged", True) is False:
        logger.error(
            "Mongo write NOT acknowledged | action=%s | deviceId=%s | ip=%s",
            action,
            device_id,
            ip_address,
        )
        raise RuntimeError(f"Mongo write not acknowledged: {action}")

    matched = getattr(result, "matched_count", None)
    modified = getattr(result, "modified_count", None)
    upserted = getattr(result, "upserted_id", None)

    logger.info(
        "Mongo write ok | action=%s | deviceId=%s | ip=%s | "
        "matched=%s | modified=%s | upserted=%s",
        action,
        device_id,
        ip_address,
        matched,
        modified,
        upserted,
    )

    if require_matched and matched == 0 and upserted is None:
        logger.warning(
            "Mongo write unmatched | action=%s | deviceId=%s | ip=%s "
            "(device may have been deleted)",
            action,
            device_id,
            ip_address,
        )
        return False
    return True


def assert_insert_acknowledged(
    result: Any,
    *,
    action: str,
    device_id: Any = None,
    ip_address: str | None = None,
) -> bool:
    """Verify InsertOneResult was acknowledged and has an inserted_id."""
    if getattr(result, "acknowledged", True) is False:
        logger.error(
            "Mongo insert NOT acknowledged | action=%s | deviceId=%s | ip=%s",
            action,
            device_id,
            ip_address,
        )
        raise RuntimeError(f"Mongo insert not acknowledged: {action}")

    inserted_id = getattr(result, "inserted_id", None)
    if inserted_id is None:
        logger.error(
            "Mongo insert missing inserted_id | action=%s | deviceId=%s | ip=%s",
            action,
            device_id,
            ip_address,
        )
        raise RuntimeError(f"Mongo insert missing inserted_id: {action}")

    logger.info(
        "Mongo insert ok | action=%s | deviceId=%s | ip=%s | insertedId=%s",
        action,
        device_id,
        ip_address,
        inserted_id,
    )
    return True
