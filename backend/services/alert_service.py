from datetime import datetime, timedelta
from typing import Any, Optional

from pymongo import ReturnDocument

from config.database import db
from services.audit_service import log_audit
from services.email_service import (
    _recovery_duration_label,
    _risk_score_from_incident,
    send_critical_device_recovery_alert,
    send_critical_offline_alert,
    send_storm_confirmed_notification,
)
from services.whatsapp_service import (
    send_critical_offline_whatsapp_alert,
    send_device_recovery_whatsapp_alert,
)
from services.mongo_retry import (
    assert_insert_acknowledged,
    assert_update_acknowledged,
    with_mongo_retry,
)
from services.monitor_events import (
    EVENT_ALERT_CREATED,
    EVENT_ALERT_RESOLVED,
    publish,
)
from services.ping_service import STATUS_OFFLINE_CRITICAL, STATUS_ONLINE
from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("alert")

ALERT_TYPE_STORM = "Storm Protection"
ALERT_TYPE_DEVICE_OFFLINE = "Device Offline"
ALERT_TYPE_DEVICE_RECOVERED = "Device Recovered"
CATEGORY_DEVICE_MONITORING = "Device Monitoring"
CATEGORY_STORM = "Storm Protection"
GENERATED_BY_SYSTEM = "SYSTEM"
STORM_CONFIRMED_TITLE = "Storm Confirmed"


def _normalize_alert_device_id(device_id):
    """Prefer ObjectId so unique partial indexes on deviceId can apply."""
    if device_id is None:
        return None
    try:
        from bson import ObjectId  # noqa: PLC0415

        if isinstance(device_id, ObjectId):
            return device_id
        if isinstance(device_id, str) and ObjectId.is_valid(device_id):
            return ObjectId(device_id)
    except Exception:  # noqa: BLE001
        pass
    return device_id


def _active_critical_offline_filter(device_id) -> dict:
    """Match unrecovered Offline (Critical) alerts for a device (ObjectId or str)."""
    ids = [device_id]
    if device_id is not None:
        ids.append(str(device_id))
    return {
        "deviceId": {"$in": ids},
        "status": STATUS_OFFLINE_CRITICAL,
        "resolved": {"$ne": True},
        "dismissed": {"$ne": True},
    }


CRITICAL_OFFLINE_ALERT_THRESHOLD = 3
# Minimum seconds between critical-offline email attempts (initial or retry).
# Matches default pingInterval cadence to avoid hammering SMTP on failure.
CRITICAL_OFFLINE_EMAIL_RETRY_COOLDOWN_SECONDS = 60


def _build_critical_offline_message(
    hostname: str,
    ip_address: str,
    scan_type: str,
    consecutive_failures: int,
) -> str:
    return (
        f"Critical device {hostname} ({ip_address}) transitioned to "
        f"{STATUS_OFFLINE_CRITICAL} via {scan_type} scan "
        f"(consecutiveFailures={consecutive_failures})."
    )


def _build_critical_offline_alert_doc(
    device: dict,
    *,
    message: str,
    scan_type: str,
    cycle_id: str | None,
    attempt_id: str | None,
    consecutive_failures: int,
    now: datetime,
) -> dict[str, Any]:
    device_id = device.get("_id")
    return {
        "deviceId": _normalize_alert_device_id(device_id),
        "hostname": device.get("hostname", "unknown"),
        "ipAddress": device.get("ipAddress", "unknown"),
        "deviceType": device.get("deviceType") or device.get("type"),
        "status": STATUS_OFFLINE_CRITICAL,
        "message": message,
        "scanType": scan_type,
        "alertType": ALERT_TYPE_DEVICE_OFFLINE,
        "category": CATEGORY_DEVICE_MONITORING,
        "severity": "CRITICAL",
        "emailSent": False,
        "emailLastAttemptAt": now,
        "recoveryEmailSent": False,
        "acknowledged": False,
        "dismissed": False,
        "resolved": False,
        "acknowledgedAt": None,
        "dismissedAt": None,
        "resolvedAt": None,
        "createdAt": now,
        "cycleId": cycle_id,
        "attemptId": attempt_id,
        "consecutiveFailuresAtAlert": int(consecutive_failures),
    }


