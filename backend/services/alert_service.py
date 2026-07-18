from datetime import datetime, timezone

from config.database import db
from services.email_service import send_critical_offline_alert
from services.ping_service import STATUS_OFFLINE_CRITICAL
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("alert")


def maybe_send_critical_offline_alert(device, previous_status, new_status, consecutive_failures, scan_type="Automatic"):
    """
    Email + history only when a critical device transitions to Offline (Critical)
    after exactly 3 consecutive failures.
    """
    if not device.get("critical"):
        return False

    if new_status != STATUS_OFFLINE_CRITICAL:
        return False

    if consecutive_failures != 3:
        return False

    hostname = device.get("hostname", "unknown")
    ip_address = device.get("ipAddress", "unknown")
    message = (
        f"Critical device {hostname} ({ip_address}) transitioned to "
        f"{STATUS_OFFLINE_CRITICAL} via {scan_type} scan."
    )

    logger.warning(
        "Critical device offline: %s (%s) | previous=%s scan=%s",
        hostname,
        ip_address,
        previous_status,
        scan_type,
    )

    email_sent = send_critical_offline_alert(device, scan_type=scan_type)

    db.alerts.insert_one({
        "deviceId": device.get("_id"),
        "hostname": hostname,
        "ipAddress": ip_address,
        "deviceType": device.get("deviceType") or device.get("type"),
        "status": STATUS_OFFLINE_CRITICAL,
        "message": message,
        "scanType": scan_type,
        "emailSent": bool(email_sent),
        "acknowledged": False,
        "dismissed": False,
        "acknowledgedAt": None,
        "dismissedAt": None,
        "createdAt": datetime.now(timezone.utc),
    })

    return True
