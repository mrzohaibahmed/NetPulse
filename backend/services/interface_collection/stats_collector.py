"""
stats_collector.py
==================
Periodic interface statistics collection orchestrator.

Strategy
--------
1. Prefer SNMP (IF-MIB / IF-X-MIB) when reachable.
2. Fall back to SSH counters when SNMP is unavailable or fails.
3. Refresh ``adminStatus`` / ``operStatus`` (and negotiated speed) on the
   existing ``interfaces`` inventory via targeted ``$set`` updates — discovery
   remains the owner of VLAN / classification / neighbor metadata.
4. Always **insert** into ``interface_stats`` (append-only history).
5. Compute utilization from the previous sample + link speed.

Designed for thousands of interfaces:
- Per-device thread pool with a bounded worker count
- ``insert_many`` in batches
- Compound indexes for latest + history queries
- Independent APScheduler job (does not touch ping monitoring)
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING

from config.database import (
    INTERFACE_STATS_BATCH_SIZE,
    INTERFACE_STATS_INTERVAL,
    MAX_INTERFACE_STATS_THREADS,
    db,
)
from models.interface_stats import create_interface_stat
from services.interface_collection.collector import _is_interface_discovery_candidate
from services.interface_collection.naming import (
    canonicalize_interface_name,
    normalize_storage_interface_name,
)
from services.interface_collection.snmp import (
    SNMPCollectorError,
    SNMPInterfaceCollector,
    resolve_snmp_credentials,
    snmp_available,
)
from services.interface_collection.ssh_collector import SSHCollectorError
from services.interface_collection.ssh_stats import collect_ssh_interface_stats
from services.interface_collection.utilization import (
    compute_utilization,
    resolve_speed_bps,
)
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("interface.stats")

COLLECTION = "interface_stats"


def ensure_interface_stats_indexes() -> None:
    """Create indexes optimised for latest-stats and history queries."""
    try:
        coll = db[COLLECTION]
        coll.create_index(
            [("deviceId", ASCENDING), ("interfaceName", ASCENDING), ("timestamp", DESCENDING)],
            name="idx_stats_device_iface_ts",
        )
        coll.create_index(
            [("deviceId", ASCENDING), ("timestamp", DESCENDING)],
            name="idx_stats_device_ts",
        )
        coll.create_index(
            [("timestamp", DESCENDING)],
            name="idx_stats_timestamp",
        )
        logger.info("[IFACE-STATS] MongoDB indexes ensured on %s", COLLECTION)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[IFACE-STATS] Failed to ensure indexes: %s", exc)


def collect_device_interface_stats(device: dict) -> dict:
    """
    Collect and persist stats for one device. Never raises.

    Returns
    -------
    dict
        success, ip, deviceId, collected, method, error
    """
    ip_address = device.get("ipAddress", "unknown")
    hostname = device.get("hostname", ip_address)
    device_id: ObjectId = device["_id"]

    if device.get("status") != "Online":
        return {
            "success": False,
            "ip": ip_address,
            "deviceId": str(device_id),
            "collected": 0,
            "method": None,
            "error": "Device is not online",
        }

    start = time.monotonic()
    method = None
    raw_rows: list[dict] = []

    try:
        raw_rows, method = _collect_raw_stats(device)
        if not raw_rows:
            return {
                "success": False,
                "ip": ip_address,
                "deviceId": str(device_id),
                "collected": 0,
                "method": method,
                "error": "No interface statistics returned",
            }

        previous = _load_previous_stats(device_id)
        inventory_speeds = _load_inventory_speeds(device_id)
        now = datetime.now(timezone.utc)

        # Keep inventory link state fresh between discovery cycles.
        # Failures here must never block stats persistence or the storm chain.
        try:
            _refresh_inventory_operational_state(device_id, raw_rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[IFACE-STATS] Operational status refresh failed | host=%s | %s",
                ip_address,
                exc,
            )

        documents = []

        for raw in raw_rows:
            raw_name = (raw.get("name") or "").strip()
            if not raw_name:
                continue
            name = normalize_storage_interface_name(raw_name)
            canon = canonicalize_interface_name(name)

            previous_doc = previous.get(canon) or previous.get(name) or previous.get(raw_name)
            speed_bps = resolve_speed_bps(
                raw.get("speed_bps"),
                inventory_speeds.get(canon),
                inventory_speeds.get(name),
                (previous_doc or {}).get("speedBps"),
            )
            util = _compute_utilization(
                current=raw,
                previous=previous_doc,
                now=now,
                speed_bps=speed_bps,
            )

            documents.append(
                create_interface_stat(
                    device_id=device_id,
                    hostname=hostname,
                    ip_address=ip_address,
                    interface_name=name,
                    rx_bytes=raw.get("rx_bytes", 0),
                    tx_bytes=raw.get("tx_bytes", 0),
                    rx_packets=raw.get("rx_packets", 0),
                    tx_packets=raw.get("tx_packets", 0),
                    broadcast_packets=raw.get("broadcast_packets", 0),
                    multicast_packets=raw.get("multicast_packets", 0),
                    rx_broadcast_packets=raw.get("rx_broadcast_packets"),
                    tx_broadcast_packets=raw.get("tx_broadcast_packets"),
                    rx_multicast_packets=raw.get("rx_multicast_packets"),
                    tx_multicast_packets=raw.get("tx_multicast_packets"),
                    rx_discards=raw.get("rx_discards"),
                    tx_discards=raw.get("tx_discards"),
                    input_errors=raw.get("input_errors", 0),
                    output_errors=raw.get("output_errors", 0),
                    discards=raw.get("discards", 0),
                    utilization=util.get("utilization"),
                    rx_utilization=util.get("rx_utilization"),
                    tx_utilization=util.get("tx_utilization"),
                    speed_bps=speed_bps,
                    if_index=raw.get("if_index"),
                    collection_method=method or "snmp",
                    timestamp=now,
                )
            )

        inserted = _insert_stats_batch(documents)
        elapsed = round(time.monotonic() - start, 2)
        logger.info(
            "[IFACE-STATS] Stored %d sample(s) in %.2fs | host=%s method=%s",
            inserted,
            elapsed,
            ip_address,
            method,
        )

        return {
            "success": True,
            "ip": ip_address,
            "deviceId": str(device_id),
            "collected": inserted,
            "method": method,
            "error": None,
        }

    except (SNMPCollectorError, SSHCollectorError) as exc:
        logger.error(
            "[IFACE-STATS] Collection failed | host=%s | %s",
            ip_address,
            exc,
        )
        return {
            "success": False,
            "ip": ip_address,
            "deviceId": str(device_id),
            "collected": 0,
            "method": method,
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[IFACE-STATS] Unexpected error | host=%s | %s",
            ip_address,
            exc,
        )
        return {
            "success": False,
            "ip": ip_address,
            "deviceId": str(device_id),
            "collected": 0,
            "method": method,
            "error": str(exc),
        }


def collect_all_interface_stats() -> dict:
    """
    Poll every eligible online managed switch for interface statistics.

    Safe for the APScheduler thread — never raises.
    """
    logger.info("[IFACE-STATS] Bulk stats collection started")
    start = time.monotonic()

    candidates = list(db.devices.find({"status": "Online"}))
    eligible = [d for d in candidates if _is_interface_discovery_candidate(d)]
    total = len(eligible)

    if total == 0:
        logger.info("[IFACE-STATS] No eligible online devices")
        return {
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "samples": 0,
            "errors": [],
        }

    succeeded = 0
    failed = 0
    samples = 0
    errors: list[dict[str, Any]] = []

    workers = max(int(MAX_INTERFACE_STATS_THREADS), 1)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(collect_device_interface_stats, device): device
            for device in eligible
        }
        for future in as_completed(futures):
            result = future.result()
            if result["success"]:
                succeeded += 1
                samples += int(result.get("collected") or 0)
            else:
                failed += 1
                errors.append({
                    "ip": result.get("ip"),
                    "deviceId": result.get("deviceId"),
                    "error": result.get("error"),
                })

    elapsed = round(time.monotonic() - start, 2)
    logger.info(
        "[IFACE-STATS] Bulk finished in %.2fs | total=%d ok=%d failed=%d samples=%d",
        elapsed,
        total,
        succeeded,
        failed,
        samples,
    )

    return {
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "samples": samples,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Query helpers (API)
# ---------------------------------------------------------------------------

def get_latest_device_stats(device_id: ObjectId) -> list[dict]:
    """
    Return the most recent stats sample for every interface on a device.

    Uses an aggregation pipeline that streams via the compound index
    ``(deviceId, interfaceName, timestamp)``.
    """
    # Prefer recent window to keep the working set small at scale.
    lookback_seconds = max(int(INTERFACE_STATS_INTERVAL) * 3, 300)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=lookback_seconds)

    pipeline = [
        {
            "$match": {
                "deviceId": device_id,
                "timestamp": {"$gte": cutoff},
            }
        },
        {"$sort": {"interfaceName": 1, "timestamp": -1}},
        {
            "$group": {
                "_id": "$interfaceName",
                "doc": {"$first": "$$ROOT"},
            }
        },
        {"$replaceRoot": {"newRoot": "$doc"}},
        {"$sort": {"interfaceName": 1}},
    ]

    docs = list(db[COLLECTION].aggregate(pipeline, allowDiskUse=True))

    # Fallback: if the lookback window is empty (first poll / long gap),
    # run without the time filter (still indexed by deviceId).
    if not docs:
        pipeline[0] = {"$match": {"deviceId": device_id}}
        docs = list(db[COLLECTION].aggregate(pipeline, allowDiskUse=True))

    return docs


def get_interface_stats_history(
    device_id: ObjectId,
    interface_name: str,
    *,
    skip: int = 0,
    limit: int = 100,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[list[dict], int]:
    """Return paginated historical samples for one interface (newest first)."""
    name = unquote(interface_name).strip()
    query: dict[str, Any] = {
        "deviceId": device_id,
        "interfaceName": name,
    }

    # Also try common Cisco short/long name variants when exact match empty.
    # Callers may pass Gi1/0/1 or GigabitEthernet1/0/1.
    if start or end:
        query["timestamp"] = {}
        if start:
            query["timestamp"]["$gte"] = start
        if end:
            query["timestamp"]["$lte"] = end

    total = db[COLLECTION].count_documents(query)
    if total == 0:
        # Soft fallback: case-insensitive exact name
        import re
        query["interfaceName"] = re.compile(f"^{re.escape(name)}$", re.IGNORECASE)
        total = db[COLLECTION].count_documents(query)

    cursor = (
        db[COLLECTION]
        .find(query)
        .sort("timestamp", DESCENDING)
        .skip(skip)
        .limit(limit)
    )
    return list(cursor), total


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _collect_raw_stats(device: dict) -> tuple[list[dict], str]:
    """Try SNMP first, then SSH. Returns (rows, method)."""
    snmp_error = None

    if snmp_available():
        try:
            creds = resolve_snmp_credentials(device)
            collector = SNMPInterfaceCollector(creds)
            rows = collector.collect_interface_stats()
            if rows:
                return rows, "snmp"
            snmp_error = "SNMP returned no interfaces"
        except SNMPCollectorError as exc:
            snmp_error = str(exc)
            logger.info(
                "[IFACE-STATS] SNMP unavailable, trying SSH | host=%s | %s",
                device.get("ipAddress"),
                exc,
            )
    else:
        snmp_error = "pysnmp not installed"
        logger.info("[IFACE-STATS] pysnmp missing; using SSH fallback")

    try:
        rows = collect_ssh_interface_stats(device)
        return rows, "ssh"
    except SSHCollectorError as exc:
        detail = str(exc)
        if snmp_error:
            detail = f"SNMP failed ({snmp_error}); SSH failed ({detail})"
        raise SSHCollectorError(detail) from exc


def _load_previous_stats(device_id: ObjectId) -> dict[str, dict]:
    """
    Load the previous sample per interface for utilization deltas.

    Uses the same aggregation as latest-stats but without a narrow window
    when needed — limited to recent samples for scale.
    """
    lookback = max(int(INTERFACE_STATS_INTERVAL) * 5, 600)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=lookback)

    pipeline = [
        {"$match": {"deviceId": device_id, "timestamp": {"$gte": cutoff}}},
        {"$sort": {"interfaceName": 1, "timestamp": -1}},
        {
            "$group": {
                "_id": "$interfaceName",
                "doc": {"$first": "$$ROOT"},
            }
        },
    ]

    previous: dict[str, dict] = {}
    for item in db[COLLECTION].aggregate(pipeline, allowDiskUse=True):
        doc = item.get("doc") or {}
        name = doc.get("interfaceName")
        if not name:
            continue
        # Index by canonical + raw so rediscovery/stats name forms always match
        previous[canonicalize_interface_name(name)] = doc
        previous[name] = doc
        previous[normalize_storage_interface_name(name)] = doc
    return previous


def _load_inventory_speeds(device_id: ObjectId) -> dict[str, int]:
    """
    Map interface name → speed_bps from the interfaces inventory.

    Used when SNMP/SSH stats cannot determine negotiated speed (e.g. Speed=auto
    on ``show interfaces status``).
    """
    speeds: dict[str, int] = {}
    try:
        cursor = db.interfaces.find(
            {"deviceId": device_id},
            {"name": 1, "speedMbps": 1, "speed": 1},
        )
        for doc in cursor:
            name = doc.get("name")
            if not name:
                continue
            bps = resolve_speed_bps(doc.get("speedMbps"), doc.get("speed"))
            if not bps:
                continue
            speeds[canonicalize_interface_name(name)] = bps
            speeds[name] = bps
            speeds[normalize_storage_interface_name(name)] = bps
    except Exception as exc:  # noqa: BLE001
        logger.debug("[IFACE-STATS] Inventory speed lookup failed: %s", exc)
    return speeds


def _refresh_inventory_operational_state(
    device_id: ObjectId,
    raw_rows: list[dict],
) -> int:
    """
    Apply targeted ``$set`` updates for operational link state only.

    Updates ``adminStatus`` / ``operStatus`` and, when a negotiated speed is
    present on the stats row, the existing inventory ``speed`` / ``speedMbps``
    fields (no schema change — inventory does not store ``speedBps``).

    Does **not**:
    - create or replace interface documents
    - modify VLAN / mode / classification / neighbors / monitoring preference
    - touch discovery timestamps (``lastUpdated`` / ``updatedAt`` / ``createdAt``)
    - overwrite existing status with null / unknown / empty values

    Returns the number of interfaces successfully updated.
    """
    if not raw_rows:
        return 0

    inventory = list(
        db.interfaces.find(
            {"deviceId": device_id},
            {"_id": 1, "name": 1, "ifIndex": 1},
        )
    )
    if not inventory:
        return 0

    by_canon: dict[str, dict] = {}
    by_if_index: dict[int, dict] = {}
    for doc in inventory:
        name = doc.get("name")
        if name:
            by_canon[canonicalize_interface_name(name)] = doc
        if_index = doc.get("ifIndex")
        if if_index is not None:
            try:
                by_if_index[int(if_index)] = doc
            except (TypeError, ValueError):
                pass

    updated = 0
    for raw in raw_rows:
        fields = _operational_fields_from_raw(raw)
        if not fields:
            continue

        target = None
        raw_name = (raw.get("name") or "").strip()
        if raw_name:
            target = by_canon.get(canonicalize_interface_name(raw_name))
        if target is None and raw.get("if_index") is not None:
            try:
                target = by_if_index.get(int(raw.get("if_index")))
            except (TypeError, ValueError):
                target = None
        if target is None or not target.get("_id"):
            continue

        try:
            result = db.interfaces.update_one(
                {"_id": target["_id"]},
                {"$set": fields},
            )
            if result.acknowledged and (
                result.modified_count or result.matched_count
            ):
                updated += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[IFACE-STATS] Status $set failed | device=%s iface=%s | %s",
                device_id,
                raw_name or raw.get("if_index"),
                exc,
            )

    if updated:
        logger.info(
            "[IFACE-STATS] Refreshed operational status on %d interface(s) | device=%s",
            updated,
            device_id,
        )
    return updated


def _operational_fields_from_raw(raw: dict) -> dict[str, Any]:
    """
    Build a partial ``$set`` payload from a stats row.

    Only includes concrete, non-empty operational values.
    """
    fields: dict[str, Any] = {}

    admin = _normalize_refresh_status(
        raw.get("admin_status", raw.get("adminStatus"))
    )
    oper = _normalize_refresh_status(
        raw.get("oper_status", raw.get("operStatus"))
    )
    if admin:
        fields["adminStatus"] = admin
    if oper:
        fields["operStatus"] = oper

    # Inventory stores speed / speedMbps (not speedBps). Map negotiated
    # stats bandwidth onto those existing fields without schema changes.
    speed_bps = resolve_speed_bps(raw.get("speed_bps"), raw.get("speedBps"))
    if speed_bps and speed_bps > 0:
        mbps = int(speed_bps // 1_000_000)
        if mbps > 0:
            fields["speedMbps"] = mbps
            fields["speed"] = _format_speed_for_inventory(mbps)

    return fields


def _normalize_refresh_status(value: Any) -> str | None:
    """Return a canonical up/down(/testing) string, or None to skip update."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text in ("unknown", "none", "null"):
        return None
    if text in ("up", "connected"):
        return "up"
    if text in ("down", "notconnect", "disabled", "err-disabled", "errdisabled"):
        return "down"
    if text == "testing":
        return "testing"
    return None


