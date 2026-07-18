import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

ALERT_EMAIL_TO = (os.getenv("ALERT_EMAIL_TO") or "").strip()
SMTP_HOST = (os.getenv("SMTP_HOST") or "smtp.gmail.com").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = (os.getenv("SMTP_USER") or "").strip()
SMTP_PASSWORD = (os.getenv("SMTP_PASSWORD") or "").strip()
SMTP_FROM = (os.getenv("SMTP_FROM") or SMTP_USER).strip()
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")
ALERT_EMAIL_ENABLED = os.getenv("ALERT_EMAIL_ENABLED", "true").lower() in ("1", "true", "yes")


def email_alerts_configured():
    return bool(
        ALERT_EMAIL_ENABLED
        and ALERT_EMAIL_TO
        and SMTP_HOST
        and SMTP_USER
        and SMTP_PASSWORD
        and SMTP_FROM
    )
