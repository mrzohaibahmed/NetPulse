"""
collector.py
============
Orchestrates interface discovery for managed network devices.

Flow
----
1. Validate device (online, credentials available)
2. Collect raw CLI outputs via SSH (``ssh_collector``)
3. Parse vendor output (``parser``)
4. Normalise to vendor-independent schema (``normalizer``)
5. Classify ports / neighbors (``classifier``)
6. Upsert into MongoDB ``interfaces`` collection

Completely independent of ping / Nmap monitoring services.
Designed so a future SNMP or Storm Detection collector can plug in at step 2
without changing persistence or API layers.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from config.database import MAX_INTERFACE_THREADS, db
from models.interface import create_interface
from services.interface_collection.classifier import classify_interface
from services.interface_collection.naming import canonicalize_interface_name
from services.interface_collection.normalizer import normalize_raw_interface
from services.interface_collection.parser import dedupe_by_name, parse_interface_outputs
from services.interface_collection.ssh_collector import (
    SSHCollectorError,
    collect_raw_outputs_for_device,
)
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("interface")

# Device types that typically expose switchport / interface inventories.
_SWITCH_LIKE_TYPES = frozenset({
    "switch", "switches", "router", "routers", "firewall", "firewalls",
    "l3 switch", "l2 switch", "core switch", "access switch",
})


def ensure_interface_indexes() -> None:
    """Create indexes on ``interfaces`` (safe to call repeatedly)."""
    try:
        db.interfaces.create_index(
            [("deviceId", 1), ("name", 1)],
            unique=True,
            name="uniq_device_interface_name",
        )
        db.interfaces.create_index([("deviceId", 1)], name="idx_interfaces_device")
        db.interfaces.create_index([("lastUpdated", -1)], name="idx_interfaces_updated")
        logger.info("[IFACE] MongoDB indexes ensured on interfaces collection")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[IFACE] Failed to ensure indexes: %s", exc)


def discover_device_interfaces(device: dict) -> dict:
    """
    Discover and persist interfaces for a single device.

    Parameters
    ----------
    device : dict
        MongoDB device document (must include ``_id``, ``ipAddress``).

    Returns
    -------
    dict
        {
          "success": bool,
          "ip": str,
          "deviceId": str,
          "discovered": int,
          "upserted": int,
          "error": str | None,
        }

    Notes
    -----
    Never raises. Safe for scheduler / bulk callers.
    """
    ip_address = device.get("ipAddress", "unknown")
    hostname = device.get("hostname", ip_address)
    device_id: ObjectId = device["_id"]

    if device.get("status") != "Online":
        logger.info(
            "[IFACE] Skipping offline device %s (%s)", hostname, ip_address
        )
        return {
            "success": False,
            "ip": ip_address,
            "deviceId": str(device_id),
            "discovered": 0,
            "upserted": 0,
            "error": "Device is not online",
        }

    logger.info("[IFACE] Starting discovery | host=%s (%s)", hostname, ip_address)
    start = time.monotonic()

    try:
        outputs, vendor = collect_raw_outputs_for_device(device)
        raw_interfaces = parse_interface_outputs(outputs, vendor=vendor)
        raw_interfaces = dedupe_by_name(raw_interfaces)

        if not raw_interfaces:
            logger.warning(
                "[IFACE] No interfaces parsed | host=%s vendor=%s",
                ip_address,
                vendor,
            )
            return {
                "success": False,
                "ip": ip_address,
                "deviceId": str(device_id),
                "discovered": 0,
                "upserted": 0,
                "error": "No interfaces found in device output",
            }

        upserted = persist_interfaces(
            device_id=device_id,
            hostname=hostname,
            ip_address=ip_address,
            raw_interfaces=raw_interfaces,
            collection_method="ssh",
        )

        elapsed = round(time.monotonic() - start, 2)
        logger.info(
            "[IFACE] Discovery completed in %.2fs | host=%s | discovered=%d upserted=%d",
            elapsed,
            ip_address,
            len(raw_interfaces),
            upserted,
        )

        return {
            "success": True,
            "ip": ip_address,
            "deviceId": str(device_id),
            "discovered": len(raw_interfaces),
            "upserted": upserted,
            "error": None,
        }

    except SSHCollectorError as exc:
        logger.error("[IFACE] SSH collection failed | host=%s | %s", ip_address, exc)
        return {
            "success": False,
            "ip": ip_address,
            "deviceId": str(device_id),
            "discovered": 0,
            "upserted": 0,
            "error": str(exc),
        }

    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[IFACE] Unexpected error | host=%s | %s", ip_address, exc
        )
        return {
            "success": False,
            "ip": ip_address,
            "deviceId": str(device_id),
            "discovered": 0,
            "upserted": 0,
            "error": str(exc),
        }


def persist_interfaces(
    device_id: ObjectId,
    hostname: str,
    ip_address: str,
    raw_interfaces: list[dict],
    collection_method: str = "ssh",
) -> int:
    """
    Normalise, classify, and upsert interface documents keyed by ``(deviceId, name)``.

    Preserves administrator overrides for ``isProtected`` and an explicit
    ``monitoringEnabled=false`` across rediscovery.

    Returns the number of interfaces written (upserted or updated).
    """
    now = datetime.now(timezone.utc)
    written = 0
    seen_names: set[str] = set()
    seen_canons: set[str] = set()

    # Preload existing docs keyed by canonical name for override + alias collapse
    existing_by_canon: dict[str, dict] = {}
    for doc in db.interfaces.find({"deviceId": device_id}):
        canon_key = canonicalize_interface_name(doc.get("name"))
        if canon_key:
            existing_by_canon[canon_key] = doc

    for raw in raw_interfaces:
        try:
            normalised = normalize_raw_interface(raw)
        except (TypeError, ValueError) as exc:
            logger.warning("[IFACE] Skipping invalid interface row: %s", exc)
            continue

        name = normalised["name"]
        canon = canonicalize_interface_name(name)
        seen_names.add(name)
        seen_canons.add(canon)

        existing = existing_by_canon.get(canon)
        if existing:
            if existing.get("isProtected") is not None:
                normalised["isProtected"] = bool(existing.get("isProtected"))
            # Honour explicit disable; classifier still forces off when admin-down
            if existing.get("monitoringEnabled") is False:
                normalised["monitoringEnabled"] = False

        classified = classify_interface(normalised)

        document = create_interface(
            device_id=device_id,
            hostname=hostname,
            ip_address=ip_address,
            name=name,
            description=classified["description"],
            admin_status=classified["adminStatus"],
            oper_status=classified["operStatus"],
            mode=classified["portMode"],
            port_mode=classified["portMode"],
            is_access=classified["isAccess"],
            is_trunk=classified["isTrunk"],
            is_uplink=classified.get("isUplink", False),
            is_infrastructure=classified.get("isInfrastructure", False),
            is_management=classified.get("isManagement", False),
            is_protected=classified.get("isProtected", False),
            monitoring_enabled=classified.get("monitoringEnabled", True),
            access_vlan=classified.get("accessVlan"),
            voice_vlan=classified.get("voiceVlan"),
            native_vlan=classified.get("nativeVlan"),
            allowed_vlans=classified.get("allowedVlans"),
            vlan=classified.get("vlan") or "",
            speed=classified["speed"],
            speed_mbps=classified.get("speedMbps"),
            duplex=classified["duplex"],
            neighbor=classified.get("neighbor"),
            if_index=classified.get("ifIndex"),
            mac_address=classified.get("macAddress") or "",
            vendor=classified.get("vendor") or "",
            collection_method=collection_method,
        )

        # Preserve original createdAt on updates
        update_fields = {k: v for k, v in document.items() if k != "createdAt"}
        update_fields["lastUpdated"] = now
        update_fields["updatedAt"] = now

        # Upsert by exact name; also collapse alias duplicates below
        result = db.interfaces.update_one(
            {"deviceId": device_id, "name": name},
            {
                "$set": update_fields,
                "$setOnInsert": {"createdAt": now},
            },
            upsert=True,
        )
        if result.acknowledged:
            written += 1

        # Remove alias duplicates (e.g. GigabitEthernet1/0/1 vs Gi1/0/1)
        if existing and existing.get("name") and existing["name"] != name:
            db.interfaces.delete_one({"_id": existing["_id"]})

    # Remove stale interfaces no longer present on the device (by canonical name)
    if seen_canons:
        stale_ids = []
        for doc in db.interfaces.find({"deviceId": device_id}, {"_id": 1, "name": 1}):
            if canonicalize_interface_name(doc.get("name")) not in seen_canons:
                stale_ids.append(doc["_id"])
        if stale_ids:
            delete_result = db.interfaces.delete_many({"_id": {"$in": stale_ids}})
            if delete_result.deleted_count:
                logger.info(
                    "[IFACE] Removed %d stale interface(s) | deviceId=%s",
                    delete_result.deleted_count,
                    device_id,
                )

    return written


def discover_all_switch_interfaces() -> dict:
    """
    Discover interfaces on all eligible online managed switches.

    Eligibility
    -----------
    - ``status == "Online"``
    - ``deviceType`` looks like a switch/router/firewall, **or** the device
      has an explicit ``credentials`` sub-document configured

    Returns
    -------
    dict
        ``{total, discovered_devices, failed, interface_count, errors}``
    """
    logger.info("[IFACE] Bulk interface discovery started")
    start = time.monotonic()

    candidates = list(db.devices.find({"status": "Online"}))
    eligible = [d for d in candidates if _is_interface_discovery_candidate(d)]
    total = len(eligible)

    if total == 0:
        logger.info("[IFACE] No eligible online devices for interface discovery")
        return {
            "total": 0,
            "discovered_devices": 0,
            "failed": 0,
            "interface_count": 0,
            "errors": [],
        }

    logger.info("[IFACE] %d device(s) queued for interface discovery", total)

    discovered_devices = 0
    failed = 0
    interface_count = 0
    errors: list[dict[str, Any]] = []

    workers = max(int(MAX_INTERFACE_THREADS), 1)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(discover_device_interfaces, device): device
            for device in eligible
        }
        for future in as_completed(future_map):
            result = future.result()
            if result["success"]:
                discovered_devices += 1
                interface_count += int(result.get("upserted") or 0)
            else:
                failed += 1
                errors.append({
                    "ip": result.get("ip"),
                    "deviceId": result.get("deviceId"),
                    "error": result.get("error"),
                })

    elapsed = round(time.monotonic() - start, 2)
    logger.info(
        "[IFACE] Bulk discovery finished in %.2fs | total=%d ok=%d failed=%d ifaces=%d",
        elapsed,
        total,
        discovered_devices,
        failed,
        interface_count,
    )

    return {
        "total": total,
        "discovered_devices": discovered_devices,
        "failed": failed,
        "interface_count": interface_count,
        "errors": errors,
    }


def get_interfaces(
    device_id: ObjectId | None = None,
    *,
    skip: int = 0,
    limit: int = 100,
    search: str | None = None,
    admin_status: str | None = None,
    oper_status: str | None = None,
    mode: str | None = None,
) -> tuple[list[dict], int]:
    """
    Query the ``interfaces`` collection with optional filters.

    Returns
    -------
    tuple
        (documents, total_count)
    """
    query: dict[str, Any] = {}
    if device_id is not None:
        query["deviceId"] = device_id

    if admin_status and admin_status.lower() != "all":
        query["adminStatus"] = admin_status.lower()
    if oper_status and oper_status.lower() != "all":
        query["operStatus"] = oper_status.lower()
    if mode and mode.lower() != "all":
        query["mode"] = mode.lower()

    if search:
        import re
        pattern = re.compile(re.escape(search.strip()), re.IGNORECASE)
        query["$or"] = [
            {"name": pattern},
            {"description": pattern},
            {"hostname": pattern},
            {"ipAddress": pattern},
            {"vlan": pattern},
        ]

    total = db.interfaces.count_documents(query)
    cursor = (
        db.interfaces.find(query)
        .sort([("hostname", 1), ("name", 1)])
        .skip(skip)
        .limit(limit)
    )
    return list(cursor), total


def _is_interface_discovery_candidate(device: dict) -> bool:
    """Return True when the device should be included in bulk discovery."""
    if device.get("credentials"):
        return True

    device_type = (device.get("deviceType") or device.get("type") or "").strip().lower()
    if device_type in _SWITCH_LIKE_TYPES:
        return True
    # Partial match: "Core Switch", "Layer-3 Switch", etc.
    return any(token in device_type for token in ("switch", "router", "firewall"))
