"""
Bulk prefetch helpers for Confirmation (Phase 2).

Loads the same logical inputs ConfirmationEngine.evaluate() would fetch
per-interface, but in a handful of Mongo round-trips.

Decision logic is unchanged — only the data-access shape differs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pymongo import DESCENDING

from services.storm.confirmation_history import (
    COLLECTION as CONFIRM_COLLECTION,
    RISK_COLLECTION,
    _as_oid,
)


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _key(device_id, interface: str) -> tuple[str, str]:
    return (str(device_id), str(interface))


def bulk_latest_eligibility_map(
    pairs: list[tuple[Any, str]] | None = None,
) -> dict[tuple[str, str], bool]:
    """Latest eligibility per (deviceId, interface)."""
    pipeline: list[dict[str, Any]] = []
    if pairs:
        oids = list({_as_oid(d) for d, _ in pairs})
        pipeline.append({"$match": {"deviceId": {"$in": oids}}})
    pipeline.extend(
        [
            {"$sort": {"timestamp": DESCENDING}},
            {
                "$group": {
                    "_id": {"deviceId": "$deviceId", "interface": "$interface"},
                    "eligible": {"$first": "$eligible"},
                }
            },
        ]
    )
    out: dict[tuple[str, str], bool] = {}
    for row in _db().eligibility_results.aggregate(pipeline, allowDiskUse=True):
        key = row.get("_id") or {}
        device_id = key.get("deviceId")
        iface = key.get("interface")
        if device_id is None or not iface:
            continue
        out[_key(device_id, iface)] = bool(row.get("eligible"))
    return out


def bulk_latest_confirmation_map(
    pairs: list[tuple[Any, str]] | None = None,
) -> dict[tuple[str, str], dict]:
    pipeline: list[dict[str, Any]] = []
    if pairs:
        oids = list({_as_oid(d) for d, _ in pairs})
        pipeline.append({"$match": {"deviceId": {"$in": oids}}})
    pipeline.extend(
        [
            {"$sort": {"timestamp": DESCENDING}},
            {
                "$group": {
                    "_id": {"deviceId": "$deviceId", "interface": "$interface"},
                    "doc": {"$first": "$$ROOT"},
                }
            },
        ]
    )
    out: dict[tuple[str, str], dict] = {}
    for row in _db()[CONFIRM_COLLECTION].aggregate(pipeline, allowDiskUse=True):
        key = row.get("_id") or {}
        device_id = key.get("deviceId")
        iface = key.get("interface")
        doc = row.get("doc")
        if device_id is None or not iface or not isinstance(doc, dict):
            continue
        out[_key(device_id, iface)] = doc
    return out


def bulk_recent_risk_rows_map(
    *,
    limit: int = 12,
    pairs: list[tuple[Any, str]] | None = None,
) -> tuple[dict[tuple[str, str], list[dict]], dict[str, Any]]:
    """
    Newest-first risk rows (capped) per interface.

    Prefers ``storm_risk_latest`` when enabled and populated; otherwise
    falls back to history ``$topN`` (Phase 2 path). Returns (map, meta)
    where meta includes riskLatestHit / riskLatestFallback timings.
    """
    import time  # noqa: PLC0415

    lim = max(int(limit), 1)
    meta: dict[str, Any] = {
        "riskLatestHit": False,
        "riskLatestFallback": False,
        "riskLookupDurationMs": 0,
        "source": "history",
    }
    started = time.monotonic()

    try:
        from services.storm.risk_latest import (  # noqa: PLC0415
            bulk_recent_risk_rows_from_latest,
            risk_latest_enabled,
        )

        if risk_latest_enabled():
            latest_map = bulk_recent_risk_rows_from_latest(limit=lim, pairs=pairs)
            # Use latest only when it covers the candidate set (or no pairs given).
            if latest_map and (not pairs or len(latest_map) >= max(len(pairs) * 9 // 10, 1)):
                meta["riskLatestHit"] = True
                meta["source"] = "risk_latest"
                meta["riskLookupDurationMs"] = int((time.monotonic() - started) * 1000)
                return latest_map, meta
            meta["riskLatestFallback"] = True
    except Exception:  # noqa: BLE001
        meta["riskLatestFallback"] = True

    pipeline: list[dict[str, Any]] = []
    if pairs:
        pipeline.append(
            {"$match": {"deviceId": {"$in": list({_as_oid(d) for d, _ in pairs})}}}
        )
    pipeline.append(
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
    )
    out: dict[tuple[str, str], list[dict]] = {}
    for row in _db()[RISK_COLLECTION].aggregate(pipeline, allowDiskUse=True):
        key = row.get("_id") or {}
        device_id = key.get("deviceId")
        iface = key.get("interface")
        rows = row.get("rows") or []
        if device_id is None or not iface:
            continue
        out[_key(device_id, iface)] = list(rows)
    meta["source"] = "history"
    meta["riskLookupDurationMs"] = int((time.monotonic() - started) * 1000)
    return out, meta


def bulk_device_status_map(device_ids: list[Any]) -> dict[str, Optional[str]]:
    oids = [_as_oid(d) for d in device_ids]
    out: dict[str, Optional[str]] = {str(o): None for o in oids}
    for doc in _db().devices.find({"_id": {"$in": oids}}, {"status": 1}):
        out[str(doc["_id"])] = doc.get("status")
    return out


def bulk_interface_exists_set(pairs: list[tuple[Any, str]]) -> set[tuple[str, str]]:
    if not pairs:
        return set()
    oids = list({_as_oid(d) for d, _ in pairs})
    names = list({str(n) for _, n in pairs})
    found: set[tuple[str, str]] = set()
    for doc in _db().interfaces.find(
        {"deviceId": {"$in": oids}, "name": {"$in": names}},
        {"deviceId": 1, "name": 1},
    ):
        found.add(_key(doc.get("deviceId"), doc.get("name")))
    return found


def bulk_latest_stats_map(
    pairs: list[tuple[Any, str]],
    *,
    lookback_hours: int = 24,
) -> dict[tuple[str, str], dict]:
    """Latest interface_stats doc keyed by (deviceId, interfaceName)."""
    if not pairs:
        return {}
    oids = list({_as_oid(d) for d, _ in pairs})
    # Prefer $topN (exact latest) over time-bounded $first to match find_one sort.
    pipeline = [
        {"$match": {"deviceId": {"$in": oids}}},
        {
            "$group": {
                "_id": {
                    "deviceId": "$deviceId",
                    "interfaceName": "$interfaceName",
                },
                "doc": {
                    "$top": {
                        "sortBy": {"timestamp": DESCENDING},
                        "output": "$$ROOT",
                    }
                },
            }
        },
    ]
    out: dict[tuple[str, str], dict] = {}
    for row in _db().interface_stats.aggregate(pipeline, allowDiskUse=True):
        key = row.get("_id") or {}
        device_id = key.get("deviceId")
        name = key.get("interfaceName")
        doc = row.get("doc")
        if device_id is None or not name or not isinstance(doc, dict):
            continue
        out[_key(device_id, name)] = doc
    return out

def detect_poll_failure_from_maps(
    device_id,
    interface: str,
    *,
    stale_seconds: int,
    latest_risk: Optional[dict],
    risk_rows: list[dict],
    device_status: Optional[str],
    interface_exists: bool,
    latest_stat: Optional[dict],
) -> tuple[bool, Optional[str]]:
    """
    Same rules as ``detect_poll_failure``, using preloaded maps.

    Must stay semantically equivalent to confirmation_history.detect_poll_failure.
    """
    if device_status is None:
        return True, "Device not found"
    if str(device_status).lower() not in ("online",):
        return True, f"Device unreachable ({device_status})"

    if not interface_exists:
        return True, "Interface removed"

    if latest_stat is None:
        return True, "Missing statistics"

    ts = latest_stat.get("timestamp")
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age > max(int(stale_seconds), 1):
            return True, "Stale statistics (polling failure)"

    if latest_risk:
        skipped = (latest_risk.get("skippedReason") or "").lower()
        if "missing statistics" in skipped or "missing history" in skipped:
            return True, "Missing statistics history"

    if latest_risk is not None:
        confidence = latest_risk.get("confidence")
        try:
            if confidence is not None and float(confidence) <= 0:
                prev = risk_rows[:2] if risk_rows else []
                if len(prev) >= 2 and float(prev[1].get("confidence") or 0) > 0:
                    return True, "Counter reset / confidence collapse"
        except (TypeError, ValueError):
            pass

    return False, None


def prefer_cycle_risk_rows(
    risk_rows: list[dict],
    cycle_id: Optional[str],
) -> list[dict]:
    if not cycle_id or not risk_rows:
        return list(risk_rows or [])
    cycle_rows = [r for r in risk_rows if str(r.get("cycleId") or "") == str(cycle_id)]
    if not cycle_rows:
        return list(risk_rows)
    chosen = cycle_rows[0]
    return [chosen] + [r for r in risk_rows if r.get("_id") != chosen.get("_id")]
