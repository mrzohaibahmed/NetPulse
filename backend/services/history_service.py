from config.database import db
from models.ping_history import create_ping_history


def save_ping_history(device, ping_result, scan_type="Manual"):
    """Save one ping result into MongoDB."""
    history = create_ping_history(
        device_id=device["_id"],
        hostname=device.get("hostname", "Unknown"),
        ip_address=device.get("ipAddress"),
        status=ping_result["status"],
        response_time=ping_result["responseTime"],
        scan_type=scan_type
    )

    db.pingHistory.insert_one(history)
