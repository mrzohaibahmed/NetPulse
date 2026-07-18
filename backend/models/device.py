from datetime import datetime, timezone


def create_device(
    hostname,
    ip_address,
    device_type,
    critical=False,
    monitor=True,
    ping_interval=None,
    ping_timeout_ms=None,
    ping_retries=None,
):
    now = datetime.now(timezone.utc)

    return {
        "hostname": hostname,
        "ipAddress": ip_address,
        "deviceType": device_type,
        "critical": critical,
        "monitor": monitor,
        "status": "Unknown",
        "responseTime": None,
        "lastSeen": None,
        "lastCheckedAt": None,
        "consecutiveFailures": 0,
        "pingInterval": ping_interval,
        "pingTimeoutMs": ping_timeout_ms,
        "pingRetries": ping_retries,
        "createdAt": now,
        "updatedAt": now,
    }