def _claim_critical_offline_alert(
    doc: dict[str, Any],
    *,
    device_id,
    ip_address: str,
) -> tuple[Any | None, bool, Any | None]:
    """
    Atomically create the active critical-offline alert.

    Returns ``(alert_id, created, insert_result)``. ``created=True`` only when
    this call inserted the document. DuplicateKeyError yields the existing alert id.
    """
    from pymongo.errors import DuplicateKeyError  # noqa: PLC0415

    def _insert_or_existing():
        try:
            result = db.alerts.insert_one(doc)
            return result.inserted_id, True, result
        except DuplicateKeyError:
            logger.info(
                "Critical alert insert idempotent (DuplicateKey) | "
                "deviceId=%s | ip=%s | attemptId=%s",
                device_id,
                ip_address,
                doc.get("attemptId"),
            )
            existing = db.alerts.find_one(
                _active_critical_offline_filter(device_id),
            )
            if existing:
                return existing.get("_id"), False, None
            return None, False, None

    inserted_id, created, insert_result = with_mongo_retry(
        _insert_or_existing,
        action="critical_alert_insert",
        device_id=device_id,
        ip_address=ip_address,
        idempotent=True,
    )
    return inserted_id, created, insert_result


def _try_claim_email_retry(alert_id) -> bool:
    """
    Atomically claim a retry slot when ``emailSent`` is still false.

    Uses ``emailLastAttemptAt`` plus cooldown so concurrent workers cannot
    hammer SMTP or send duplicate retries in the same window.
    """
    now = utc_now()
    cutoff = now - timedelta(seconds=CRITICAL_OFFLINE_EMAIL_RETRY_COOLDOWN_SECONDS)

    def _claim():
        return db.alerts.find_one_and_update(
            {
                "_id": alert_id,
                "status": STATUS_OFFLINE_CRITICAL,
                "emailSent": False,
                "resolved": False,
                "dismissed": False,
                "$or": [
                    {"emailLastAttemptAt": {"$exists": False}},
                    {"emailLastAttemptAt": None},
                    {"emailLastAttemptAt": {"$lte": cutoff}},
                ],
            },
            {"$set": {"emailLastAttemptAt": now}},
            return_document=ReturnDocument.AFTER,
        )

    try:
        claimed = with_mongo_retry(
            _claim,
            action="critical_alert_email_retry_claim",
            idempotent=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Critical offline email retry claim failed | alertId=%s | %s",
            alert_id,
            exc,
        )
        return False
    return claimed is not None


def _deliver_critical_offline_email(
    device: dict,
    alert_id,
    *,
    scan_type: str,
    retry: bool = False,
) -> bool:
    """
    Send critical-offline SMTP and persist ``emailSent`` on success.

    SMTP is not transactional with MongoDB. If ``send_email`` succeeds but the
    ``emailSent`` update fails, a later cooldown retry may re-send — acceptable
    trade-off without distributed transactions.
    """
    hostname = device.get("hostname", "unknown")
    ip_address = device.get("ipAddress", "unknown")
    if retry:
        logger.info(
            "retrying critical offline email | alertId=%s | deviceId=%s | ip=%s",
            alert_id,
            device.get("_id"),
            ip_address,
        )
    else:
        logger.info(
            "critical offline email attempt | alertId=%s | deviceId=%s | ip=%s",
            alert_id,
            device.get("_id"),
            ip_address,
        )

    try:
        email_sent = send_critical_offline_alert(device, scan_type=scan_type)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "critical offline email failed; will retry | alertId=%s | deviceId=%s | "
            "ip=%s | error=%s",
            alert_id,
            device.get("_id"),
            ip_address,
            exc,
        )
        return False

    if email_sent:
        mark_alert_email_sent(str(alert_id), True)
        logger.info(
            "critical offline email sent | alertId=%s | deviceId=%s | ip=%s",
            alert_id,
            device.get("_id"),
            ip_address,
        )
        return True

    logger.warning(
        "critical offline email failed; will retry | alertId=%s | deviceId=%s | ip=%s",
        alert_id,
        device.get("_id"),
        ip_address,
    )
    return False


