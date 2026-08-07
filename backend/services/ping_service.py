from ping3 import ping

from services.settings_service import get_ping_config
from utils.utc import utc_now

STATUS_ONLINE = "Online"
STATUS_NOT_REACHABLE = "Not Reachable"
STATUS_OFFLINE_CRITICAL = "Offline (Critical)"


def classify_failure_status(critical=False):
    if critical:
        return STATUS_OFFLINE_CRITICAL
    return STATUS_NOT_REACHABLE


def ping_device(ip_address, critical=False, timeout_ms=None, retries=None, device=None):
    """
    ICMP ping with configurable timeout and retries (FR2.2, FR2.3).
    Classifies failures as Not Reachable or Offline (Critical) (FR3.1–FR3.3).
    lastSeen is only set on success (FR3.4) — always timezone-aware UTC.
    """
    config = get_ping_config(device)
    timeout_ms = int(timeout_ms if timeout_ms is not None else config["timeout_ms"])
    retries = int(retries if retries is not None else config["retries"])
    timeout_s = max(timeout_ms, 100) / 1000.0
    attempts = max(retries, 1)

    last_error = "Device is unreachable"
    response_time = None

    for _ in range(attempts):
        try:
            response = ping(ip_address, timeout=timeout_s)

            if response is None or response is False or not isinstance(response, (int, float)):
                last_error = "Device is unreachable"
                continue

            response_time = round(response * 1000, 2)
            return {
                "success": True,
                "status": STATUS_ONLINE,
                "responseTime": response_time,
                "lastSeen": utc_now(),
                "message": "Device is reachable",
                "attempts": attempts,
            }
        except Exception as error:
            last_error = str(error)

    return {
        "success": False,
        "status": classify_failure_status(critical),
        "responseTime": None,
        "lastSeen": None,
        "message": last_error,
        "attempts": attempts,
    }
