"""
MongoDB index and $jsonSchema validator definitions for server hardware collections (Phase 3C).
"""

from __future__ import annotations

from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import PyMongoError

from config.database import db
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("server_hardware.indexes")

CURRENT_UNIQUE_INDEX_NAME = "uniq_server_hardware_current_deviceId"
HISTORY_COMPOUND_INDEX_NAME = "idx_server_hardware_history_device_obs"
HISTORY_UNIQUE_INDEX_NAME = "uniq_server_hardware_history_device_obs"
HISTORY_TTL_INDEX_NAME = "ttl_server_hardware_history_observedAt"

# 30-day default TTL retention period (seconds)
HISTORY_TTL_SECONDS = 2592000

CURRENT_SCHEMA_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "deviceId",
            "schemaVersion",
            "observedAt",
            "updatedAt",
            "identity",
            "firmware",
            "power",
            "processors",
            "memory",
            "storage",
            "networkInterfaces",
            "temperatures",
            "fans",
            "capabilities",
        ],
        "properties": {
            "deviceId": {"bsonType": "objectId"},
            "schemaVersion": {"bsonType": "int"},
            "observedAt": {"bsonType": "date"},
            "updatedAt": {"bsonType": "date"},
            "identity": {"bsonType": "object"},
            "firmware": {"bsonType": "object"},
            "power": {"bsonType": "object"},
            "processors": {"bsonType": "array"},
            "memory": {"bsonType": "array"},
            "storage": {"bsonType": "object"},
            "networkInterfaces": {"bsonType": "array"},
            "temperatures": {"bsonType": "array"},
            "fans": {"bsonType": "array"},
            "capabilities": {"bsonType": "object"},
        },
    }
}

HISTORY_SCHEMA_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "deviceId",
            "schemaVersion",
            "observedAt",
            "identity",
            "firmware",
            "power",
            "processors",
            "memory",
            "storage",
            "networkInterfaces",
            "temperatures",
            "fans",
            "capabilities",
        ],
        "properties": {
            "deviceId": {"bsonType": "objectId"},
            "schemaVersion": {"bsonType": "int"},
            "observedAt": {"bsonType": "date"},
            "identity": {"bsonType": "object"},
            "firmware": {"bsonType": "object"},
            "power": {"bsonType": "object"},
            "processors": {"bsonType": "array"},
            "memory": {"bsonType": "array"},
            "storage": {"bsonType": "object"},
            "networkInterfaces": {"bsonType": "array"},
            "temperatures": {"bsonType": "array"},
            "fans": {"bsonType": "array"},
            "capabilities": {"bsonType": "object"},
        },
    }
}


def ensure_server_hardware_indexes(*, database: Any = None) -> None:
    """
    Ensure indexes and $jsonSchema validators on server_hardware_current and server_hardware_history.

    Safe to call repeatedly on startup.
    """
    target_db = database if database is not None else db

    # 1. server_hardware_current
    try:
        target_db.server_hardware_current.create_index(
            [("deviceId", ASCENDING)],
            unique=True,
            name=CURRENT_UNIQUE_INDEX_NAME,
        )
        logger.info("[SERVER_HARDWARE] Current unique index ensured | name=%s", CURRENT_UNIQUE_INDEX_NAME)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SERVER_HARDWARE] Failed to ensure current unique index: %s", exc)

    try:
        target_db.command(
            "collMod",
            "server_hardware_current",
            validator=CURRENT_SCHEMA_VALIDATOR,
            validationLevel="moderate",
        )
        logger.info("[SERVER_HARDWARE] Current $jsonSchema validator applied")
    except PyMongoError:
        # Collection might not exist yet — will apply when created or on subsequent write
        pass

    # 2. server_hardware_history
    try:
        target_db.server_hardware_history.create_index(
            [("deviceId", ASCENDING), ("observedAt", DESCENDING)],
            name=HISTORY_COMPOUND_INDEX_NAME,
        )
        logger.info("[SERVER_HARDWARE] History compound search index ensured | name=%s", HISTORY_COMPOUND_INDEX_NAME)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SERVER_HARDWARE] Failed to ensure history compound index: %s", exc)

    try:
        target_db.server_hardware_history.create_index(
            [("deviceId", ASCENDING), ("observedAt", ASCENDING)],
            unique=True,
            name=HISTORY_UNIQUE_INDEX_NAME,
        )
        logger.info("[SERVER_HARDWARE] History compound unique index ensured | name=%s", HISTORY_UNIQUE_INDEX_NAME)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SERVER_HARDWARE] Failed to ensure history unique index: %s", exc)

    try:
        target_db.server_hardware_history.create_index(
            [("observedAt", ASCENDING)],
            expireAfterSeconds=HISTORY_TTL_SECONDS,
            name=HISTORY_TTL_INDEX_NAME,
        )
        logger.info("[SERVER_HARDWARE] History TTL index ensured | name=%s | ttl=%ds", HISTORY_TTL_INDEX_NAME, HISTORY_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SERVER_HARDWARE] Failed to ensure history TTL index: %s", exc)

    try:
        target_db.command(
            "collMod",
            "server_hardware_history",
            validator=HISTORY_SCHEMA_VALIDATOR,
            validationLevel="moderate",
        )
        logger.info("[SERVER_HARDWARE] History $jsonSchema validator applied")
    except PyMongoError:
        pass
