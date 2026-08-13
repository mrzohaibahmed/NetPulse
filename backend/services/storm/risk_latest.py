"""
Rebuildable latest-risk projection (Phase 3B).

``storm_risk_history`` remains the authoritative append-only audit log.
``storm_risk_latest`` is a derived read optimization keyed by
(deviceId, interface), including a capped newest-first ``recentRows``
window matching Confirmation's history lookback.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, UpdateOne

from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.risk_latest")

COLLECTION = "storm_risk_latest"
HISTORY_COLLECTION = "storm_risk_history"
DEFAULT_RECENT_LIMIT = 12


def risk_latest_enabled() -> bool:
    """Feature flag — set STORM_RISK_LATEST=0 to force history fallback."""
    return str(os.environ.get("STORM_RISK_LATEST", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _as_oid(device_id):
    if isinstance(device_id, ObjectId):
        return device_id
    if isinstance(device_id, str) and ObjectId.is_valid(device_id):
        return ObjectId(device_id)
    return device_id


def _key(device_id, interface: str) -> tuple[str, str]:
    return (str(device_id), str(interface))


def ensure_risk_latest_indexes() -> None:
    try:
        coll = _db()[COLLECTION]
        coll.create_index(
            [("deviceId", ASCENDING), ("interface", ASCENDING)],
            unique=True,
            name="idx_risk_latest_device_iface",
        )
        coll.create_index(
            [("cycleId", ASCENDING)],
            name="idx_risk_latest_cycle",
            sparse=True,
        )
        coll.create_index(
            [("timestamp", DESCENDING)],
            name="idx_risk_latest_ts",
        )
        logger.info("[RISK_LATEST] indexes ensured on %s", COLLECTION)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RISK_LATEST] Failed to ensure indexes: %s", exc)


def _row_from_history_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Projection fields Confirmation / arbitration need from a risk row."""
    row = {
        "_id": doc.get("_id"),
        "deviceId": doc.get("deviceId"),
        "interface": doc.get("interface"),
        "riskScore": doc.get("riskScore"),
        "severity": doc.get("severity"),
        "confidence": doc.get("confidence"),
        "eligible": doc.get("eligible"),
        "skippedReason": doc.get("skippedReason"),
        "timestamp": doc.get("timestamp"),
        "contributors": doc.get("contributors"),
        "rawMetrics": doc.get("rawMetrics"),
        "sourceClassification": doc.get("sourceClassification"),
        "sourceConfidence": doc.get("sourceConfidence"),
        "sourceRationale": doc.get("sourceRationale"),
        "hostname": doc.get("hostname"),
        "ipAddress": doc.get("ipAddress"),
    }
    if doc.get("cycleId") is not None:
        row["cycleId"] = doc.get("cycleId")
    return row


def upsert_risk_latest_from_history_doc(
    doc: dict[str, Any],
    *,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
) -> None:
    """
    Mirror one newly appended history document into the latest projection.

    Safe to call after each append-only history insert.
    """
    device_id = doc.get("deviceId")
    interface = doc.get("interface")
    if device_id is None or not interface:
        return

    oid = _as_oid(device_id)
    name = str(interface)
    row = _row_from_history_doc(doc)
    lim = max(int(recent_limit), 1)
    now = datetime.now(timezone.utc)

    _db()[COLLECTION].update_one(
        {"deviceId": oid, "interface": name},
        {
            "$set": {
                "deviceId": oid,
                "interface": name,
                "hostname": doc.get("hostname"),
                "ipAddress": doc.get("ipAddress"),
                "riskScore": doc.get("riskScore"),
                "severity": doc.get("severity"),
                "confidence": doc.get("confidence"),
                "eligible": doc.get("eligible"),
                "skippedReason": doc.get("skippedReason"),
                "timestamp": doc.get("timestamp") or now,
                "cycleId": doc.get("cycleId"),
                "contributors": doc.get("contributors"),
                "rawMetrics": doc.get("rawMetrics"),
                "sourceClassification": doc.get("sourceClassification"),
                "sourceConfidence": doc.get("sourceConfidence"),
                "sourceRationale": doc.get("sourceRationale"),
                "historyId": doc.get("_id"),
                "updatedAt": now,
            },
            "$push": {
                "recentRows": {
                    "$each": [row],
                    "$position": 0,
                    "$slice": lim,
                }
            },
        },
        upsert=True,
    )


def upsert_risk_latest_many(
    docs: list[dict[str, Any]],
    *,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
) -> None:
    """Batch mirror after ``insert_many`` into history."""
    if not docs:
        return
    lim = max(int(recent_limit), 1)
    now = datetime.now(timezone.utc)
    ops: list[UpdateOne] = []
    for doc in docs:
        device_id = doc.get("deviceId")
        interface = doc.get("interface")
        if device_id is None or not interface:
            continue
        oid = _as_oid(device_id)
        name = str(interface)
        row = _row_from_history_doc(doc)
        ops.append(
            UpdateOne(
                {"deviceId": oid, "interface": name},
                {
                    "$set": {
                        "deviceId": oid,
                        "interface": name,
                        "hostname": doc.get("hostname"),
                        "ipAddress": doc.get("ipAddress"),
                        "riskScore": doc.get("riskScore"),
                        "severity": doc.get("severity"),
                        "confidence": doc.get("confidence"),
                        "eligible": doc.get("eligible"),
                        "skippedReason": doc.get("skippedReason"),
                        "timestamp": doc.get("timestamp") or now,
                        "cycleId": doc.get("cycleId"),
                        "contributors": doc.get("contributors"),
                        "rawMetrics": doc.get("rawMetrics"),
                        "sourceClassification": doc.get("sourceClassification"),
                        "sourceConfidence": doc.get("sourceConfidence"),
                        "sourceRationale": doc.get("sourceRationale"),
                        "historyId": doc.get("_id"),
                        "updatedAt": now,
                    },
                    "$push": {
                        "recentRows": {
                            "$each": [row],
                            "$position": 0,
                            "$slice": lim,
                        }
                    },
                },
                upsert=True,
            )
        )
    if ops:
        _db()[COLLECTION].bulk_write(ops, ordered=False)


