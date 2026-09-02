"""Site monitoring aggregation for the Enterprise Dashboard."""

from __future__ import annotations

from models.location import (
    DEFAULT_SITE_LOCATION,
    SITE_LOCATIONS,
    canonical_site_location,
    isp_slot_ids_for_location,
)
from services.isp_service import list_isp_connections
from utils.serializers import format_datetime, get_device_type, serialize_isp_connection

_SERVER_TYPE_MATCH = {
    "$expr": {
        "$eq": [
            {
                "$toLower": {
                    "$ifNull": ["$deviceType", {"$ifNull": ["$type", ""]}],
                }
            },
            "server",
        ]
    }
}


def _serialize_site_server(device: dict) -> dict:
    return {
        "id": str(device["_id"]),
        "hostname": device.get("hostname"),
        "ipAddress": device.get("ipAddress"),
        "deviceType": get_device_type(device),
        "status": device.get("status", "Unknown"),
        "responseTime": device.get("responseTime"),
        "lastSeen": format_datetime(device.get("lastSeen")),
        "lastCheckedAt": format_datetime(device.get("lastCheckedAt")),
        "location": device.get("location"),
        "monitor": bool(device.get("monitor", True)),
        "critical": bool(device.get("critical", False)),
    }


def _normalize_site_isps(isps: list[dict], location: str) -> list[dict]:
    """Return up to three ISP records for a site, preserving slot order."""
    location_key = canonical_site_location(location) or DEFAULT_SITE_LOCATION
    by_id = {isp["_id"]: isp for isp in isps}
    normalized: list[dict] = []
    for index, slot_id in enumerate(isp_slot_ids_for_location(location_key), start=1):
        existing = by_id.get(slot_id)
        if existing and (canonical_site_location(existing.get("location")) or DEFAULT_SITE_LOCATION) == location_key:
            normalized.append(serialize_isp_connection(existing))
            continue
        normalized.append({
            "id": slot_id,
            "name": f"ISP {index}",
            "target": "",
            "location": location_key,
            "monitor": False,
            "status": "Unknown",
            "responseTime": None,
            "lastSeen": None,
            "lastCheckedAt": None,
            "consecutiveFailures": 0,
            "lastPingAttemptId": None,
            "lastPingStartedAt": None,
            "createdAt": None,
            "updatedAt": None,
        })
    return normalized


def _ordered_site_names(*, isps: list[dict], servers: list[dict]) -> list[str]:
    discovered: set[str] = set(SITE_LOCATIONS)
    for isp in isps:
        discovered.add(canonical_site_location(isp.get("location")) or DEFAULT_SITE_LOCATION)
    for server in servers:
        location = canonical_site_location(server.get("location"))
        if location:
            discovered.add(location)

    ordered: list[str] = []
    for location in SITE_LOCATIONS:
        if location in discovered:
            ordered.append(location)
            discovered.discard(location)
    ordered.extend(sorted(discovered))
    return ordered


def build_site_monitoring_payload(db) -> dict:
    """Build grouped ISP + server monitoring data in a single query pass."""
    isps = list_isp_connections()
    servers = list(db.devices.find(_SERVER_TYPE_MATCH).sort("hostname", 1))

    sites = []
    for site_name in _ordered_site_names(isps=isps, servers=servers):
        site_isps = _normalize_site_isps(isps, site_name)
        site_servers = [
            _serialize_site_server(server)
            for server in servers
            if canonical_site_location(server.get("location")) == site_name
        ]
        sites.append({
            "name": site_name,
            "isps": site_isps,
            "servers": site_servers,
        })

    return {"sites": sites}
