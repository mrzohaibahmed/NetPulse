"""ISP connection persistence helpers."""

from __future__ import annotations

import re

from pymongo.errors import DuplicateKeyError

from config.database import db
from models.isp_connection import (
    MAX_ISP_CONNECTIONS,
    create_isp_connection,
    default_isp_connections,
)
from models.location import (
    DEFAULT_SITE_LOCATION,
    isp_slot_ids_for_location,
    validate_location,
)
from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("isp")

HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-zA-Z0-9-]{1,63}(?<!-)(\.(?!-)[a-zA-Z0-9-]{1,63}(?<!-))*\.?$"
)
IPV4_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)$"
)


def validate_target(target: str) -> str:
    """Validate and normalize a ping target (IPv4 or hostname)."""
    value = (target or "").strip()
    if not value:
        return ""
    if IPV4_RE.match(value):
        return value
    if HOSTNAME_RE.match(value):
        return value
    raise ValueError("target must be a valid IPv4 address or hostname")


def ensure_isp_connections() -> None:
    """Seed default ISP slots and migrate legacy records without location."""
    existing = list(db.ispConnections.find({}))
    if not existing:
        docs = default_isp_connections()
        try:
            db.ispConnections.insert_many(docs, ordered=True)
            logger.info("[ISP] Seeded %s default ISP connection slots", len(docs))
        except DuplicateKeyError:
            logger.info("[ISP] Default ISP slots already present")
        return

    for doc in existing:
        if not doc.get("location"):
            db.ispConnections.update_one(
                {"_id": doc["_id"]},
                {"$set": {"location": DEFAULT_SITE_LOCATION, "updatedAt": utc_now()}},
            )

    existing_ids = {doc["_id"] for doc in existing}
    missing_docs = []
    for doc in default_isp_connections():
        if doc["_id"] not in existing_ids:
            missing_docs.append(doc)
    if missing_docs:
        try:
            db.ispConnections.insert_many(missing_docs, ordered=True)
            logger.info("[ISP] Seeded %s additional ISP slots", len(missing_docs))
        except DuplicateKeyError:
            logger.info("[ISP] Additional ISP slots already present")


def count_isp_connections() -> int:
    return db.ispConnections.count_documents({})


def list_isp_connections() -> list[dict]:
    """Return all ISP connections sorted by slot id."""
    return list(db.ispConnections.find({}).sort("_id", 1))


def get_isp_connection(isp_id: str) -> dict | None:
    return db.ispConnections.find_one({"_id": isp_id})


def next_available_slot(location: str = DEFAULT_SITE_LOCATION) -> str | None:
    location = validate_location(location) or DEFAULT_SITE_LOCATION
    existing = {doc["_id"] for doc in db.ispConnections.find({}, {"_id": 1})}
    for slot in isp_slot_ids_for_location(location):
        if slot not in existing:
            return slot
    if len(existing) < MAX_ISP_CONNECTIONS:
        return f"{location.lower().replace(' ', '-')}-isp-extra-{len(existing) + 1}"
    return None


def create_isp_record(
    *,
    name: str,
    target: str = "",
    monitor: bool = False,
    location: str = DEFAULT_SITE_LOCATION,
) -> dict:
    if count_isp_connections() >= MAX_ISP_CONNECTIONS:
        raise ValueError(f"Maximum of {MAX_ISP_CONNECTIONS} ISP connections allowed")

    normalized_target = validate_target(target)
    normalized_location = validate_location(location) or DEFAULT_SITE_LOCATION
    slot = next_available_slot(normalized_location)
    if slot is None:
        raise ValueError(f"Maximum of {MAX_ISP_CONNECTIONS} ISP connections allowed")

    doc = create_isp_connection(
        isp_id=slot,
        name=name,
        target=normalized_target,
        monitor=monitor,
        location=normalized_location,
    )
    db.ispConnections.insert_one(doc)
    return doc


def update_isp_record(
    isp_id: str,
    *,
    name: str | None = None,
    target: str | None = None,
    monitor: bool | None = None,
    location: str | None = None,
) -> dict | None:
    existing = get_isp_connection(isp_id)
    if existing is None:
        slot_location = DEFAULT_SITE_LOCATION
        if isp_id in isp_slot_ids_for_location(DEFAULT_SITE_LOCATION):
            slot_index = isp_slot_ids_for_location(DEFAULT_SITE_LOCATION).index(isp_id) + 1
        else:
            for candidate in ("Karachi", "Lahore"):
                slots = isp_slot_ids_for_location(candidate)
                if isp_id in slots:
                    slot_location = candidate
                    slot_index = slots.index(isp_id) + 1
                    break
            else:
                return None
        cleaned_name = (name or "").strip() or f"ISP {slot_index}"
        doc = create_isp_connection(
            isp_id=isp_id,
            name=cleaned_name,
            target=validate_target(target or ""),
            monitor=bool(monitor) if monitor is not None else False,
            location=validate_location(location) or slot_location,
        )
        db.ispConnections.insert_one(doc)
        return doc

    update: dict = {"updatedAt": utc_now()}
    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("name is required")
        update["name"] = cleaned
    if target is not None:
        update["target"] = validate_target(target)
    if monitor is not None:
        update["monitor"] = bool(monitor)
    if location is not None:
        update["location"] = validate_location(location) or DEFAULT_SITE_LOCATION

    db.ispConnections.update_one({"_id": isp_id}, {"$set": update})
    return get_isp_connection(isp_id)


def delete_isp_record(isp_id: str) -> bool:
    result = db.ispConnections.delete_one({"_id": isp_id})
    return result.deleted_count > 0
