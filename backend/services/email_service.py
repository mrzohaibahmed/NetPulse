"""
Email delivery for NetPulse alerts and storm protection notifications.

Reuses a single SMTP path — do not add parallel SMTP clients elsewhere.
Supports Gmail and Outlook / Microsoft 365 as configurable SMTP providers.
The recipient address is always independent of the sender provider.
"""

from __future__ import annotations

import html
import smtplib
import socket
import ssl
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

from services.settings_service import get_settings, normalize_provider
from utils.monitor_logger import get_monitor_logger
from utils.secret_crypto import decrypt_secret

logger = get_monitor_logger("email")

# ---------------------------------------------------------------------------
# Provider SMTP presets (administrator may override any field)
# ---------------------------------------------------------------------------

PROVIDER_PRESETS: dict[str, dict] = {
    "gmail": {
        "host": "smtp.gmail.com",
        "port": 587,
        "useTls": True,
    },
    "outlook": {
        "host": "smtp.office365.com",
        "port": 587,
        "useTls": True,
    },
}

# Subjects (exact product copy)
SUBJECT_CONFIRMED = "🚨 CRITICAL: Storm Confirmed on Switch Port"
SUBJECT_SHUTDOWN = "🚨 CRITICAL: Storm Detected - Port Automatically Shut Down"
SUBJECT_RECOVERY = "✅ INFO: Port Automatically Restored"
SUBJECT_MITIGATION_FAILURE = "❌ WARNING: Automatic Port Shutdown Failed"
SUBJECT_RECOVERY_FAILURE = "⚠ WARNING: Automatic Port Recovery Failed"
SUBJECT_REMITIGATION_BLOCKED = (
    "🚨 CRITICAL: Re-Mitigation Blocked — Manual Intervention Required"
)


def _smtp_ready(smtp: dict) -> bool:
    return bool(
        smtp.get("enabled")
        and smtp.get("toAddress")
        and smtp.get("host")
        and smtp.get("user")
        and smtp.get("password")
        and smtp.get("fromAddress")
    )


def _open_smtp_connection(host: str, port: int, use_tls: bool) -> smtplib.SMTP:
    """
    Create an SMTP client with the correct security mode.

    Port 465 → SMTP_SSL (implicit TLS); caller must not call STARTTLS.
    Other ports → SMTP; caller applies STARTTLS when ``use_tls`` is True.

    TLS certificate verification uses Python defaults (never disabled).
    """
    if port == 465:
        return smtplib.SMTP_SSL(host, port, timeout=30)
    return smtplib.SMTP(host, port, timeout=30)


def _authenticate_smtp(
    server: smtplib.SMTP,
    *,
    port: int,
    use_tls: bool,
    user: str,
    password: str,
) -> None:
    """Apply STARTTLS when appropriate, then authenticate."""
    if port != 465 and use_tls:
        server.starttls()
    server.login(user, password)


def _classify_smtp_error(error: Exception, provider: str) -> str:
    """Return a user-friendly, credential-safe error description."""
    msg = str(error).lower()

    if isinstance(error, smtplib.SMTPAuthenticationError):
        if provider == "outlook":
            return (
                "Email authentication failed. For Outlook / Microsoft 365, verify that "
                "SMTP AUTH is enabled for this account or tenant, and that the username "
                "and password (or App Password) are correct."
            )
        return (
            "Email authentication failed. Verify the SMTP username and password. "
            "For Gmail, use an App Password — normal account passwords are not accepted."
        )

    if isinstance(error, smtplib.SMTPConnectError):
        return "Unable to connect to the configured SMTP server. Check the host and port."

    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "The recipient email address was rejected by the SMTP server. Verify the address."

    if isinstance(error, smtplib.SMTPSenderRefused):
        return (
            "The configured sender email address was rejected by the SMTP server. "
            "Verify the From address matches the authenticated account."
        )

    if isinstance(error, ssl.SSLError):
        return "TLS/SSL negotiation with the SMTP server failed. Check the port and security settings."

    if isinstance(error, (socket.timeout, TimeoutError)):
        return "Connection to the SMTP server timed out. Check the host and network connectivity."

    if isinstance(error, socket.gaierror):
        return "Unable to resolve the SMTP server hostname. Check the host setting."

    if "smtp auth" in msg or "authentication" in msg or "535" in msg or "534" in msg:
        if provider == "outlook":
            return (
                "Outlook / Microsoft 365 SMTP authentication is unavailable for this "
                "account or tenant. Verify that SMTP AUTH is enabled or use the "
                "supported authentication method."
            )
        return "Email authentication failed. Verify the SMTP username and password."

    if "recipient" in msg or "550" in msg or "551" in msg or "553" in msg:
        return "The recipient email address is invalid or rejected by the server."

    if "sender" in msg or "501" in msg or "503" in msg:
        return "The configured sender email address is invalid."

    return "Failed to send email via the configured SMTP server."


