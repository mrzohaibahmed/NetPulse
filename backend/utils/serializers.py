from datetime import timezone


def format_datetime(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        if getattr(value, "tzinfo", None) is None:
            return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        iso = value.isoformat()
        return iso.replace("+00:00", "Z") if iso.endswith("+00:00") else iso
    return value


def get_device_type(device):
    return device.get("deviceType") or device.get("type")


def serialize_network_info(network_info: dict | None) -> dict | None:
    """
    Serialise the networkInfo sub-document stored by nmap_service.

    Converts the ``lastScan`` datetime to an ISO-8601 string and returns
    the dict as-is for all other fields (ports, services, os, etc.).
    Returns ``None`` when networkInfo has not yet been populated.
    """
    if not network_info:
        return None

    result = dict(network_info)  # shallow copy — do not mutate the mongo doc

    # Ensure lastScan datetime is JSON-serialisable.
    if "lastScan" in result:
        result["lastScan"] = format_datetime(result["lastScan"])

    return result


def serialize_device(device):
    return {
        "_id": str(device["_id"]),
        "hostname": device.get("hostname"),
        "ipAddress": device.get("ipAddress"),
        "deviceType": get_device_type(device),
        "critical": device.get("critical", False),
        "monitor": device.get("monitor", True),
        "status": device.get("status", "Unknown"),
        "lastSeen": format_datetime(device.get("lastSeen")),
        "lastCheckedAt": format_datetime(device.get("lastCheckedAt")),
        "responseTime": device.get("responseTime"),
        "consecutiveFailures": device.get("consecutiveFailures", 0),
        "pingInterval": device.get("pingInterval"),
        "pingTimeoutMs": device.get("pingTimeoutMs"),
        "pingRetries": device.get("pingRetries"),
        "createdAt": format_datetime(device.get("createdAt")),
        "updatedAt": format_datetime(device.get("updatedAt")),
        # Nmap metadata — present after the first successful scan, None before.
        "networkInfo": serialize_network_info(device.get("networkInfo")),
    }

