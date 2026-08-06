from datetime import datetime, timezone
from typing import Any, Optional

from config.database import db
from services.audit_service import log_audit
from services.email_service import (
    _recovery_duration_label,
    _risk_score_from_incident,
    send_critical_offline_alert,
)
from services.ping_service import STATUS_OFFLINE_CRITICAL
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("alert")

ALERT_TYPE_STORM = "Storm Protection"
CATEGORY_STORM = "Storm Protection"
GENERATED_BY_SYSTEM = "SYSTEM"


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


# ---------------------------------------------------------------------------
# Storm Protection alerts (reuse alerts collection — not a separate system)
# ---------------------------------------------------------------------------


def _storm_device_fields(incident: Optional[dict], device: Optional[dict] = None) -> dict[str, Any]:
    incident = incident or {}
    device = device or {}
    hostname = (
        incident.get("hostname")
        or device.get("hostname")
        or "unknown"
    )
    ip_address = (
        incident.get("ipAddress")
        or device.get("ipAddress")
        or "unknown"
    )
    device_name = (
        device.get("name")
        or device.get("deviceName")
        or incident.get("deviceName")
        or hostname
    )
    device_id = incident.get("deviceId") or device.get("_id")
    return {
        "deviceId": device_id,
        "deviceName": device_name,
        "hostname": hostname,
        "ipAddress": ip_address,
        "deviceType": device.get("deviceType") or device.get("type") or incident.get("deviceType"),
        "interface": incident.get("interface") or "unknown",
        "incidentId": incident.get("incidentId"),
        "riskScore": _risk_score_from_incident(incident),
    }


def _insert_storm_alert(
    *,
    incident: dict,
    title: str,
    message: str,
    severity: str,
    action: str,
    status: str,
    device: Optional[dict] = None,
    recovery_duration: Optional[str] = None,
    email_sent: bool = False,
) -> Optional[str]:
    """
    Persist a Storm Protection alert into the shared alerts collection and
    write an audit log. Never raises — failures are logged only.
    """
    try:
        now = datetime.now(timezone.utc)
        fields = _storm_device_fields(incident, device)
        doc: dict[str, Any] = {
            "deviceId": fields["deviceId"],
            "hostname": fields["hostname"],
            "ipAddress": fields["ipAddress"],
            "deviceType": fields.get("deviceType"),
            "deviceName": fields["deviceName"],
            "status": status,
            "message": message,
            "title": title,
            "scanType": CATEGORY_STORM,
            "alertType": ALERT_TYPE_STORM,
            "category": CATEGORY_STORM,
            "severity": severity,
            "interface": fields["interface"],
            "incidentId": fields.get("incidentId"),
            "riskScore": fields.get("riskScore"),
            "action": action,
            "generatedBy": GENERATED_BY_SYSTEM,
            "emailSent": bool(email_sent),
            "acknowledged": False,
            "dismissed": False,
            "acknowledgedAt": None,
            "dismissedAt": None,
            "createdAt": now,
        }
        if recovery_duration is not None:
            doc["recoveryDuration"] = recovery_duration

        result = db.alerts.insert_one(doc)
        alert_id = str(result.inserted_id)

        log_audit(
            action="storm_protection_alert",
            entity_type="alert",
            entity_id=alert_id,
            details={
                "action": action,
                "device": fields["hostname"],
                "deviceName": fields["deviceName"],
                "deviceIp": fields["ipAddress"],
                "interface": fields["interface"],
                "incident": fields.get("incidentId"),
                "timestamp": now.isoformat().replace("+00:00", "Z"),
                "alertId": alert_id,
                "title": title,
                "severity": severity,
                "status": status,
            },
        )

        logger.info(
            "Storm alert created | title=%s | incident=%s | alertId=%s | severity=%s",
            title,
            fields.get("incidentId"),
            alert_id,
            severity,
        )
        return alert_id
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Failed to create storm alert | title=%s | incident=%s | %s",
            title,
            (incident or {}).get("incidentId"),
            exc,
        )
        return None


