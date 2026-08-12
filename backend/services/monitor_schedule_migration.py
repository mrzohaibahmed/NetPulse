"""
Idempotent migration helpers for the 60s dispatch monitoring cutover.

- Bumps historical seed settings (pingInterval 30→60, pingConcurrency 20|30→40)
  only when they still match known legacy defaults (never overrides custom values).
- Staggers ``nextCheckAt`` for monitored devices that lack a schedule so a
  process restart / first dispatch enablement does not make the entire fleet
  due in one tick.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any

from services.settings_service import SETTINGS_ID, get_settings
from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("monitor_schedule_migration")

LEGACY_SEED_INTERVAL = 30
TARGET_INTERVAL = 60
LEGACY_SEED_CONCURRENCIES = {20, 30}
TARGET_CONCURRENCY = 40
MIGRATION_FLAG = "monitorDispatchCadenceV2"


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _stagger_offset_seconds(device_id: Any, interval_s: int) -> int:
    """Deterministic offset in ``[0, interval_s)`` from the device id."""
    interval_s = max(int(interval_s), 1)
    raw = str(device_id).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return int(digest[:8], 16) % interval_s


def ensure_monitor_cadence_settings() -> dict[str, Any]:
    """
    Promote legacy seed cadence/concurrency to the 60s / 40-worker targets.

    Idempotent via ``monitorDispatchCadenceV2`` on the settings document.
    Custom ``pingInterval`` / ``pingConcurrency`` values are left untouched.
    """
    settings = get_settings()
    if settings.get(MIGRATION_FLAG):
        return {"skipped": True, "reason": "already_migrated"}

    updates: dict[str, Any] = {
        MIGRATION_FLAG: True,
        "updatedAt": utc_now(),
    }
    changed: list[str] = []

    try:
        interval = int(settings.get("pingInterval") or LEGACY_SEED_INTERVAL)
    except (TypeError, ValueError):
        interval = LEGACY_SEED_INTERVAL
    if interval == LEGACY_SEED_INTERVAL:
        updates["pingInterval"] = TARGET_INTERVAL
        changed.append("pingInterval")

    try:
        concurrency = int(settings.get("pingConcurrency") or 20)
    except (TypeError, ValueError):
        concurrency = 20
    if concurrency in LEGACY_SEED_CONCURRENCIES:
        updates["pingConcurrency"] = TARGET_CONCURRENCY
        changed.append("pingConcurrency")

    _db().settings.update_one({"_id": SETTINGS_ID}, {"$set": updates})
    logger.info(
        "Monitor cadence settings migration applied | changed=%s | flag=%s",
        changed or ["flag_only"],
        MIGRATION_FLAG,
    )
    return {"skipped": False, "changed": changed}


def backfill_next_check_at(*, interval_seconds: int | None = None) -> dict[str, Any]:
    """
    Assign staggered ``nextCheckAt`` to monitored devices missing the field.

    Does not rewrite devices that already have ``nextCheckAt``.
    Does not clear status/history/claim fields.
    """
    settings = get_settings()
    try:
        interval_s = int(
            interval_seconds
            if interval_seconds is not None
            else settings.get("pingInterval") or TARGET_INTERVAL
        )
    except (TypeError, ValueError):
        interval_s = TARGET_INTERVAL
    interval_s = max(interval_s, 1)

    now = utc_now()
    query = {
        "monitor": True,
        "$or": [
            {"nextCheckAt": {"$exists": False}},
            {"nextCheckAt": None},
        ],
    }

    updated = 0
    scanned = 0
    try:
        cursor = _db().devices.find(query, {"_id": 1})
        for doc in cursor:
            scanned += 1
            device_id = doc["_id"]
            offset = _stagger_offset_seconds(device_id, interval_s)
            next_at = now + timedelta(seconds=offset)
            result = _db().devices.update_one(
                {
                    "_id": device_id,
                    "$or": [
                        {"nextCheckAt": {"$exists": False}},
                        {"nextCheckAt": None},
                    ],
                },
                {"$set": {"nextCheckAt": next_at}},
            )
            if int(getattr(result, "modified_count", 0) or 0):
                updated += 1
    except Exception as exc:  # noqa: BLE001
        logger.error("nextCheckAt backfill failed | error=%s", exc)
        raise

    logger.info(
        "nextCheckAt backfill complete | scanned=%s | updated=%s | interval=%ss",
        scanned,
        updated,
        interval_s,
    )
    return {"scanned": scanned, "updated": updated, "intervalSeconds": interval_s}


def ensure_monitor_schedule_migration() -> dict[str, Any]:
    """Bootstrap entrypoint: cadence settings then staggered nextCheckAt backfill."""
    cadence = ensure_monitor_cadence_settings()
    # Re-read settings after possible interval bump.
    settings = get_settings()
    try:
        interval_s = int(settings.get("pingInterval") or TARGET_INTERVAL)
    except (TypeError, ValueError):
        interval_s = TARGET_INTERVAL
    backfill = backfill_next_check_at(interval_seconds=interval_s)
    return {"cadence": cadence, "backfill": backfill}
