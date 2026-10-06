"""
BSON Serializer and MongoDB Repository for Server Hardware Persistence (Phase 3C).

Provides atomic, race-safe storage for current hardware snapshots and append-only historical records.
Consumes normalized Phase 3B ServerHardware domain objects. Does NOT perform HTTP requests, Redfish parsing,
or operational health calculations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo.errors import DuplicateKeyError, PyMongoError

from config.database import db
from services.server_hardware.models import ServerHardware
from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("server_hardware.persistence")


class ServerHardwarePersistenceError(Exception):
    """Raised when a server hardware persistence or serialization operation fails."""


def _normalize_device_id(device_id: str | ObjectId) -> ObjectId:
    """
    Normalize device identifier to BSON ObjectId.

    Accepts ObjectId instances or valid 24-character hexadecimal strings.
    Raises ServerHardwarePersistenceError if invalid.
    """
    if isinstance(device_id, ObjectId):
        return device_id
    if isinstance(device_id, str) and ObjectId.is_valid(device_id):
        return ObjectId(device_id)
    raise ServerHardwarePersistenceError(f"Invalid device identifier format: {device_id}")


def serialize_server_hardware(
    hardware: ServerHardware,
    device_id: ObjectId,
    observed_at: datetime,
) -> dict[str, Any]:
    """
    Serialize ServerHardware dataclass object into a BSON-safe MongoDB document.

    Enforces schemaVersion = 1, camelCase field naming, and explicit type preservation.
    Excludes credentials, tokens, and raw Redfish payloads.
    """
    if not isinstance(hardware, ServerHardware):
        raise ServerHardwarePersistenceError("Invalid ServerHardware object")
    if not isinstance(observed_at, datetime):
        raise ServerHardwarePersistenceError("observed_at must be a datetime object")

    # Ensure UTC timezone awareness
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)

    return {
        "deviceId": device_id,
        "schemaVersion": 1,
        "observedAt": observed_at,
        "identity": {
            "hostname": hardware.identity.hostname,
            "manufacturer": hardware.identity.manufacturer,
            "model": hardware.identity.model,
            "serialNumber": hardware.identity.serial_number,
            "uuid": hardware.identity.uuid,
            "assetTag": hardware.identity.asset_tag,
        },
        "firmware": {
            "iloGeneration": hardware.firmware.ilo_generation,
            "iloFirmwareVersion": hardware.firmware.ilo_firmware_version,
            "biosVersion": hardware.firmware.bios_version,
        },
        "power": {
            "powerState": hardware.power.power_state,
            "powerConsumedWatts": hardware.power.power_consumed_watts,
            "powerCapacityWatts": hardware.power.power_capacity_watts,
            "powerSupplies": [
                {
                    "id": ps.id,
                    "name": ps.name,
                    "model": ps.model,
                    "powerCapacityWatts": ps.power_capacity_watts,
                    "lastPowerOutputWatts": ps.last_power_output_watts,
                    "lineInputVoltage": ps.line_input_voltage,
                    "status": ps.status.to_dict() if ps.status else None,
                }
                for ps in hardware.power.power_supplies
            ],
        },
        "processors": [
            {
                "id": p.id,
                "name": p.name,
                "manufacturer": p.manufacturer,
                "model": p.model,
                "cores": p.cores,
                "threads": p.threads,
                "speedMhz": p.speed_mhz,
                "status": p.status.to_dict() if p.status else None,
            }
            for p in hardware.processors
        ],
        "memory": [
            {
                "id": m.id,
                "name": m.name,
                "capacityBytes": m.capacity_bytes,
                "speedMhz": m.speed_mhz,
                "manufacturer": m.manufacturer,
                "partNumber": m.part_number,
                "serialNumber": m.serial_number,
                "status": m.status.to_dict() if m.status else None,
            }
            for m in hardware.memory
        ],
        "storage": {
            "controllers": [
                {
                    "id": c.id,
                    "name": c.name,
                    "model": c.model,
                    "serialNumber": c.serial_number,
                    "firmwareVersion": c.firmware_version,
                    "status": c.status.to_dict() if c.status else None,
                    "sourceType": c.source_type,
                }
                for c in hardware.storage.controllers
            ],
            "physicalDrives": [
                {
                    "id": d.id,
                    "name": d.name,
                    "controllerId": d.controller_id,
                    "model": d.model,
                    "serialNumber": d.serial_number,
                    "capacityBytes": d.capacity_bytes,
                    "mediaType": d.media_type,
                    "protocol": d.protocol,
                    "status": d.status.to_dict() if d.status else None,
                }
                for d in hardware.storage.physical_drives
            ],
            "logicalVolumes": [
                {
                    "id": v.id,
                    "name": v.name,
                    "controllerId": v.controller_id,
                    "capacityBytes": v.capacity_bytes,
                    "raidType": v.raid_type,
                    "status": v.status.to_dict() if v.status else None,
                }
                for v in hardware.storage.logical_volumes
            ],
        },
        "networkInterfaces": [
            {
                "id": n.id,
                "name": n.name,
                "macAddress": n.mac_address,
                "linkStatus": n.link_status,
                "speedMbps": n.speed_mbps,
                "manufacturer": n.manufacturer,
                "model": n.model,
                "isManagementInterface": n.is_management_interface,
                "status": n.status.to_dict() if n.status else None,
            }
            for n in hardware.network_interfaces
        ],
        "temperatures": [
            {
                "id": t.id,
                "name": t.name,
                "readingCelsius": t.reading_celsius,
                "upperThresholdCritical": t.upper_threshold_critical,
                "status": t.status.to_dict() if t.status else None,
            }
            for t in hardware.temperatures
        ],
        "fans": [
            {
                "id": f.id,
                "name": f.name,
                "readingRpm": f.reading_rpm,
                "readingPercent": f.reading_percent,
                "status": f.status.to_dict() if f.status else None,
            }
            for f in hardware.fans
        ],
        "capabilities": hardware.capabilities.to_dict(),
    }


def save_current_server_hardware(
    device_id: str | ObjectId,
    hardware: ServerHardware,
    observed_at: datetime,
    *,
    database: Any = None,
) -> bool:
    """
    Persist complete normalized ServerHardware snapshot to server_hardware_current.

    Uses atomic document replacement (upsert=False) to guarantee that stale fields or removed components
    are completely replaced. Protects against out-of-order older observations overwriting newer data.
    """
    target_oid = _normalize_device_id(device_id)
    doc = serialize_server_hardware(hardware, target_oid, observed_at)
    doc["updatedAt"] = utc_now()

    target_db = database if database is not None else db

    try:
        # Step 1: Attempt atomic document REPLACEMENT if existing.observedAt <= incoming.observedAt
        # upsert=False prevents DuplicateKeyError on non-matching filters
        res = target_db.server_hardware_current.replace_one(
            {
                "deviceId": target_oid,
                "$or": [
                    {"observedAt": {"$lte": observed_at}},
                    {"observedAt": {"$exists": False}},
                ],
            },
            doc,
            upsert=False,
        )

        if res.matched_count > 0:
            logger.info(
                "[HARDWARE_PERSISTENCE] Replaced current hardware snapshot | deviceId=%s | observedAt=%s",
                target_oid,
                observed_at,
            )
            return True

        # Step 2: Matched count == 0. Check if document exists for deviceId.
        existing = target_db.server_hardware_current.find_one(
            {"deviceId": target_oid},
            {"_id": 1, "observedAt": 1},
        )

        if existing:
            existing_obs = existing.get("observedAt")
            if existing_obs is not None and existing_obs > observed_at:
                # Existing document is strictly newer; incoming snapshot is stale.
                logger.info(
                    "[HARDWARE_PERSISTENCE] Stale hardware snapshot ignored | deviceId=%s | incomingObservedAt=%s | existingObservedAt=%s",
                    target_oid,
                    observed_at,
                    existing_obs,
                )
                return False

            # Document exists with observedAt <= incoming observedAt (e.g. inserted between Step 1 and Step 2).
            retry_res = target_db.server_hardware_current.replace_one(
                {
                    "deviceId": target_oid,
                    "$or": [
                        {"observedAt": {"$lte": observed_at}},
                        {"observedAt": {"$exists": False}},
                    ],
                },
                doc,
                upsert=False,
            )
            return retry_res.matched_count > 0

        # Step 3: No current document exists: attempt insert_one
        try:
            target_db.server_hardware_current.insert_one(doc)
            logger.info(
                "[HARDWARE_PERSISTENCE] Inserted initial current hardware snapshot | deviceId=%s | observedAt=%s",
                target_oid,
                observed_at,
            )
            return True
        except DuplicateKeyError:
            # Race condition: concurrent worker inserted first. Retry atomic replace_one.
            retry_res = target_db.server_hardware_current.replace_one(
                {
                    "deviceId": target_oid,
                    "$or": [
                        {"observedAt": {"$lte": observed_at}},
                        {"observedAt": {"$exists": False}},
                    ],
                },
                doc,
                upsert=False,
            )
            return retry_res.matched_count > 0

    except PyMongoError as exc:
        raise ServerHardwarePersistenceError(
            f"Failed to persist current server hardware for device {target_oid}: {exc}"
        ) from exc


def append_server_hardware_history(
    device_id: str | ObjectId,
    hardware: ServerHardware,
    observed_at: datetime,
    *,
    database: Any = None,
) -> bool:
    """
    Append point-in-time normalized snapshot to server_hardware_history.

    Idempotent via compound unique index on (deviceId, observedAt).
    """
    target_oid = _normalize_device_id(device_id)
    doc = serialize_server_hardware(hardware, target_oid, observed_at)

    target_db = database if database is not None else db

    try:
        target_db.server_hardware_history.insert_one(doc)
        logger.info(
            "[HARDWARE_HISTORY] Appended hardware history snapshot | deviceId=%s | observedAt=%s",
            target_oid,
            observed_at,
        )
        return True
    except DuplicateKeyError:
        logger.info(
            "[HARDWARE_HISTORY] Duplicate history snapshot ignored | deviceId=%s | observedAt=%s",
            target_oid,
            observed_at,
        )
        return False
    except PyMongoError as exc:
        raise ServerHardwarePersistenceError(
            f"Failed to append server hardware history for device {target_oid}: {exc}"
        ) from exc


def get_current_server_hardware(
    device_id: str | ObjectId,
    *,
    database: Any = None,
) -> dict[str, Any] | None:
    """Retrieve current hardware snapshot for device_id, or None if missing."""
    target_oid = _normalize_device_id(device_id)
    target_db = database if database is not None else db
    try:
        return target_db.server_hardware_current.find_one({"deviceId": target_oid})
    except PyMongoError as exc:
        raise ServerHardwarePersistenceError(
            f"Failed to fetch current server hardware for device {target_oid}: {exc}"
        ) from exc


def get_server_hardware_history(
    device_id: str | ObjectId,
    *,
    limit: int = 50,
    database: Any = None,
) -> list[dict[str, Any]]:
    """Retrieve historical hardware snapshots for device_id ordered by observedAt DESC."""
    target_oid = _normalize_device_id(device_id)
    target_db = database if database is not None else db
    try:
        cursor = target_db.server_hardware_history.find(
            {"deviceId": target_oid}
        ).sort("observedAt", -1).limit(limit)
        return list(cursor)
    except PyMongoError as exc:
        raise ServerHardwarePersistenceError(
            f"Failed to fetch server hardware history for device {target_oid}: {exc}"
        ) from exc
