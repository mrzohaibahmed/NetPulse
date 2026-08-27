from datetime import datetime
from typing import Any, Optional

from config.database import db
from services.audit_service import log_audit
from services.email_service import (
    _recovery_duration_label,
    _risk_score_from_incident,
    send_critical_offline_alert,
    send_storm_confirmed_notification,
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
from services.ping_service import STATUS_OFFLINE_CRITICAL
from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("alert")

ALERT_TYPE_STORM = "Storm Protection"
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


def resolve_critical_offline_alerts(device, *, cycle_id=None) -> int:
    """
    Mark active Offline (Critical) alerts as recovered when the device is Online.

    Never deletes alerts. Idempotent under concurrent / repeated recoveries.
    """
    device_id = device.get("_id")
    hostname = device.get("hostname", "unknown")
    ip_address = device.get("ipAddress", "unknown")
    now = utc_now()

    def _update():
        return db.alerts.update_many(
            _active_critical_offline_filter(device_id),
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
    if modified:
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
    Email + alert insert for critical devices when failures reach the threshold.

    Phase 7–8:
      - Fire when consecutiveFailures >= 3 (recovers missed exactly-3 inserts).
      - Unique partial index + DuplicateKeyError ⇒ at most one active alert.
      - Retries are idempotent when the unique constraint is present.
    """
    from pymongo.errors import DuplicateKeyError  # noqa: PLC0415

    if not device.get("critical"):
        return False

    if new_status != STATUS_OFFLINE_CRITICAL:
        return False

    # Recover missed threshold: alert whenever failures >= 3 and none active.
    if int(consecutive_failures or 0) < CRITICAL_OFFLINE_ALERT_THRESHOLD:
        return False

    device_id = device.get("_id")
    hostname = device.get("hostname", "unknown")
    ip_address = device.get("ipAddress", "unknown")

    existing = db.alerts.find_one(
        _active_critical_offline_filter(device_id),
        {"_id": 1},
    )
    if existing:
        logger.info(
            "Critical offline alert already active — skip duplicate | "
            "deviceId=%s | ip=%s | alertId=%s | failures=%s | cycleId=%s",
            device_id,
            ip_address,
            existing.get("_id"),
            consecutive_failures,
            cycle_id,
        )
        return False

    message = (
        f"Critical device {hostname} ({ip_address}) transitioned to "
        f"{STATUS_OFFLINE_CRITICAL} via {scan_type} scan "
        f"(consecutiveFailures={consecutive_failures})."
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

    email_sent = send_critical_offline_alert(device, scan_type=scan_type)
    now = utc_now()
    doc = {
        "deviceId": device_id,
        "hostname": hostname,
        "ipAddress": ip_address,
        "deviceType": device.get("deviceType") or device.get("type"),
        "status": STATUS_OFFLINE_CRITICAL,
        "message": message,
        "scanType": scan_type,
        "alertType": "Device Offline",
        "category": "Device Monitoring",
        "severity": "CRITICAL",
        "emailSent": bool(email_sent),
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

    def _insert_or_existing():
        try:
            return db.alerts.insert_one(doc)
        except DuplicateKeyError:
            # Unique active-alert index: peer or prior retry already inserted.
            logger.info(
                "Critical alert insert idempotent (DuplicateKey) | "
                "deviceId=%s | ip=%s | attemptId=%s",
                device_id,
                ip_address,
                attempt_id,
            )
            return None

    try:
        result = with_mongo_retry(
            _insert_or_existing,
            action="critical_alert_insert",
            device_id=device_id,
            ip_address=ip_address,
            idempotent=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to insert critical offline alert | deviceId=%s | ip=%s | error=%s",
            device_id,
            ip_address,
            exc,
        )
        return False

    if result is None:
        return False

    try:
        assert_insert_acknowledged(
            result,
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
            "alertId": str(result.inserted_id),
            "cycleId": cycle_id,
            "attemptId": attempt_id,
        },
    )
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
