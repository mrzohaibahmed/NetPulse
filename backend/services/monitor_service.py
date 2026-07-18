from datetime import datetime, timezone

from config.database import db
from services.alert_service import maybe_send_critical_offline_alert
from services.history_service import save_ping_history
from services.ping_service import STATUS_ONLINE, ping_device
from services.settings_service import get_ping_config
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("monitor")


def _should_check_now(device, now):
    """Honor per-device interval override within the global scheduler cycle (FR8.1)."""
    config = get_ping_config(device)
    interval = config["interval"]
    last_checked = device.get("lastCheckedAt")
    if not last_checked:
        return True

    if getattr(last_checked, "tzinfo", None) is None:
        last_checked = last_checked.replace(tzinfo=timezone.utc)

    elapsed = (now - last_checked).total_seconds()
    return elapsed >= interval


def apply_ping_result(device, result, scan_type="Automatic"):
    """Update device fields and history from a ping result (FR3.4, FR3.5)."""
    now = datetime.now(timezone.utc)
    previous_status = device.get("status", "Unknown")
    consecutive = int(device.get("consecutiveFailures") or 0)

    update = {
        "status": result["status"],
        "responseTime": result["responseTime"],
        "updatedAt": now,
        "lastCheckedAt": now,
    }

    if result["success"]:
        update["lastSeen"] = result["lastSeen"]
        update["consecutiveFailures"] = 0
        consecutive = 0
    else:
        consecutive += 1
        update["consecutiveFailures"] = consecutive
        # Do not overwrite lastSeen on failure (FR3.4)

    db.devices.update_one({"_id": device["_id"]}, {"$set": update})

    save_ping_history(
        device=device,
        ping_result=result,
        scan_type=scan_type,
    )

    maybe_send_critical_offline_alert(
        device,
        previous_status,
        result["status"],
        consecutive_failures=consecutive,
        scan_type=scan_type,
    )

    return previous_status


def _scan_device(device):
    hostname = device.get("hostname", "unknown")
    ip_address = device.get("ipAddress", "unknown")

    logger.info("Device scan started: %s (%s)", hostname, ip_address)

    result = ping_device(
        ip_address,
        critical=bool(device.get("critical")),
        device=device,
    )
    apply_ping_result(device, result, scan_type="Automatic")

    if result["status"] == STATUS_ONLINE:
        logger.info(
            "Device online: %s (%s) | response=%s ms",
            hostname,
            ip_address,
            result["responseTime"],
        )
    else:
        logger.warning(
            "Device down: %s (%s) | status=%s | %s",
            hostname,
            ip_address,
            result["status"],
            result.get("message", "No response"),
        )


def monitor_all_devices():
    """Scan all devices with monitor=True (FR2.1)."""
    logger.info("Monitoring cycle started")

    now = datetime.now(timezone.utc)
    devices = list(db.devices.find({"monitor": True}))
    scanned = 0
    skipped = 0
    failed = 0

    for device in devices:
        hostname = device.get("hostname", "unknown")
        ip_address = device.get("ipAddress", "unknown")

        try:
            if not _should_check_now(device, now):
                skipped += 1
                continue
            _scan_device(device)
            scanned += 1
        except Exception as error:
            failed += 1
            logger.exception(
                "Scan failed for %s (%s): %s",
                hostname,
                ip_address,
                error,
            )

    logger.info(
        "Monitoring cycle finished | total=%s scanned=%s skipped=%s failed=%s",
        len(devices),
        scanned,
        skipped,
        failed,
    )