def mark_alert_email_sent(alert_id: Optional[str], email_sent: bool = True) -> None:
    """Best-effort update of emailSent after independent email delivery."""
    if not alert_id:
        return
    try:
        from bson import ObjectId  # noqa: PLC0415

        if not ObjectId.is_valid(alert_id):
            return
        db.alerts.update_one(
            {"_id": ObjectId(alert_id)},
            {"$set": {"emailSent": bool(email_sent)}},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to update alert emailSent | alertId=%s | %s", alert_id, exc)


def create_storm_shutdown_alert(
    incident: dict,
    *,
    device: Optional[dict] = None,
    email_sent: bool = False,
) -> Optional[str]:
    """CRITICAL alert after verified automatic port shutdown → MITIGATED."""
    interface = (incident or {}).get("interface") or "unknown"
    return _insert_storm_alert(
        incident=incident,
        device=device,
        title="Automatic Port Shutdown",
        message=(
            f"Storm detected on interface {interface}.\n"
            "NetPulse automatically shut down the port to protect the network."
        ),
        severity="CRITICAL",
        action="SHUTDOWN",
        status="MITIGATED",
        email_sent=email_sent,
    )


def create_storm_recovery_alert(
    incident: dict,
    *,
    device: Optional[dict] = None,
    recovered_at: Optional[datetime] = None,
    email_sent: bool = False,
) -> Optional[str]:
    """INFO alert after verified automatic port recovery."""
    interface = (incident or {}).get("interface") or "unknown"
    duration = _recovery_duration_label(incident, recovered_at)
    return _insert_storm_alert(
        incident=incident,
        device=device,
        title="Automatic Port Recovery",
        message=(
            "Storm conditions cleared.\n"
            f"NetPulse automatically restored interface {interface}."
        ),
        severity="INFO",
        action="NO_SHUTDOWN",
        status="RECOVERED",
        recovery_duration=duration,
        email_sent=email_sent,
    )


def create_storm_shutdown_failure_alert(
    incident: dict,
    *,
    device: Optional[dict] = None,
    action_status: str = "MITIGATION_FAILED",
    email_sent: bool = False,
) -> Optional[str]:
    """CRITICAL alert when automatic mitigation execution fails."""
    return _insert_storm_alert(
        incident=incident,
        device=device,
        title="Automatic Shutdown Failed",
        message=(
            "Automatic mitigation failed.\n"
            "Manual investigation is required."
        ),
        severity="CRITICAL",
        action="SHUTDOWN",
        status=action_status or "MITIGATION_FAILED",
        email_sent=email_sent,
    )


def create_storm_recovery_failure_alert(
    incident: dict,
    *,
    device: Optional[dict] = None,
    action_status: str = "RECOVERY_FAILED",
    email_sent: bool = False,
) -> Optional[str]:
    """WARNING alert when automatic recovery execution fails."""
    return _insert_storm_alert(
        incident=incident,
        device=device,
        title="Automatic Recovery Failed",
        message=(
            "Automatic recovery failed.\n"
            "Manual intervention may be required."
        ),
        severity="WARNING",
        action="NO_SHUTDOWN",
        status=action_status or "RECOVERY_FAILED",
        email_sent=email_sent,
    )


def create_storm_remitigation_blocked_alert(
    incident: dict,
    *,
    device: Optional[dict] = None,
    reason: str,
    failed_rule: Optional[str] = None,
    email_sent: bool = False,
) -> Optional[str]:
    """CRITICAL alert when post-recovery automatic re-mitigation is blocked."""
    interface = (incident or {}).get("interface") or "unknown"
    message = reason or (
        f"Storm remains active on interface {interface} after recovery.\n"
        "Automatic re-mitigation was blocked.\n"
        "Manual intervention is required."
    )
    return _insert_storm_alert(
        incident=incident,
        device=device,
        title="Re-Mitigation Blocked — Manual Intervention Required",
        message=message,
        severity="CRITICAL",
        action="SHUTDOWN",
        status="ESCALATED",
        email_sent=email_sent,
    )