def _build_critical_recovery_message(hostname: str, ip_address: str, scan_type: str) -> str:
    return (
        f"Critical device {hostname} ({ip_address}) recovered to "
        f"{STATUS_ONLINE} via {scan_type} scan."
    )


def _build_critical_recovery_alert_doc(
    device: dict,
    *,
    message: str,
    scan_type: str,
    cycle_id: str | None,
    offline_alert: dict | None,
    now: datetime,
) -> dict[str, Any]:
    device_id = device.get("_id")
    return {
        "deviceId": _normalize_alert_device_id(device_id),
        "hostname": device.get("hostname", "unknown"),
        "ipAddress": device.get("ipAddress", "unknown"),
        "deviceType": device.get("deviceType") or device.get("type"),
        "status": STATUS_ONLINE,
        "message": message,
        "title": "Critical Device Recovered",
        "scanType": scan_type,
        "alertType": ALERT_TYPE_DEVICE_RECOVERED,
        "category": CATEGORY_DEVICE_MONITORING,
        "severity": "INFO",
        "emailSent": False,
        "acknowledged": False,
        "dismissed": False,
        "resolved": False,
        "acknowledgedAt": None,
        "dismissedAt": None,
        "resolvedAt": None,
        "createdAt": now,
        "cycleId": cycle_id,
        "relatedOfflineAlertId": (
            offline_alert.get("_id") if isinstance(offline_alert, dict) else None
        ),
        "generatedBy": GENERATED_BY_SYSTEM,
    }


def _create_critical_recovery_alert(
    device: dict,
    *,
    scan_type: str,
    cycle_id: str | None,
    offline_alert: dict | None,
) -> Any | None:
    """Insert a Device Recovered alert. Returns alert id or None."""
    device_id = device.get("_id")
    hostname = device.get("hostname", "unknown")
    ip_address = device.get("ipAddress", "unknown")
    now = utc_now()
    message = _build_critical_recovery_message(hostname, ip_address, scan_type)
    doc = _build_critical_recovery_alert_doc(
        device,
        message=message,
        scan_type=scan_type,
        cycle_id=cycle_id,
        offline_alert=offline_alert,
        now=now,
    )

    try:
        result = with_mongo_retry(
            lambda: db.alerts.insert_one(doc),
            action="critical_recovery_alert_insert",
            device_id=device_id,
            ip_address=ip_address,
            idempotent=False,
        )
        assert_insert_acknowledged(
            result,
            action="critical_recovery_alert_insert",
            device_id=device_id,
            ip_address=ip_address,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to create critical recovery alert | deviceId=%s | ip=%s | error=%s",
            device_id,
            ip_address,
            exc,
        )
        return None

    alert_id = result.inserted_id
    publish(
        EVENT_ALERT_CREATED,
        {
            "deviceId": str(device_id) if device_id is not None else None,
            "hostname": hostname,
            "ipAddress": ip_address,
            "status": STATUS_ONLINE,
            "alertId": str(alert_id),
            "alertType": ALERT_TYPE_DEVICE_RECOVERED,
            "cycleId": cycle_id,
        },
    )
    logger.info(
        "Critical recovery alert created | deviceId=%s | hostname=%s | ip=%s | alertId=%s",
        device_id,
        hostname,
        ip_address,
        alert_id,
    )
    return alert_id


