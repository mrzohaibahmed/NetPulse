"""ISP connectivity document helpers."""

from __future__ import annotations

from utils.utc import utc_now

MAX_ISP_CONNECTIONS = 3

DEFAULT_ISP_SLOTS = ("isp-1", "isp-2", "isp-3")

STATUS_UNKNOWN = "Unknown"
STATUS_ONLINE = "Online"
STATUS_OFFLINE = "Offline"


def create_isp_connection(
    *,
    isp_id: str,
    name: str,
    target: str = "",
    monitor: bool = False,
) -> dict:
    """Build a new ``ispConnections`` document."""
    now = utc_now()
    return {
        "_id": isp_id,
        "name": name.strip(),
        "target": target.strip(),
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
    """Three placeholder ISP slots (admin-configurable names/targets)."""
    return [
        create_isp_connection(isp_id=slot, name=f"ISP {index}", monitor=False)
        for index, slot in enumerate(DEFAULT_ISP_SLOTS, start=1)
    ]