def bulk_recent_risk_rows_from_latest(
    *,
    limit: int = DEFAULT_RECENT_LIMIT,
    pairs: list[tuple[Any, str]] | None = None,
) -> dict[tuple[str, str], list[dict]]:
    """
    Load Confirmation risk windows from ``storm_risk_latest``.

    Returns the same shape as history ``$topN`` prefetch: newest-first rows.
    """
    lim = max(int(limit), 1)
    query: dict[str, Any] = {}
    if pairs:
        query["deviceId"] = {"$in": list({_as_oid(d) for d, _ in pairs})}

    out: dict[tuple[str, str], list[dict]] = {}
    for doc in _db()[COLLECTION].find(query):
        device_id = doc.get("deviceId")
        iface = doc.get("interface")
        if device_id is None or not iface:
            continue
        rows = list(doc.get("recentRows") or [])
        if not rows:
            # Degenerate projection — synthesize from scalar latest fields.
            rows = [_row_from_history_doc(doc)]
        out[_key(device_id, iface)] = rows[:lim]
    return out


def load_confirmation_candidates_from_latest() -> Optional[list[dict[str, Any]]]:
    """
    Candidate list with the same population as distinct history keys
    (all interfaces present in the projection — ever scored if rebuilt /
    never deleted).

    Returns None when the projection is empty so callers fall back to history.
    """
    coll = _db()[COLLECTION]
    if coll.estimated_document_count() <= 0:
        return None
    rows = []
    for doc in coll.find(
        {},
        {
            "deviceId": 1,
            "interface": 1,
            "hostname": 1,
            "ipAddress": 1,
        },
    ):
        device_id = doc.get("deviceId")
        iface = doc.get("interface")
        if device_id is None or not iface:
            continue
        rows.append(
            {
                "_id": {"deviceId": device_id, "interface": iface},
                "hostname": doc.get("hostname"),
                "ipAddress": doc.get("ipAddress"),
            }
        )
    return rows or None


def rebuild_risk_latest(*, recent_limit: int = DEFAULT_RECENT_LIMIT) -> dict[str, Any]:
    """
    Rebuild projection from authoritative history. Safe to run operationally.

    Does not delete history. Replaces latest documents via upsert.
    """
    lim = max(int(recent_limit), 1)
    started = time.monotonic()
    pipeline = [
        {
            "$group": {
                "_id": {"deviceId": "$deviceId", "interface": "$interface"},
                "rows": {
                    "$topN": {
                        "n": lim,
                        "sortBy": {"timestamp": DESCENDING},
                        "output": "$$ROOT",
                    }
                },
            }
        }
    ]
    upserted = 0
    ops: list[UpdateOne] = []
    now = datetime.now(timezone.utc)
    for group in _db()[HISTORY_COLLECTION].aggregate(pipeline, allowDiskUse=True):
        key = group.get("_id") or {}
        device_id = key.get("deviceId")
        iface = key.get("interface")
        rows_raw = list(group.get("rows") or [])
        if device_id is None or not iface or not rows_raw:
            continue
        latest = rows_raw[0]
        recent = [_row_from_history_doc(r) for r in rows_raw[:lim]]
        oid = _as_oid(device_id)
        name = str(iface)
        ops.append(
            UpdateOne(
                {"deviceId": oid, "interface": name},
                {
                    "$set": {
                        "deviceId": oid,
                        "interface": name,
                        "hostname": latest.get("hostname"),
                        "ipAddress": latest.get("ipAddress"),
                        "riskScore": latest.get("riskScore"),
                        "severity": latest.get("severity"),
                        "confidence": latest.get("confidence"),
                        "eligible": latest.get("eligible"),
                        "skippedReason": latest.get("skippedReason"),
                        "timestamp": latest.get("timestamp") or now,
                        "cycleId": latest.get("cycleId"),
                        "contributors": latest.get("contributors"),
                        "rawMetrics": latest.get("rawMetrics"),
                        "sourceClassification": latest.get("sourceClassification"),
                        "sourceConfidence": latest.get("sourceConfidence"),
                        "sourceRationale": latest.get("sourceRationale"),
                        "historyId": latest.get("_id"),
                        "recentRows": recent,
                        "updatedAt": now,
                        "rebuiltAt": now,
                    }
                },
                upsert=True,
            )
        )
        if len(ops) >= 200:
            _db()[COLLECTION].bulk_write(ops, ordered=False)
            upserted += len(ops)
            ops = []
    if ops:
        _db()[COLLECTION].bulk_write(ops, ordered=False)
        upserted += len(ops)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "[RISK_LATEST] rebuild complete upserted=%s durationMs=%s recentLimit=%s",
        upserted,
        elapsed_ms,
        lim,
    )
    return {"upserted": upserted, "durationMs": elapsed_ms, "recentLimit": lim}