def send_email(
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
    *,
    to_address: Optional[str] = None,
) -> bool:
    """
    Send an email using the global SMTP settings.

    The provider (gmail / outlook) controls which SMTP server is used.
    The recipient is always independent of the provider — any valid
    email address may receive alerts regardless of provider choice.

    ``to_address`` optionally overrides ``smtp.toAddress`` (storm recipient).
    Never raises — returns False on skip/failure.
    """
    try:
        settings = get_settings()
        smtp = dict(settings.get("smtp") or {})
        if smtp.get("password"):
            smtp["password"] = decrypt_secret(smtp["password"])

        recipient = (to_address or "").strip() or (smtp.get("toAddress") or "").strip()
        if recipient:
            smtp["toAddress"] = recipient

        if not _smtp_ready(smtp):
            logger.warning("Email skipped: SMTP settings are not configured")
            return False

        provider = normalize_provider(smtp.get("provider", "gmail"))
        host = str(smtp["host"])
        port = int(smtp.get("port", 587))
        use_tls = bool(smtp.get("useTls", True))
        user = str(smtp["user"])
        password = str(smtp["password"])
        from_address = str(smtp["fromAddress"])
        from_name = str(smtp.get("fromName") or "NetPulse")
        to = str(smtp["toAddress"])

        formatted_from = f"{from_name} <{from_address}>" if from_name else from_address

        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = formatted_from
        message["To"] = to
        message.attach(MIMEText(body_text, "plain", "utf-8"))

        if body_html:
            message.attach(MIMEText(body_html, "html", "utf-8"))

        with _open_smtp_connection(host, port, use_tls) as server:
            _authenticate_smtp(
                server,
                port=port,
                use_tls=use_tls,
                user=user,
                password=password,
            )
            server.sendmail(from_address, [to], message.as_string())

        logger.info(
            "Email sent | provider=%s to=%s subject=%s",
            provider,
            to,
            subject,
        )
        return True

    except Exception as error:  # noqa: BLE001
        logger.error("Email send failed: %s", type(error).__name__)
        logger.debug("Email error detail: %s", error)
        return False


