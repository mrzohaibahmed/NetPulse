"""
stats_collector.py
==================
Periodic interface statistics collection orchestrator.

Strategy
--------
1. Prefer SNMP (IF-MIB / IF-X-MIB) when reachable.
2. Fall back to SSH counters when SNMP is unavailable or fails.
3. Always **insert** into ``interface_stats`` (append-only history).
4. Compute utilization from the previous sample + link speed.

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
        now = datetime.now(timezone.utc)
        documents = []

        for raw in raw_rows:
            raw_name = (raw.get("name") or "").strip()
            if not raw_name:
                continue
            name = normalize_storage_interface_name(raw_name)
            canon = canonicalize_interface_name(name)

            previous_doc = previous.get(canon) or previous.get(name) or previous.get(raw_name)
            util = _compute_utilization(
                current=raw,
                previous=previous_doc,
                now=now,
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
                    speed_bps=raw.get("speed_bps"),
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


def _compute_utilization(
    current: dict,
    previous: dict | None,
    now: datetime,
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

    speed_bps = current.get("speed_bps") or previous.get("speedBps")
    if not speed_bps or speed_bps <= 0:
        return empty

    prev_ts = previous.get("timestamp")
    if not prev_ts:
        return empty
    if getattr(prev_ts, "tzinfo", None) is None:
        prev_ts = prev_ts.replace(tzinfo=timezone.utc)

    interval = (now - prev_ts).total_seconds()
    if interval <= 0:
        return empty

    rx_delta = _counter_delta(
        current.get("rx_bytes", 0),
        previous.get("rxBytes", 0),
    )
    tx_delta = _counter_delta(
        current.get("tx_bytes", 0),
        previous.get("txBytes", 0),
    )

    rx_bps = (rx_delta * 8) / interval
    tx_bps = (tx_delta * 8) / interval

    rx_util = round(min(max((rx_bps / speed_bps) * 100.0, 0.0), 100.0), 4)
    tx_util = round(min(max((tx_bps / speed_bps) * 100.0, 0.0), 100.0), 4)
    overall = round(max(rx_util, tx_util), 4)

    return {
        "utilization": overall,
        "rx_utilization": rx_util,
        "tx_utilization": tx_util,
    }


def _counter_delta(current: int, previous: int) -> int:
    """Handle 32/64-bit counter wrap."""
    try:
        cur = int(current or 0)
        prev = int(previous or 0)
    except (TypeError, ValueError):
        return 0
    if cur >= prev:
        return cur - prev
    # Wrap — assume 32-bit unless previous looks like HC (very large)
    modulus = 2**64 if prev > 2**32 else 2**32
    return (cur + modulus) - prev


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
