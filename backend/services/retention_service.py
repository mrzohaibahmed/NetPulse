"""
Data retention: MongoDB TTL indexes for high-volume history, plus a daily
purge of closed storm incidents (native TTL must not delete active incidents).

Three TTL windows:
- pingHistoryRetentionDays (default 7): pingHistory only
- dataRetentionDays (default 90): interface/storm evaluation telemetry
- incidentRetentionDays (default 365): mitigation/recovery attempt logs AND
  RESOLVED storm_incidents purge (same window so incidents stay consistent
  with their action history)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ASCENDING
from pymongo.errors import OperationFailure

from config.database import db
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("retention")

DEFAULT_PING_HISTORY_RETENTION_DAYS = 7
MIN_PING_HISTORY_RETENTION_DAYS = 7
MAX_PING_HISTORY_RETENTION_DAYS = 3650

DEFAULT_RETENTION_DAYS = 90
MIN_RETENTION_DAYS = 7
MAX_RETENTION_DAYS = 3650

DEFAULT_INCIDENT_RETENTION_DAYS = 365
MIN_INCIDENT_RETENTION_DAYS = 30
MAX_INCIDENT_RETENTION_DAYS = 3650

# Ping history — shortest window (pingHistoryRetentionDays).
PING_HISTORY_TTL_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("pingHistory", "timestamp", "idx_pingHistory_timestamp_ttl"),
)

# Telemetry / evaluation history — shorter window (dataRetentionDays).
# Do NOT include storm_incidents, lock collections, or mitigation/recovery audits.
DATA_TTL_TARGETS: tuple[tuple[str, str, str], ...] = (
    # collection, date_field, index_name
    ("interface_stats", "timestamp", "idx_interface_stats_timestamp_ttl"),
    ("eligibility_results", "timestamp", "idx_eligibility_timestamp_ttl"),
    ("storm_risk_history", "timestamp", "idx_storm_risk_timestamp_ttl"),
    ("storm_confirmation_history", "timestamp", "idx_storm_confirmation_timestamp_ttl"),
    ("storm_safety_history", "timestamp", "idx_storm_safety_timestamp_ttl"),
)

# Append-only port-action audit logs — longer window (incidentRetentionDays).
INCIDENT_TTL_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("storm_mitigation_history", "timestamp", "idx_storm_mitigation_timestamp_ttl"),
    ("storm_recovery_history", "timestamp", "idx_storm_recovery_timestamp_ttl"),
)

# Backward-compatible alias used by older call sites / tests.
TTL_TARGETS = DATA_TTL_TARGETS

# Only terminal incidents are age-purged by the daily job.
# Filter is temporary: RESOLVED only — MITIGATION_FAILED terminal-status decision pending.
CLOSED_INCIDENT_STATUSES: tuple[str, ...] = ("RESOLVED",)


def clamp_ping_history_retention_days(value: Any) -> int:
    days = int(value)
    if days < MIN_PING_HISTORY_RETENTION_DAYS:
        raise ValueError(
            f"pingHistoryRetentionDays must be at least {MIN_PING_HISTORY_RETENTION_DAYS}"
        )
    if days > MAX_PING_HISTORY_RETENTION_DAYS:
        raise ValueError(
            f"pingHistoryRetentionDays must be at most {MAX_PING_HISTORY_RETENTION_DAYS}"
        )
    return days


def clamp_retention_days(value: Any) -> int:
    days = int(value)
    if days < MIN_RETENTION_DAYS:
        raise ValueError(f"dataRetentionDays must be at least {MIN_RETENTION_DAYS}")
    if days > MAX_RETENTION_DAYS:
        raise ValueError(f"dataRetentionDays must be at most {MAX_RETENTION_DAYS}")
    return days


def clamp_incident_retention_days(value: Any) -> int:
    days = int(value)
    if days < MIN_INCIDENT_RETENTION_DAYS:
        raise ValueError(
            f"incidentRetentionDays must be at least {MIN_INCIDENT_RETENTION_DAYS}"
        )
    if days > MAX_INCIDENT_RETENTION_DAYS:
        raise ValueError(
            f"incidentRetentionDays must be at most {MAX_INCIDENT_RETENTION_DAYS}"
        )
    return days


def get_ping_history_retention_days(settings: dict | None = None) -> int:
    if settings is None:
        from services.settings_service import get_settings  # noqa: PLC0415

        settings = get_settings() or {}
    try:
        return clamp_ping_history_retention_days(
            settings.get("pingHistoryRetentionDays", DEFAULT_PING_HISTORY_RETENTION_DAYS)
        )
    except (TypeError, ValueError):
        return DEFAULT_PING_HISTORY_RETENTION_DAYS


def get_retention_days(settings: dict | None = None) -> int:
    if settings is None:
        from services.settings_service import get_settings  # noqa: PLC0415

        settings = get_settings() or {}
    try:
        return clamp_retention_days(
            settings.get("dataRetentionDays", DEFAULT_RETENTION_DAYS)
        )
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS


def get_incident_retention_days(settings: dict | None = None) -> int:
    if settings is None:
        from services.settings_service import get_settings  # noqa: PLC0415

        settings = get_settings() or {}
    try:
        return clamp_incident_retention_days(
            settings.get("incidentRetentionDays", DEFAULT_INCIDENT_RETENTION_DAYS)
        )
    except (TypeError, ValueError):
        return DEFAULT_INCIDENT_RETENTION_DAYS


def _ttl_seconds(days: int) -> int:
    return int(days) * 24 * 60 * 60


def _existing_ttl_seconds(coll, index_name: str) -> int | None:
    for idx in coll.list_indexes():
        if idx.get("name") != index_name:
            continue
        if "expireAfterSeconds" in idx:
            return int(idx["expireAfterSeconds"])
    return None


def _ensure_ttl_group(
    targets: tuple[tuple[str, str, str], ...],
    days: int,
    results: dict[str, Any],
) -> None:
    expire_after = _ttl_seconds(days)
    for collection_name, field, index_name in targets:
        coll = db[collection_name]
        try:
            current = _existing_ttl_seconds(coll, index_name)
            if current == expire_after:
                results["indexes"][collection_name] = {
                    "status": "unchanged",
                    "expireAfterSeconds": current,
                    "retentionDays": days,
                }
                continue

            if current is not None:
                coll.drop_index(index_name)
                logger.info(
                    "Dropped TTL index for recreate | collection=%s name=%s old=%ss",
                    collection_name,
                    index_name,
                    current,
                )

            coll.create_index(
                [(field, ASCENDING)],
                name=index_name,
                expireAfterSeconds=expire_after,
            )
            results["indexes"][collection_name] = {
                "status": "ensured",
                "expireAfterSeconds": expire_after,
                "retentionDays": days,
            }
            logger.info(
                "TTL index ensured | collection=%s field=%s expireAfterSeconds=%s (%sd)",
                collection_name,
                field,
                expire_after,
                days,
            )
        except OperationFailure as exc:
            results["indexes"][collection_name] = {"status": "error", "error": str(exc)}
            logger.error(
                "Failed to ensure TTL index | collection=%s error=%s",
                collection_name,
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            results["indexes"][collection_name] = {"status": "error", "error": str(exc)}
            logger.error(
                "Failed to ensure TTL index | collection=%s error=%s",
                collection_name,
                exc,
            )


def ensure_retention_ttl_indexes(
    retention_days: int | None = None,
    incident_retention_days: int | None = None,
    ping_history_retention_days: int | None = None,
) -> dict[str, Any]:
    """
    Ensure TTL indexes for all retention windows.

    If an existing TTL index has a different expireAfterSeconds, it is dropped
    and recreated (MongoDB does not allow in-place TTL value changes on all versions).
    """
    ping_history_days = (
        clamp_ping_history_retention_days(ping_history_retention_days)
        if ping_history_retention_days is not None
        else get_ping_history_retention_days()
    )
    data_days = (
        clamp_retention_days(retention_days)
        if retention_days is not None
        else get_retention_days()
    )
    incident_days = (
        clamp_incident_retention_days(incident_retention_days)
        if incident_retention_days is not None
        else get_incident_retention_days()
    )

    results: dict[str, Any] = {
        "pingHistoryRetentionDays": ping_history_days,
        "dataRetentionDays": data_days,
        "incidentRetentionDays": incident_days,
        "indexes": {},
    }
    _ensure_ttl_group(PING_HISTORY_TTL_TARGETS, ping_history_days, results)
    _ensure_ttl_group(DATA_TTL_TARGETS, data_days, results)
    _ensure_ttl_group(INCIDENT_TTL_TARGETS, incident_days, results)
    return results


def purge_closed_storm_incidents(retention_days: int | None = None) -> dict[str, Any]:
    """
    Delete storm_incidents in terminal states older than the retention window.

    Active / in-flight incidents are never removed by this job.
    Uses incidentRetentionDays so RESOLVED incidents expire with their
    mitigation/recovery history (not the shorter telemetry window).
    """
    days = (
        clamp_incident_retention_days(retention_days)
        if retention_days is not None
        else get_incident_retention_days()
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    query = {
        "status": {"$in": list(CLOSED_INCIDENT_STATUSES)},
        "$or": [
            {"updatedAt": {"$lt": cutoff}},
            {
                "updatedAt": {"$exists": False},
                "createdAt": {"$lt": cutoff},
            },
        ],
    }

    try:
        result = db.storm_incidents.delete_many(query)
        deleted = int(result.deleted_count)
        logger.info(
            "Closed incident purge | deleted=%s incidentRetentionDays=%s cutoff=%s",
            deleted,
            days,
            cutoff.isoformat(),
        )
        return {
            "deleted": deleted,
            "incidentRetentionDays": days,
            "cutoff": cutoff.isoformat(),
            "statuses": list(CLOSED_INCIDENT_STATUSES),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("Closed incident purge failed: %s", exc)
        return {"deleted": 0, "error": str(exc), "incidentRetentionDays": days}