def send_email_with_result(
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
    *,
    to_address: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Like ``send_email`` but also returns a user-friendly error message.

    Used by the test-email API endpoint so the frontend can display
    a meaningful error without exposing credentials or stack traces.
    Returns ``(True, "")`` on success or ``(False, "<friendly-message>")`` on failure.
    """
    settings = get_settings()
    smtp = dict(settings.get("smtp") or {})
    if smtp.get("password"):
        smtp["password"] = decrypt_secret(smtp["password"])

    recipient = (to_address or "").strip() or (smtp.get("toAddress") or "").strip()
    if recipient:
        smtp["toAddress"] = recipient

    if not smtp.get("enabled"):
        return False, "Email alerts are disabled. Enable them in settings first."

    if not smtp.get("host"):
        return False, "SMTP host is not configured."

    if not smtp.get("user"):
        return False, "SMTP username is not configured."

    if not smtp.get("password"):
        return False, "SMTP password is not configured."

    if not smtp.get("fromAddress"):
        return False, "Sender (From) email address is not configured."

    if not smtp.get("toAddress"):
        return False, "Recipient (To) email address is not configured."

    provider = normalize_provider(smtp.get("provider", "gmail"))
    host = str(smtp["host"])
    port = int(smtp.get("port", 587))
    use_tls = bool(smtp.get("useTls", True))
    user = str(smtp["user"])
    password = str(smtp["password"])
    from_address = str(smtp["fromAddress"])
    from_name = str(smtp.get("fromName") or "NetPulse")
    to = str(smtp["toAddress"])

    formatted_from = f"{from_name} <{from_address}>" if from_name else from_address

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = formatted_from
    message["To"] = to
    message.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        message.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with _open_smtp_connection(host, port, use_tls) as server:
            _authenticate_smtp(
                server,
                port=port,
                use_tls=use_tls,
                user=user,
                password=password,
            )
            server.sendmail(from_address, [to], message.as_string())

        logger.info(
            "Test email sent | provider=%s to=%s",
            provider,
            to,
        )
        return True, ""

    except Exception as error:  # noqa: BLE001
        friendly = _classify_smtp_error(error, provider)
        logger.error(
            "Test email failed | provider=%s host=%s port=%d to=%s | %s",
            provider,
            host,
            port,
            to,
            type(error).__name__,
        )
        logger.debug("SMTP error detail: %s", error)
        return False, friendly


def send_critical_offline_alert(device, scan_type="Automatic"):
    hostname = device.get("hostname", "Unknown")
    ip_address = device.get("ipAddress", "Unknown")
    device_type = device.get("deviceType") or device.get("type") or "Unknown"
    detected_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    subject = f"[NetPulse Alert] Critical device offline: {hostname}"
    body_text = (
        "NetPulse Network Monitor Alert\n"
        "================================\n\n"
        f"A critical device has gone offline.\n\n"
        f"Hostname:   {hostname}\n"
        f"IP address: {ip_address}\n"
        f"Type:       {device_type}\n"
        f"Detected:   {detected_at}\n"
        f"Scan type:  {scan_type}\n\n"
        "Please investigate the device as soon as possible."
    )
    body_html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #132033;">
        <h2 style="color: #c23b3b;">Critical device offline</h2>
        <p>A critical device monitored by NetPulse is unreachable.</p>
        <table cellpadding="6" cellspacing="0" style="border-collapse: collapse;">
          <tr><td><strong>Hostname</strong></td><td>{html.escape(str(hostname))}</td></tr>
          <tr><td><strong>IP address</strong></td><td>{html.escape(str(ip_address))}</td></tr>
          <tr><td><strong>Type</strong></td><td>{html.escape(str(device_type))}</td></tr>
          <tr><td><strong>Detected</strong></td><td>{detected_at}</td></tr>
          <tr><td><strong>Scan type</strong></td><td>{html.escape(str(scan_type))}</td></tr>
        </table>
        <p style="margin-top: 16px;">Please investigate the device as soon as possible.</p>
      </body>
    </html>
    """

    return send_email(subject, body_text, body_html)


# ---------------------------------------------------------------------------
# Storm protection notifications
# ---------------------------------------------------------------------------


def _storm_notification_settings() -> dict[str, Any]:
    settings = get_settings() or {}
    raw = settings.get("stormNotifications") or {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "shutdownEmails": bool(raw.get("shutdownEmails", True)),
        "recoveryEmails": bool(raw.get("recoveryEmails", True)),
        "failureEmails": bool(raw.get("failureEmails", True)),
        "toAddress": str(raw.get("toAddress") or "").strip(),
    }


def _storm_recipient(cfg: dict[str, Any]) -> Optional[str]:
    if cfg.get("toAddress"):
        return cfg["toAddress"]
    smtp = (get_settings() or {}).get("smtp") or {}
    return str(smtp.get("toAddress") or "").strip() or None


def _fmt_ts(value: Any = None) -> str:
    if isinstance(value, datetime):
        ts = value
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _escape(value: Any) -> str:
    if value is None:
        return "—"
    return html.escape(str(value))


def _risk_score_from_incident(incident: Optional[dict]) -> Any:
    if not incident:
        return None
    risk = incident.get("risk") or {}
    if isinstance(risk, dict) and risk.get("riskScore") is not None:
        return risk.get("riskScore")
    trigger = incident.get("trigger") or {}
    if isinstance(trigger, dict) and trigger.get("risk") is not None:
        return trigger.get("risk")
    return None


def _recovery_duration_label(incident: Optional[dict], recovered_at: Optional[datetime] = None) -> str:
    """Best-effort duration from mitigation/creation to recovery."""
    if not incident:
        return "—"
    end = recovered_at or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    start = None
    for key in ("mitigatedAt", "updatedAt", "createdAt"):
        candidate = incident.get(key)
        if isinstance(candidate, datetime):
            start = candidate
            if key == "mitigatedAt":
                break
    # Prefer timeline "Shutdown Executed" / "Verification Passed" for shutdown
    for event in reversed(list(incident.get("timeline") or [])):
        name = str(event.get("event") or "")
        if name in ("Shutdown Executed", "Verification Passed", "Mitigation Preparation Ready"):
            ts = event.get("time")
            if isinstance(ts, datetime):
                start = ts
                if name == "Shutdown Executed":
                    break

    if not isinstance(start, datetime):
        return "—"
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    seconds = max(0, int((end - start).total_seconds()))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _build_storm_email(
    *,
    event_type: str,
    banner_color: str,
    banner_label: str,
    incident: dict,
    action_performed: str,
    action_status: str,
    reason: str,
    verification_result: Any,
    operator: str = "SYSTEM",
    recovery_duration: Optional[str] = None,
    timestamp: Optional[datetime] = None,
    suggested_action: Optional[str] = None,
) -> tuple[str, str]:
    hostname = incident.get("hostname") or "Unknown"
    ip_address = incident.get("ipAddress") or "Unknown"
    interface = incident.get("interface") or "Unknown"
    incident_id = incident.get("incidentId") or "—"
    severity = incident.get("severity") or "CRITICAL"
    risk_score = _risk_score_from_incident(incident)
    device_name = hostname
    ts = _fmt_ts(timestamp)
    verification_text = verification_result
    if isinstance(verification_result, dict):
        if verification_result.get("success") is True:
            verification_text = "Passed"
            if verification_result.get("output"):
                verification_text = f"Passed — {verification_result.get('output')}"
        elif verification_result.get("success") is False:
            err = verification_result.get("error") or verification_result.get("output") or "Failed"
            verification_text = str(err)
        else:
            verification_text = str(verification_result)
    verification_text = str(verification_text or "—")[:500]

    rows = [
        ("Event Type", event_type),
        ("Device Name", device_name),
        ("Hostname", hostname),
        ("Device IP", ip_address),
        ("Interface", interface),
        ("Incident ID", incident_id),
        ("Severity", severity),
        ("Risk Score", risk_score if risk_score is not None else "—"),
        ("Action Performed", action_performed),
        ("Action Status", action_status),
        ("Operator", operator or "SYSTEM"),
        ("Timestamp", ts),
        ("Reason", reason or "—"),
        ("Verification Result", verification_text),
    ]
    if recovery_duration is not None:
        rows.insert(-2, ("Recovery Duration", recovery_duration))

    text_lines = [
        "NetPulse Storm Protection Notification",
        "=" * 40,
        "",
        f"{banner_label}: {event_type}",
        "",
    ]
    for label, value in rows:
        text_lines.append(f"{label}: {value}")
    if suggested_action:
        text_lines.extend(
            [
                "",
                "Suggested Action for Network Engineer:",
                "-" * 40,
                suggested_action,
            ]
        )
    text_lines.extend(
        [
            "",
            "This message was generated automatically by NetPulse.",
            "Do not reply to this email.",
        ]
    )
    body_text = "\n".join(text_lines)

    table_rows = "".join(
        f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #e5eaf0;color:#5a6a7a;width:38%;font-size:13px;">
            {html.escape(label)}
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5eaf0;color:#132033;font-size:13px;font-weight:600;">
            {_escape(value)}
          </td>
        </tr>
        """
        for label, value in rows
    )

    suggested_action_html = ""
    if suggested_action:
        suggested_action_html = (
            '<tr>'
            '<td style="padding:0 28px 24px 28px;">'
            '<div style="background:#fefce8;border:1px solid #facc15;border-radius:8px;padding:14px 16px;">'
            '<div style="font-size:12px;font-weight:700;color:#854d0e;text-transform:uppercase;letter-spacing:0.06em;">'
            '&#x26A0; Suggested Action for Network Engineer'
            '</div>'
            f'<div style="margin-top:6px;font-size:14px;color:#422006;line-height:1.5;">'
            f'{_escape(suggested_action)}'
            '</div>'
            '</div>'
            '</td>'
            '</tr>'
        )

    body_html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>NetPulse Storm Notification</title>
</head>
<body style="margin:0;padding:0;background:#f4f7fb;font-family:Segoe UI,Arial,Helvetica,sans-serif;color:#132033;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f7fb;padding:24px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:640px;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #d9e2ec;">
          <tr>
            <td style="background:#0b1f33;padding:22px 28px;">
              <div style="font-size:22px;font-weight:700;letter-spacing:0.4px;color:#ffffff;">
                NetPulse
              </div>
              <div style="margin-top:4px;font-size:13px;color:#9fb3c8;">
                Network Monitor · Storm Protection
              </div>
            </td>
          </tr>
          <tr>
            <td style="background:{banner_color};padding:14px 28px;color:#ffffff;font-size:15px;font-weight:700;">
              {html.escape(banner_label)} — {html.escape(event_type)}
            </td>
          </tr>
          <tr>
            <td style="padding:24px 28px 8px 28px;">
              <p style="margin:0 0 16px 0;font-size:14px;line-height:1.5;color:#334155;">
                An automatic storm-protection action has completed on your network.
              </p>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #e5eaf0;border-radius:8px;overflow:hidden;">
                {table_rows}
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 28px 24px 28px;">
              <div style="background:#f8fafc;border:1px solid #e5eaf0;border-radius:8px;padding:14px 16px;">
                <div style="font-size:12px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.06em;">
                  Action Summary
                </div>
                <div style="margin-top:6px;font-size:14px;color:#132033;">
                  <strong>{_escape(action_performed)}</strong>
                  &nbsp;→&nbsp;
                  <strong>{_escape(action_status)}</strong>
                  on <strong>{_escape(interface)}</strong>
                  ({_escape(hostname)} / {_escape(ip_address)})
                </div>
              </div>
            </td>
          </tr>
          {suggested_action_html}
          <tr>
            <td style="background:#0b1f33;padding:16px 28px;color:#9fb3c8;font-size:12px;line-height:1.5;">
              <div>NetPulse Storm Protection · Automated notification</div>
              <div style="margin-top:4px;">Sent {_escape(ts)} · Operator {_escape(operator or "SYSTEM")}</div>
              <div style="margin-top:8px;color:#6b8299;">This is an automated message. Please do not reply.</div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
    return body_text, body_html


def _audit_storm_email(
    *,
    subject: str,
    recipient: Optional[str],
    incident_id: Any,
    delivered: bool,
    event_type: str,
) -> None:
    try:
        from services.audit_service import log_audit  # noqa: PLC0415

        log_audit(
            action="storm_email_notification",
            entity_type="incident",
            entity_id=incident_id,
            details={
                "emailSent": bool(delivered),
                "deliveryStatus": "SENT" if delivered else "FAILED",
                "recipient": recipient,
                "subject": subject,
                "eventType": event_type,
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Storm email audit log failed: %s", exc)


def _dispatch_storm_email(
    *,
    kind: str,
    subject: str,
    incident: dict,
    banner_color: str,
    banner_label: str,
    event_type: str,
    action_performed: str,
    action_status: str,
    reason: str,
    verification_result: Any,
    operator: str = "SYSTEM",
    recovery_duration: Optional[str] = None,
    suggested_action: Optional[str] = None,
    setting_flag: str,
) -> bool:
    """Shared gate + send + audit. Never raises."""
    try:
        cfg = _storm_notification_settings()
        if not cfg.get("enabled"):
            logger.info("Storm email skipped (notifications disabled) | kind=%s", kind)
            return False
        if not cfg.get(setting_flag):
            logger.info("Storm email skipped (%s disabled) | kind=%s", setting_flag, kind)
            return False

        recipient = _storm_recipient(cfg)
        body_text, body_html = _build_storm_email(
            event_type=event_type,
            banner_color=banner_color,
            banner_label=banner_label,
            incident=incident or {},
            action_performed=action_performed,
            action_status=action_status,
            reason=reason,
            verification_result=verification_result,
            operator=operator or "SYSTEM",
            recovery_duration=recovery_duration,
            suggested_action=suggested_action,
        )
        delivered = send_email(
            subject,
            body_text,
            body_html,
            to_address=recipient,
        )
        _audit_storm_email(
            subject=subject,
            recipient=recipient,
            incident_id=(incident or {}).get("incidentId"),
            delivered=delivered,
            event_type=event_type,
        )
        return delivered
    except Exception as exc:  # noqa: BLE001
        logger.exception("Storm email dispatch failed | kind=%s | %s", kind, exc)
        return False


def send_storm_confirmed_notification(
    incident: dict,
    *,
    reason: str = "Storm confirmed on switch port",
    operator: str = "SYSTEM",
) -> bool:
    """Email when storm confirmation first reaches CONFIRMED for a port."""
    return _dispatch_storm_email(
        kind="confirmed",
        subject=SUBJECT_CONFIRMED,
        incident=incident,
        banner_color="#b91c1c",
        banner_label="CRITICAL",
        event_type="Storm Confirmed",
        action_performed="NONE",
        action_status="CONFIRMED",
        reason=reason,
        verification_result=None,
        operator=operator,
        suggested_action=(
            "Investigate the switch port for broadcast/multicast storms or loops. "
            "Automatic mitigation may follow if safety checks pass."
        ),
        setting_flag="enabled",
    )


def send_storm_shutdown_notification(
    incident: dict,
    *,
    verification_result: Any = None,
    reason: str = "Storm confirmed — automatic port shutdown",
    operator: str = "SYSTEM",
) -> bool:
    """Email after verified automatic SHUTDOWN → MITIGATED."""
    return _dispatch_storm_email(
        kind="shutdown",
        subject=SUBJECT_SHUTDOWN,
        incident=incident,
        banner_color="#b91c1c",
        banner_label="CRITICAL",
        event_type="Automatic Port Shutdown",
        action_performed="SHUTDOWN",
        action_status="MITIGATED",
        reason=reason,
        verification_result=verification_result if verification_result is not None else {"success": True},
        operator=operator,
        suggested_action=(
            "Please inspect the physical port and trace the cable to identify "
            "any unauthorized switches or routing loops before manually "
            "recovering this interface."
        ),
        setting_flag="shutdownEmails",
    )


def send_storm_recovery_notification(
    incident: dict,
    *,
    verification_result: Any = None,
    reason: str = "Automatic recovery verified — port restored",
    operator: str = "SYSTEM",
    recovered_at: Optional[datetime] = None,
) -> bool:
    """Email after verified automatic recovery (port restored / MONITORING)."""
    duration = _recovery_duration_label(incident, recovered_at)
    return _dispatch_storm_email(
        kind="recovery",
        subject=SUBJECT_RECOVERY,
        incident=incident,
        banner_color="#15803d",
        banner_label="INFO",
        event_type="Automatic Port Recovery",
        action_performed="NO SHUTDOWN (Restore)",
        action_status="RECOVERED",
        reason=reason,
        verification_result=verification_result if verification_result is not None else {"success": True},
        operator=operator,
        recovery_duration=duration,
        setting_flag="recoveryEmails",
    )


def send_storm_mitigation_failure(
    incident: dict,
    *,
    verification_result: Any = None,
    reason: str = "Automatic mitigation failed",
    operator: str = "SYSTEM",
    action_status: str = "MITIGATION_FAILED",
) -> bool:
    """Email when automatic mitigation execution fails."""
    return _dispatch_storm_email(
        kind="mitigation_failure",
        subject=SUBJECT_MITIGATION_FAILURE,
        incident=incident,
        banner_color="#c2410c",
        banner_label="WARNING",
        event_type="Automatic Mitigation Failure",
        action_performed="SHUTDOWN",
        action_status=action_status,
        reason=reason,
        verification_result=verification_result if verification_result is not None else {"success": False},
        operator=operator,
        setting_flag="failureEmails",
    )


def send_storm_recovery_failure(
    incident: dict,
    *,
    verification_result: Any = None,
    reason: str = "Automatic recovery failed",
    operator: str = "SYSTEM",
    action_status: str = "RECOVERY_FAILED",
) -> bool:
    """Email when automatic recovery execution fails."""
    return _dispatch_storm_email(
        kind="recovery_failure",
        subject=SUBJECT_RECOVERY_FAILURE,
        incident=incident,
        banner_color="#b45309",
        banner_label="WARNING",
        event_type="Automatic Recovery Failure",
        action_performed="NO SHUTDOWN (Restore)",
        action_status=action_status,
        reason=reason,
        verification_result=verification_result if verification_result is not None else {"success": False},
        operator=operator,
        setting_flag="failureEmails",
    )


def send_storm_remitigation_blocked_notification(
    incident: dict,
    *,
    reason: str,
    failed_rule: Optional[str] = None,
    operator: str = "SYSTEM",
) -> bool:
    """Email when post-recovery automatic re-mitigation is blocked."""
    detail = reason
    if failed_rule:
        detail = f"{reason}\n\nFailed rule: {failed_rule}"
    return _dispatch_storm_email(
        kind="remitigation_blocked",
        subject=SUBJECT_REMITIGATION_BLOCKED,
        incident=incident,
        banner_color="#c23b3b",
        banner_label="CRITICAL",
        event_type="Re-Mitigation Blocked",
        action_performed="SHUTDOWN",
        action_status="ESCALATED",
        reason=detail,
        verification_result={"success": False, "failedRule": failed_rule},
        operator=operator,
        setting_flag="failureEmails",
    )
