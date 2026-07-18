import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from services.settings_service import get_settings
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("email")


def _smtp_ready(smtp):
    return bool(
        smtp.get("enabled")
        and smtp.get("toAddress")
        and smtp.get("host")
        and smtp.get("user")
        and smtp.get("password")
        and smtp.get("fromAddress")
    )


def send_email(subject, body_text, body_html=None):
    settings = get_settings()
    smtp = settings.get("smtp") or {}

    if not _smtp_ready(smtp):
        logger.warning("Email alert skipped: SMTP settings are not configured")
        return False

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = smtp["fromAddress"]
    message["To"] = smtp["toAddress"]
    message.attach(MIMEText(body_text, "plain", "utf-8"))

    if body_html:
        message.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp["host"], int(smtp.get("port", 587)), timeout=30) as server:
            if smtp.get("useTls", True):
                server.starttls()
            server.login(smtp["user"], smtp["password"])
            server.sendmail(smtp["fromAddress"], [smtp["toAddress"]], message.as_string())

        logger.info("Alert email sent to %s | subject=%s", smtp["toAddress"], subject)
        return True

    except Exception as error:
        logger.exception("Failed to send alert email: %s", error)
        return False


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
          <tr><td><strong>Hostname</strong></td><td>{hostname}</td></tr>
          <tr><td><strong>IP address</strong></td><td>{ip_address}</td></tr>
          <tr><td><strong>Type</strong></td><td>{device_type}</td></tr>
          <tr><td><strong>Detected</strong></td><td>{detected_at}</td></tr>
          <tr><td><strong>Scan type</strong></td><td>{scan_type}</td></tr>
        </table>
        <p style="margin-top: 16px;">Please investigate the device as soon as possible.</p>
      </body>
    </html>
    """

    return send_email(subject, body_text, body_html)