def _format_speed_for_inventory(speed_mbps: int) -> str:
    """Human-readable speed string matching discovery inventory style."""
    if speed_mbps >= 1000 and speed_mbps % 1000 == 0:
        g = speed_mbps // 1000
        return f"{g}G"
    return str(speed_mbps)


def _compute_utilization(
    current: dict,
    previous: dict | None,
    now: datetime,
    *,
    speed_bps: int | None = None,
) -> dict[str, float | None]:
    """
    Compute RX/TX/overall utilization percentages from counter deltas.

    Returns None utilizations when previous sample or speed is unavailable.
    """
    empty = {
        "utilization": None,
        "rx_utilization": None,
        "tx_utilization": None,
    }
    if not previous:
        return empty

    resolved_speed = resolve_speed_bps(
        speed_bps,
        current.get("speed_bps"),
        previous.get("speedBps"),
    )
    if not resolved_speed:
        return empty

    result = compute_utilization(
        current_rx_bytes=current.get("rx_bytes", 0),
        current_tx_bytes=current.get("tx_bytes", 0),
        previous_rx_bytes=previous.get("rxBytes", 0),
        previous_tx_bytes=previous.get("txBytes", 0),
        speed_bps=resolved_speed,
        current_timestamp=now,
        previous_timestamp=previous.get("timestamp"),
    )
    if result.get("utilization") is None:
        return empty

    return {
        "utilization": result["utilization"],
        "rx_utilization": result["rx_utilization"],
        "tx_utilization": result["tx_utilization"],
    }


def _counter_delta(current: int, previous: int) -> int:
    """Handle 32/64-bit counter wrap (legacy helper kept for callers/tests)."""
    from services.interface_collection.utilization import counter_delta  # noqa: PLC0415

    delta, _event = counter_delta(current, previous)
    return delta


def _insert_stats_batch(documents: list[dict]) -> int:
    """Append-only bulk insert in chunks."""
    if not documents:
        return 0

    batch_size = max(int(INTERFACE_STATS_BATCH_SIZE), 50)
    inserted = 0
    coll = db[COLLECTION]

    for i in range(0, len(documents), batch_size):
        chunk = documents[i:i + batch_size]
        # insert_many never overwrites; ordered=False for throughput
        result = coll.insert_many(chunk, ordered=False)
        inserted += len(result.inserted_ids)

    return inserted
