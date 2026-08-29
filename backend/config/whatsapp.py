import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


WHATSAPP_ALERTS_ENABLED = _env_bool("WHATSAPP_ALERTS_ENABLED", "false")
WHATSAPP_ACCESS_TOKEN = (os.getenv("WHATSAPP_ACCESS_TOKEN") or "").strip()
WHATSAPP_PHONE_NUMBER_ID = (os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
WHATSAPP_BUSINESS_ACCOUNT_ID = (os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID") or "").strip()
WHATSAPP_API_VERSION = (os.getenv("WHATSAPP_API_VERSION") or "v21.0").strip()
WHATSAPP_RECIPIENT_NUMBERS = (os.getenv("WHATSAPP_RECIPIENT_NUMBERS") or "").strip()
WHATSAPP_CRITICAL_ALERT_TEMPLATE = (
    os.getenv("WHATSAPP_CRITICAL_ALERT_TEMPLATE") or "netpulse_critical_alert"
).strip()
WHATSAPP_RECOVERY_ALERT_TEMPLATE = (
    os.getenv("WHATSAPP_RECOVERY_ALERT_TEMPLATE") or "netpulse_device_recovery"
).strip()
WHATSAPP_TEMPLATE_LANGUAGE = (os.getenv("WHATSAPP_TEMPLATE_LANGUAGE") or "en").strip()
WHATSAPP_REQUEST_TIMEOUT_SECONDS = int(os.getenv("WHATSAPP_REQUEST_TIMEOUT_SECONDS", "10"))
WHATSAPP_CRITICAL_ALERTS_ENABLED = _env_bool("WHATSAPP_CRITICAL_ALERTS_ENABLED", "true")
WHATSAPP_RECOVERY_ALERTS_ENABLED = _env_bool("WHATSAPP_RECOVERY_ALERTS_ENABLED", "true")