def _deliver_critical_recovery_email(
    device: dict,
    offline_alert: dict,
    recovery_alert_id,
    *,
    scan_type: str,
) -> bool:
    """Send recovery SMTP and mark both offline + recovery alert email flags."""
    hostname = device.get("hostname", "unknown")
    ip_address = device.get("ipAddress", "unknown")
    offline_alert_id = offline_alert.get("_id")

    logger.info(
        "critical recovery email attempt | offlineAlertId=%s | recoveryAlertId=%s | "
        "deviceId=%s | ip=%s",
        offline_alert_id,
        recovery_alert_id,
        device.get("_id"),
        ip_address,
    )

    try:
        email_sent = send_critical_device_recovery_alert(
            device,
            offline_alert,
            scan_type=scan_type,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "critical recovery email failed | offlineAlertId=%s | deviceId=%s | "
            "ip=%s | error=%s",
            offline_alert_id,
            device.get("_id"),
            ip_address,
            exc,
        )
        return False

    if not email_sent:
        logger.warning(
            "critical recovery email failed | offlineAlertId=%s | deviceId=%s | ip=%s",
            offline_alert_id,
            device.get("_id"),
            ip_address,
        )
        return False

    try:
        if offline_alert_id is not None:
            db.alerts.update_one(
                {"_id": offline_alert_id},
                {"$set": {"recoveryEmailSent": True}},
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to set recoveryEmailSent | offlineAlertId=%s | %s",
            offline_alert_id,
            exc,
        )

    if recovery_alert_id is not None:
        mark_alert_email_sent(str(recovery_alert_id), True)

    logger.info(
        "critical recovery email sent | offlineAlertId=%s | recoveryAlertId=%s | "
        "deviceId=%s | hostname=%s | ip=%s",
        offline_alert_id,
        recovery_alert_id,
        device.get("_id"),
        hostname,
        ip_address,
    )
    return True


