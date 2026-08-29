"""
WhatsApp Cloud API delivery for NetPulse critical device alerts.

Uses the official Meta WhatsApp Cloud API (HTTPS template messages).
Credentials are loaded from environment variables only — never exposed via API.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

from config import whatsapp as wa_config
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("whatsapp")

_config_error_logged = False


def _mask_recipient(number: str) -> str:
    """Return a log-safe recipient label (last four digits only)."""
    digits = re.sub(r"\D", "", str(number or ""))
    if len(digits) <= 4:
        return "****"
    return f"...{digits[-4:]}"


def _parse_recipients(raw: str) -> list[str]:
    """Parse comma-separated E.164 numbers (digits only, no +)."""
    recipients: list[str] = []
    for part in (raw or "").split(","):
        digits = re.sub(r"\D", "", part.strip())
        if digits:
            recipients.append(digits)
    return recipients


def _whatsapp_settings() -> dict[str, Any]:
    return {
        "enabled": bool(wa_config.WHATSAPP_ALERTS_ENABLED),
        "accessToken": wa_config.WHATSAPP_ACCESS_TOKEN,
        "phoneNumberId": wa_config.WHATSAPP_PHONE_NUMBER_ID,
        "businessAccountId": wa_config.WHATSAPP_BUSINESS_ACCOUNT_ID,
        "apiVersion": wa_config.WHATSAPP_API_VERSION,
        "recipients": _parse_recipients(wa_config.WHATSAPP_RECIPIENT_NUMBERS),
        "criticalTemplate": wa_config.WHATSAPP_CRITICAL_ALERT_TEMPLATE,
        "recoveryTemplate": wa_config.WHATSAPP_RECOVERY_ALERT_TEMPLATE,
        "templateLanguage": wa_config.WHATSAPP_TEMPLATE_LANGUAGE,
        "timeoutSeconds": max(1, int(wa_config.WHATSAPP_REQUEST_TIMEOUT_SECONDS)),
        "criticalAlertsEnabled": bool(wa_config.WHATSAPP_CRITICAL_ALERTS_ENABLED),
        "recoveryAlertsEnabled": bool(wa_config.WHATSAPP_RECOVERY_ALERTS_ENABLED),
    }


def _log_config_error(message: str) -> None:
    global _config_error_logged  # noqa: PLW0603
    if not _config_error_logged:
        logger.error("[WHATSAPP] Configuration error — WhatsApp sending disabled | %s", message)
        _config_error_logged = True


def _validate_send_config(*, require_critical_template: bool = False,
                          require_recovery_template: bool = False) -> tuple[bool, str, dict[str, Any]]:
    cfg = _whatsapp_settings()
    if not cfg["enabled"]:
        return False, "WhatsApp alerts are disabled.", cfg

    missing: list[str] = []
    if not cfg["accessToken"]:
        missing.append("WHATSAPP_ACCESS_TOKEN")
    if not cfg["phoneNumberId"]:
        missing.append("WHATSAPP_PHONE_NUMBER_ID")
    if not cfg["apiVersion"]:
        missing.append("WHATSAPP_API_VERSION")
    if not cfg["recipients"]:
        missing.append("WHATSAPP_RECIPIENT_NUMBERS")
    if require_critical_template and not cfg["criticalTemplate"]:
        missing.append("WHATSAPP_CRITICAL_ALERT_TEMPLATE")
    if require_recovery_template and not cfg["recoveryTemplate"]:
        missing.append("WHATSAPP_RECOVERY_ALERT_TEMPLATE")

    if missing:
        message = f"Missing required configuration: {', '.join(missing)}"
        _log_config_error(message)
        return False, message, cfg

    return True, "", cfg


def get_public_whatsapp_status() -> dict[str, Any]:
    """Safe status for APIs — never includes the access token."""
    cfg = _whatsapp_settings()
    ok, _, _ = _validate_send_config(
        require_critical_template=True,
        require_recovery_template=True,
    )
    return {
        "enabled": bool(cfg["enabled"]),
        "configured": bool(ok),
        "recipientCount": len(cfg["recipients"]),
        "criticalAlertsEnabled": bool(cfg["criticalAlertsEnabled"]),
        "recoveryAlertsEnabled": bool(cfg["recoveryAlertsEnabled"]),
        "criticalTemplate": cfg["criticalTemplate"] if cfg["enabled"] else "",
        "recoveryTemplate": cfg["recoveryTemplate"] if cfg["enabled"] else "",
    }


def _api_url(cfg: dict[str, Any]) -> str:
    version = str(cfg["apiVersion"]).lstrip("/")
    phone_id = cfg["phoneNumberId"]
    return f"https://graph.facebook.com/{version}/{phone_id}/messages"


def _build_template_payload(
    recipient: str,
    template_name: str,
    body_parameters: list[str],
    *,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": cfg["templateLanguage"]},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(value)[:1024]}
                        for value in body_parameters
                    ],
                }
            ],
        },
    }


def _send_template_to_recipient(
    recipient: str,
    template_name: str,
    body_parameters: list[str],
    *,
    cfg: dict[str, Any],
    device_name: str,
    alert_kind: str,
) -> tuple[bool, str]:
    url = _api_url(cfg)
    payload = _build_template_payload(
        recipient,
        template_name,
        body_parameters,
        cfg=cfg,
    )
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {cfg['accessToken']}",
            "Content-Type": "application/json",
        },
    )

    masked = _mask_recipient(recipient)
    logger.info(
        "[WHATSAPP] Sending %s alert | device=%s recipient=%s template=%s",
        alert_kind,
        device_name,
        masked,
        template_name,
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=cfg["timeoutSeconds"],
        ) as response:
            status = getattr(response, "status", 200)
            if status < 200 or status >= 300:
                logger.error(
                    "[WHATSAPP] Alert failed | device=%s recipient=%s status=%s",
                    device_name,
                    masked,
                    status,
                )
                return False, f"WhatsApp API returned HTTP {status}."
            logger.info(
                "[WHATSAPP] Alert sent successfully | device=%s recipient=%s",
                device_name,
                masked,
            )
            return True, ""
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body else {}
            detail = (
                parsed.get("error", {}).get("message")
                or parsed.get("error", {}).get("error_user_msg")
                or ""
            )
        except Exception:  # noqa: BLE001
            detail = ""
        logger.error(
            "[WHATSAPP] Alert failed | device=%s recipient=%s status=%s detail=%s",
            device_name,
            masked,
            exc.code,
            detail or type(exc).__name__,
        )
        friendly = detail or f"WhatsApp API request failed with HTTP {exc.code}."
        return False, friendly
    except urllib.error.URLError as exc:
        logger.error(
            "[WHATSAPP] Alert failed | device=%s recipient=%s error=%s",
            device_name,
            masked,
            type(exc).__name__,
        )
        return False, "Unable to reach the WhatsApp API. Check network connectivity."
    except TimeoutError:
        logger.error(
            "[WHATSAPP] Alert failed | device=%s recipient=%s error=timeout",
            device_name,
            masked,
        )
        return False, "WhatsApp API request timed out."
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[WHATSAPP] Alert failed | device=%s recipient=%s error=%s",
            device_name,
            masked,
            type(exc).__name__,
        )
        return False, "Failed to send WhatsApp alert."


def _dispatch_template_alert(
    *,
    alert_kind: str,
    device_name: str,
    template_name: str,
    body_parameters: list[str],
    setting_flag: str,
) -> bool:
    """Send a template alert to all configured recipients. Never raises."""
    try:
        require_critical = alert_kind == "critical"
        require_recovery = alert_kind == "recovery"
        ok, message, cfg = _validate_send_config(
            require_critical_template=require_critical,
            require_recovery_template=require_recovery,
        )
        if not ok:
            if cfg.get("enabled"):
                logger.warning(
                    "[WHATSAPP] Skipped %s alert | device=%s reason=%s",
                    alert_kind,
                    device_name,
                    message,
                )
            return False

        if not cfg.get(setting_flag):
            logger.info(
                "[WHATSAPP] Skipped %s alert (%s disabled) | device=%s",
                alert_kind,
                setting_flag,
                device_name,
            )
            return False

        any_sent = False
        for recipient in cfg["recipients"]:
            sent, _ = _send_template_to_recipient(
                recipient,
                template_name,
                body_parameters,
                cfg=cfg,
                device_name=device_name,
                alert_kind=alert_kind,
            )
            any_sent = any_sent or sent
        return any_sent
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[WHATSAPP] Dispatch failed | kind=%s device=%s | %s",
            alert_kind,
            device_name,
            exc,
        )
        return False


def _format_timestamp(value: Optional[datetime] = None) -> str:
    ts = value or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def send_critical_offline_whatsapp_alert(device, scan_type: str = "Automatic") -> bool:
    """
    Send a WhatsApp template alert when a critical device is confirmed offline.

    Template body variables (create in Meta Business Manager):
      {{1}} device name, {{2}} IP, {{3}} status, {{4}} severity, {{5}} timestamp
    """
    hostname = device.get("hostname", "Unknown")
    ip_address = device.get("ipAddress", "Unknown")
    detected_at = _format_timestamp()
    body_parameters = [
        str(hostname),
        str(ip_address),
        "OFFLINE",
        "CRITICAL",
        detected_at,
    ]
    return _dispatch_template_alert(
        alert_kind="critical",
        device_name=str(hostname),
        template_name=_whatsapp_settings()["criticalTemplate"],
        body_parameters=body_parameters,
        setting_flag="criticalAlertsEnabled",
    )


def send_device_recovery_whatsapp_alert(device) -> bool:
    """
    Send a WhatsApp template alert when a critical device recovers to Online.

    Template body variables:
      {{1}} device name, {{2}} IP, {{3}} status, {{4}} timestamp
    """
    hostname = device.get("hostname", "Unknown")
    ip_address = device.get("ipAddress", "Unknown")
    recovered_at = _format_timestamp()
    body_parameters = [
        str(hostname),
        str(ip_address),
        "ONLINE",
        recovered_at,
    ]
    return _dispatch_template_alert(
        alert_kind="recovery",
        device_name=str(hostname),
        template_name=_whatsapp_settings()["recoveryTemplate"],
        body_parameters=body_parameters,
        setting_flag="recoveryAlertsEnabled",
    )


def send_test_whatsapp_alert() -> tuple[bool, str]:
    """
    Send a test WhatsApp template message to all configured recipients.

    Uses the critical alert template with sample values.
    """
    ok, message, cfg = _validate_send_config(require_critical_template=True)
    if not ok:
        return False, message

    sample_device = {
        "hostname": "NetPulse-Test",
        "ipAddress": "192.0.2.1",
    }
    body_parameters = [
        "NetPulse-Test",
        "192.0.2.1",
        "TEST",
        "INFO",
        _format_timestamp(),
    ]

    errors: list[str] = []
    sent_any = False
    for recipient in cfg["recipients"]:
        sent, error = _send_template_to_recipient(
            recipient,
            cfg["criticalTemplate"],
            body_parameters,
            cfg=cfg,
            device_name=sample_device["hostname"],
            alert_kind="test",
        )
        if sent:
            sent_any = True
        elif error:
            errors.append(error)

    if sent_any:
        return True, ""
    return False, errors[0] if errors else "Failed to send test WhatsApp alert."
