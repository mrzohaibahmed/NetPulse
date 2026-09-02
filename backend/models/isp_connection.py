"""ISP connectivity document helpers."""

from __future__ import annotations

from models.location import (
    DEFAULT_SITE_LOCATION,
    ISPS_PER_SITE,
    SITE_LOCATIONS,
    all_isp_slot_ids,
    isp_slot_ids_for_location,
)
from utils.utc import utc_now

MAX_ISP_CONNECTIONS = len(all_isp_slot_ids())

STATUS_UNKNOWN = "Unknown"
STATUS_ONLINE = "Online"
STATUS_OFFLINE = "Offline"


def create_isp_connection(
    *,
    isp_id: str,
    name: str,
    target: str = "",
    monitor: bool = False,
    location: str = DEFAULT_SITE_LOCATION,
) -> dict:
    """Build a new ``ispConnections`` document."""
    now = utc_now()
    return {
        "_id": isp_id,
        "name": name.strip(),
        "target": target.strip(),
        "location": (location or DEFAULT_SITE_LOCATION).strip(),
        "monitor": bool(monitor),
        "status": STATUS_UNKNOWN,
        "responseTime": None,
        "lastSeen": None,
        "lastCheckedAt": None,
        "consecutiveFailures": 0,
        "lastPingAttemptId": None,
        "lastPingStartedAt": None,
        "createdAt": now,
        "updatedAt": now,
    }


def default_isp_connections() -> list[dict]:
    """Placeholder ISP slots for each configured site (admin-configurable)."""
    docs: list[dict] = []
    for location in SITE_LOCATIONS:
        for index, slot in enumerate(isp_slot_ids_for_location(location), start=1):
            docs.append(
                create_isp_connection(
                    isp_id=slot,
                    name=f"ISP {index}",
                    monitor=False,
                    location=location,
                )
            )
    return docs