def resolve_critical_offline_alerts(
    device,
    *,
    scan_type: str = "Automatic",
    cycle_id=None,
) -> int:
    """
    Mark active Offline (Critical) alerts as recovered when the device is Online.

    Creates a Device Recovered alert and sends recovery email + WhatsApp.
    Never deletes alerts. Idempotent under concurrent / repeated recoveries.
    """
    device_id = device.get("_id")
    hostname = device.get("hostname", "unknown")
    ip_address = device.get("ipAddress", "unknown")
    now = utc_now()
    alert_filter = _active_critical_offline_filter(device_id)

    try:
        active_alerts = list(db.alerts.find(alert_filter))
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to load critical alerts for recovery | deviceId=%s | ip=%s | error=%s",
            device_id,
            ip_address,
            exc,
        )
        return 0

    if not active_alerts:
        return 0

    def _update():
        return db.alerts.update_many(
            alert_filter,
            {
                "$set": {
                    "resolved": True,
                    "resolvedAt": now,
                    "resolvedBy": GENERATED_BY_SYSTEM,
                    "resolvedReason": "Device recovered to Online",
                }
            },
        )

    try:
        result = with_mongo_retry(
            _update,
            action="critical_alert_resolve",
            device_id=device_id,
            ip_address=ip_address,
            idempotent=True,
        )
        assert_update_acknowledged(
            result,
            action="critical_alert_resolve",
            device_id=device_id,
            ip_address=ip_address,
            require_matched=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to resolve critical alerts | deviceId=%s | ip=%s | error=%s",
            device_id,
            ip_address,
            exc,
        )
        return 0

    modified = int(result.modified_count or 0)
    if not modified:
        return 0

    logger.info(
        "Critical alerts resolved on recovery | deviceId=%s | hostname=%s | "
        "ip=%s | count=%s | cycleId=%s",
        device_id,
        hostname,
        ip_address,
        modified,
        cycle_id,
    )
    publish(
        EVENT_ALERT_RESOLVED,
        {
            "deviceId": str(device_id) if device_id is not None else None,
            "hostname": hostname,
            "ipAddress": ip_address,
            "status": STATUS_OFFLINE_CRITICAL,
            "resolvedCount": modified,
            "cycleId": cycle_id,
        },
    )
    try:
        log_audit(
            action="critical_offline_alert_resolved",
            entity_type="device",
            entity_id=str(device_id) if device_id is not None else None,
            details={
                "hostname": hostname,
                "ipAddress": ip_address,
                "resolvedCount": modified,
                "cycleId": cycle_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Audit log failed for alert resolve: %s", exc)

    # One recovery alert + email per resolved offline incident (usually one).
    for offline_alert in active_alerts:
        if offline_alert.get("recoveryEmailSent"):
            continue
        recovery_alert_id = _create_critical_recovery_alert(
            device,
            scan_type=scan_type,
            cycle_id=cycle_id,
            offline_alert=offline_alert,
        )
        try:
            _deliver_critical_recovery_email(
                device,
                offline_alert,
                recovery_alert_id,
                scan_type=scan_type,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Critical recovery email failed | deviceId=%s | ip=%s | "
                "offlineAlertId=%s | error=%s",
                device_id,
                ip_address,
                offline_alert.get("_id"),
                exc,
            )

    try:
        send_device_recovery_whatsapp_alert(device)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "WhatsApp recovery alert failed | deviceId=%s | ip=%s | error=%s",
            device_id,
            ip_address,
            exc,
        )
    return modified


def maybe_send_critical_offline_alert(
    device,
    previous_status,
    new_status,
    consecutive_failures,
    scan_type="Automatic",
    *,
    cycle_id=None,
    attempt_id: str | None = None,
):
    """
    Create/claim active critical-offline alert, then send or retry email.

    Alert insert precedes SMTP. Only the insert owner sends the initial email.
    Failed sends remain ``emailSent=false`` and are retried after cooldown
    while the device stays critically offline.
    """
    if not device.get("critical"):
        return False

    if new_status != STATUS_OFFLINE_CRITICAL:
        return False

    if int(consecutive_failures or 0) < CRITICAL_OFFLINE_ALERT_THRESHOLD:
        return False

    device_id = device.get("_id")
    hostname = device.get("hostname", "unknown")
    ip_address = device.get("ipAddress", "unknown")

    message = _build_critical_offline_message(
        hostname,
        ip_address,
        scan_type,
        int(consecutive_failures),
    )

    logger.warning(
        "Critical device offline: %s (%s) | previous=%s scan=%s | "
        "failures=%s | cycleId=%s | attemptId=%s",
        hostname,
        ip_address,
        previous_status,
        scan_type,
        consecutive_failures,
        cycle_id,
        attempt_id,
    )

    now = utc_now()
    doc = _build_critical_offline_alert_doc(
        device,
        message=message,
        scan_type=scan_type,
        cycle_id=cycle_id,
        attempt_id=attempt_id,
        consecutive_failures=int(consecutive_failures),
        now=now,
    )

    try:
        alert_id, created, insert_result = _claim_critical_offline_alert(
            doc,
            device_id=device_id,
            ip_address=ip_address,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to claim critical offline alert | deviceId=%s | ip=%s | error=%s",
            device_id,
            ip_address,
            exc,
        )
        return False

    if alert_id is None:
        return False

    if created:
        if insert_result is not None:
            try:
                assert_insert_acknowledged(
                    insert_result,
                    action="critical_alert_insert",
                    device_id=device_id,
                    ip_address=ip_address,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Critical alert insert acknowledgement failed | deviceId=%s | error=%s",
                    device_id,
                    exc,
                )
                return False
        publish(
            EVENT_ALERT_CREATED,
            {
                "deviceId": str(device_id) if device_id is not None else None,
                "hostname": hostname,
                "ipAddress": ip_address,
                "status": STATUS_OFFLINE_CRITICAL,
                "alertId": str(alert_id),
                "cycleId": cycle_id,
                "attemptId": attempt_id,
            },
        )
        try:
            send_critical_offline_whatsapp_alert(device, scan_type=scan_type)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "WhatsApp critical offline alert failed | deviceId=%s | ip=%s | error=%s",
                device_id,
                ip_address,
                exc,
            )
        _deliver_critical_offline_email(
            device,
            alert_id,
            scan_type=scan_type,
            retry=False,
        )
        return True

    # Existing active alert — retry only when email not yet delivered.
    existing = db.alerts.find_one({"_id": alert_id})
    if not existing:
        return False

    if existing.get("emailSent"):
        logger.info(
            "Critical offline alert already active — email sent | "
            "deviceId=%s | ip=%s | alertId=%s | failures=%s | cycleId=%s",
            device_id,
            ip_address,
            alert_id,
            consecutive_failures,
            cycle_id,
        )
        return False

    if not device.get("monitor", True):
        return False

    if not _try_claim_email_retry(alert_id):
        return False

    return _deliver_critical_offline_email(
        device,
        alert_id,
        scan_type=scan_type,
        retry=True,
    )


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
        now = utc_now()
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
            "resolved": False,
            "acknowledgedAt": None,
            "dismissedAt": None,
            "resolvedAt": None,
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


def create_storm_confirmed_alert(
    incident: dict,
    *,
    device: Optional[dict] = None,
    email_sent: bool = False,
) -> Optional[str]:
    """CRITICAL alert when storm is first confirmed on a switch port."""
    interface = (incident or {}).get("interface") or "unknown"
    reason = (incident or {}).get("reason") or (
        f"Storm confirmed on interface {interface}."
    )
    return _insert_storm_alert(
        incident=incident,
        device=device,
        title=STORM_CONFIRMED_TITLE,
        message=str(reason),
        severity="CRITICAL",
        action="NONE",
        status="CONFIRMED",
        email_sent=email_sent,
    )


def claim_storm_confirmed_alert(
    device_id,
    interface: str,
    *,
    risk_score: float,
    hostname: Optional[str] = None,
    ip_address: Optional[str] = None,
    reason: Optional[str] = None,
) -> tuple[Optional[str], bool]:
    """
    Atomically create the active Storm Confirmed alert for a port.

    Returns ``(alert_id, created)``.
    ``created=True`` only when this invocation inserted the document.
    On unique-index conflict, returns the existing alert id (if found) and
    ``created=False`` — callers must not send email.
    """
    from pymongo.errors import DuplicateKeyError  # noqa: PLC0415

    name = str(interface or "").strip() or "unknown"
    host = hostname or "unknown"
    ip = ip_address or "unknown"
    oid = _normalize_alert_device_id(device_id)
    message = reason or (
        f"Storm confirmed on interface {name}. "
        f"Risk score {round(float(risk_score), 1)}."
    )
    incident = {
        "deviceId": oid,
        "interface": name,
        "hostname": host,
        "ipAddress": ip,
        "deviceType": "Switch",
        "reason": message,
        "risk": {"riskScore": risk_score},
        "status": "CONFIRMED",
    }

    # Fast path — avoid insert noise when an active alert is already visible.
    try:
        existing = db.alerts.find_one(
            {
                "deviceId": oid,
                "interface": name,
                "title": STORM_CONFIRMED_TITLE,
                "resolved": False,
                "dismissed": False,
            },
            {"_id": 1},
        )
        if existing:
            logger.info(
                "Storm confirmed alert already active — skip create/email | %s | %s | alertId=%s",
                host,
                name,
                existing.get("_id"),
            )
            return str(existing["_id"]), False
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Storm confirmed alert pre-check failed | %s | %s | %s",
            host,
            name,
            exc,
        )

    # Atomic create: unique partial index is the authority under races.
    alert_id = None
    try:
        # Build via shared inserter fields, but catch DuplicateKeyError here
        # (_insert_storm_alert swallows all errors and would hide ownership).
        now = utc_now()
        fields = _storm_device_fields(incident)
        doc: dict[str, Any] = {
            "deviceId": fields["deviceId"],
            "hostname": fields["hostname"],
            "ipAddress": fields["ipAddress"],
            "deviceType": fields.get("deviceType"),
            "deviceName": fields["deviceName"],
            "status": "CONFIRMED",
            "message": message,
            "title": STORM_CONFIRMED_TITLE,
            "scanType": CATEGORY_STORM,
            "alertType": ALERT_TYPE_STORM,
            "category": CATEGORY_STORM,
            "severity": "CRITICAL",
            "interface": fields["interface"],
            "incidentId": fields.get("incidentId"),
            "riskScore": fields.get("riskScore"),
            "action": "NONE",
            "generatedBy": GENERATED_BY_SYSTEM,
            "emailSent": False,
            "acknowledged": False,
            "dismissed": False,
            "resolved": False,
            "acknowledgedAt": None,
            "dismissedAt": None,
            "resolvedAt": None,
            "createdAt": now,
        }
        result = db.alerts.insert_one(doc)
        alert_id = str(result.inserted_id)
        try:
            log_audit(
                action="storm_protection_alert",
                entity_type="alert",
                entity_id=alert_id,
                details={
                    "action": "NONE",
                    "device": fields["hostname"],
                    "deviceName": fields["deviceName"],
                    "deviceIp": fields["ipAddress"],
                    "interface": fields["interface"],
                    "incident": fields.get("incidentId"),
                    "timestamp": now.isoformat().replace("+00:00", "Z"),
                    "alertId": alert_id,
                    "title": STORM_CONFIRMED_TITLE,
                    "severity": "CRITICAL",
                    "status": "CONFIRMED",
                },
            )
        except Exception as audit_exc:  # noqa: BLE001
            logger.warning("Storm confirmed alert audit failed | %s", audit_exc)
        logger.info(
            "Storm confirmed alert created — email notification allowed | %s | %s | alertId=%s",
            host,
            name,
            alert_id,
        )
        return alert_id, True
    except DuplicateKeyError:
        logger.info(
            "Storm confirmed alert duplicate prevented — email skipped | %s | %s",
            host,
            name,
        )
        try:
            existing = db.alerts.find_one(
                {
                    "deviceId": oid,
                    "interface": name,
                    "title": STORM_CONFIRMED_TITLE,
                    "resolved": False,
                    "dismissed": False,
                },
                {"_id": 1},
            )
            if existing:
                return str(existing["_id"]), False
        except Exception as lookup_exc:  # noqa: BLE001
            logger.warning(
                "Storm confirmed duplicate lookup failed | %s | %s | %s",
                host,
                name,
                lookup_exc,
            )
        return None, False
    except Exception as alert_exc:  # noqa: BLE001
        logger.warning(
            "Storm confirmed alert failed | %s | %s | %s",
            host,
            name,
            alert_exc,
        )
        return None, False


def notify_storm_confirmed(
    device_id,
    interface: str,
    *,
    risk_score: float,
    hostname: Optional[str] = None,
    ip_address: Optional[str] = None,
    reason: Optional[str] = None,
) -> Optional[str]:
    """
    Create a Storm Confirmed alert and send email only if this caller created it.

    Unique partial index + DuplicateKey handling guarantee at most one active
    alert and one initial email under concurrent callers. Never raises.
    """
    name = str(interface or "").strip() or "unknown"
    message = reason or (
        f"Storm confirmed on interface {name}. "
        f"Risk score {round(float(risk_score), 1)}."
    )
    host = hostname or "unknown"
    ip = ip_address or "unknown"
    oid = _normalize_alert_device_id(device_id)

    alert_id, created = claim_storm_confirmed_alert(
        oid,
        name,
        risk_score=risk_score,
        hostname=host,
        ip_address=ip,
        reason=message,
    )
    if not created:
        return alert_id

    incident = {
        "deviceId": oid,
        "interface": name,
        "hostname": host,
        "ipAddress": ip,
        "deviceType": "Switch",
        "reason": message,
        "risk": {"riskScore": risk_score},
        "status": "CONFIRMED",
    }
    try:
        email_sent = send_storm_confirmed_notification(
            incident,
            reason=message,
        )
        mark_alert_email_sent(alert_id, bool(email_sent))
    except Exception as email_exc:  # noqa: BLE001
        logger.warning(
            "Storm confirmed email failed | %s | %s | %s",
            host,
            name,
            email_exc,
        )

    return alert_id


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
    title: str = "Automatic Port Recovery",
    message: Optional[str] = None,
    recovery_source: Optional[str] = None,
) -> Optional[str]:
    """INFO alert after verified port recovery."""
    interface = (incident or {}).get("interface") or "unknown"
    duration = _recovery_duration_label(incident, recovered_at)
    alert_message = message or (
        "Storm conditions cleared.\n"
        f"NetPulse automatically restored interface {interface}."
    )
    alert_id = _insert_storm_alert(
        incident=incident,
        device=device,
        title=title,
        message=alert_message,
        severity="INFO",
        action="NO_SHUTDOWN",
        status="RECOVERED",
        recovery_duration=duration,
        email_sent=email_sent,
    )
    if alert_id and recovery_source:
        try:
            from bson import ObjectId  # noqa: PLC0415

            if ObjectId.is_valid(alert_id):
                db.alerts.update_one(
                    {"_id": ObjectId(alert_id)},
                    {"$set": {"recoverySource": recovery_source}},
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to set recoverySource on alert | alertId=%s | %s",
                alert_id,
                exc,
            )
    return alert_id


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
