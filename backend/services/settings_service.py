import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from config.database import db
from utils.secret_crypto import encrypt_secret

SETTINGS_ID = "global"

DEFAULT_SETTINGS: dict[str, Any] = {
    "_id": SETTINGS_ID,
    "pingInterval": int(os.getenv("SCAN_INTERVAL", "30")),
    "pingTimeoutMs": int(os.getenv("PING_TIMEOUT_MS", "1000")),
    "pingRetries": int(os.getenv("PING_RETRIES", "3")),
    "smtp": {
        "enabled": os.getenv("ALERT_EMAIL_ENABLED", "true").lower() in ("1", "true", "yes"),
        "host": (os.getenv("SMTP_HOST") or "smtp.gmail.com").strip(),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": (os.getenv("SMTP_USER") or "").strip(),
        "password": (os.getenv("SMTP_PASSWORD") or "").strip(),
        "fromAddress": (os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "").strip(),
        "toAddress": (os.getenv("ALERT_EMAIL_TO") or "").strip(),
        "useTls": os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes"),
    },
    "mitigationMode": os.getenv("STORM_MITIGATION_MODE", "manual"),
    "autoRecovery": os.getenv("STORM_AUTO_RECOVERY", "true").lower() in ("1", "true", "yes"),
    "cooldownMinutes": int(os.getenv("STORM_RECOVERY_COOLDOWN_MINUTES", "5")),
    "stabilizationSeconds": int(os.getenv("STORM_RECOVERY_STABILIZATION_SECONDS", "60")),
    "maximumRecoveryAttempts": int(os.getenv("STORM_RECOVERY_MAX_ATTEMPTS", "3")),
    "reMitigationThreshold": int(os.getenv("STORM_RE_MITIGATION_THRESHOLD", "75")),
    "dataRetentionDays": int(os.getenv("DATA_RETENTION_DAYS", "90")),
    "incidentRetentionDays": int(os.getenv("INCIDENT_RETENTION_DAYS", "365")),
    "stormNotifications": {
        "enabled": os.getenv("STORM_EMAIL_ENABLED", "true").lower() in ("1", "true", "yes"),
        "shutdownEmails": os.getenv("STORM_EMAIL_SHUTDOWN", "true").lower()
        in ("1", "true", "yes"),
        "recoveryEmails": os.getenv("STORM_EMAIL_RECOVERY", "true").lower()
        in ("1", "true", "yes"),
        "failureEmails": os.getenv("STORM_EMAIL_FAILURE", "true").lower()
        in ("1", "true", "yes"),
        "toAddress": (os.getenv("STORM_EMAIL_TO") or "").strip(),
    },
    "updatedAt": None,
}


def ensure_settings():
    existing = db.settings.find_one({"_id": SETTINGS_ID})
    if existing:
        return existing

    doc = deepcopy(DEFAULT_SETTINGS)
    if doc["smtp"].get("password"):
        doc["smtp"]["password"] = encrypt_secret(doc["smtp"]["password"])
    doc["updatedAt"] = datetime.now(timezone.utc)
    db.settings.insert_one(doc)
    return doc


def get_settings():
    ensure_settings()
    return db.settings.find_one({"_id": SETTINGS_ID})


def get_public_settings():
    settings = get_settings()
    smtp = settings.get("smtp", {})
    return {
        "pingInterval": settings.get("pingInterval", 30),
        "pingTimeoutMs": settings.get("pingTimeoutMs", 1000),
        "pingRetries": settings.get("pingRetries", 3),
        "smtp": {
            "enabled": smtp.get("enabled", True),
            "host": smtp.get("host", ""),
            "port": smtp.get("port", 587),
            "user": smtp.get("user", ""),
            "passwordSet": bool(smtp.get("password")),
            "fromAddress": smtp.get("fromAddress", ""),
            "toAddress": smtp.get("toAddress", ""),
            "useTls": smtp.get("useTls", True),
        },
        "mitigationMode": settings.get("mitigationMode", "manual"),
        "autoRecovery": bool(settings.get("autoRecovery", True)),
        "cooldownMinutes": int(settings.get("cooldownMinutes", 5)),
        "stabilizationSeconds": int(settings.get("stabilizationSeconds", 60)),
        "maximumRecoveryAttempts": int(settings.get("maximumRecoveryAttempts", 3)),
        "reMitigationThreshold": int(settings.get("reMitigationThreshold", 75)),
        "dataRetentionDays": int(settings.get("dataRetentionDays", 90)),
        "incidentRetentionDays": int(settings.get("incidentRetentionDays", 365)),
        "stormNotifications": _public_storm_notifications(settings),
        "updatedAt": settings.get("updatedAt"),
    }


def _public_storm_notifications(settings: dict) -> dict[str, Any]:
    defaults = DEFAULT_SETTINGS["stormNotifications"]
    raw = settings.get("stormNotifications") or {}
    return {
        "enabled": bool(raw.get("enabled", defaults["enabled"])),
        "shutdownEmails": bool(raw.get("shutdownEmails", defaults["shutdownEmails"])),
        "recoveryEmails": bool(raw.get("recoveryEmails", defaults["recoveryEmails"])),
        "failureEmails": bool(raw.get("failureEmails", defaults["failureEmails"])),
        "toAddress": str(raw.get("toAddress") or defaults.get("toAddress") or "").strip(),
    }


def update_settings(payload: dict):
    ensure_settings()
    current = get_settings()
    update: dict[str, Any] = {"updatedAt": datetime.now(timezone.utc)}

    if "pingInterval" in payload and payload["pingInterval"] is not None:
        value = int(payload["pingInterval"])
        if value < 5:
            raise ValueError("pingInterval must be at least 5 seconds")
        update["pingInterval"] = value

    if "pingTimeoutMs" in payload and payload["pingTimeoutMs"] is not None:
        value = int(payload["pingTimeoutMs"])
        if value < 100:
            raise ValueError("pingTimeoutMs must be at least 100ms")
        update["pingTimeoutMs"] = value

    if "pingRetries" in payload and payload["pingRetries"] is not None:
        value = int(payload["pingRetries"])
        if value < 1:
            raise ValueError("pingRetries must be at least 1")
        update["pingRetries"] = value

    if "smtp" in payload and isinstance(payload["smtp"], dict):
        smtp = dict(current.get("smtp") or {})
        incoming = payload["smtp"]
        for key in ("enabled", "host", "port", "user", "fromAddress", "toAddress", "useTls"):
            if key in incoming:
                smtp[key] = incoming[key]
        if "password" in incoming and incoming["password"]:
            smtp["password"] = encrypt_secret(str(incoming["password"]))
        if "port" in smtp:
            smtp["port"] = int(smtp["port"])
        update["smtp"] = smtp

    if "mitigationMode" in payload and payload["mitigationMode"] is not None:
        val = str(payload["mitigationMode"]).strip().lower()
        if val not in ("automatic", "manual"):
            raise ValueError("mitigationMode must be 'automatic' or 'manual'")
        update["mitigationMode"] = val

    if "autoRecovery" in payload and payload["autoRecovery"] is not None:
        update["autoRecovery"] = bool(payload["autoRecovery"])

    if "cooldownMinutes" in payload and payload["cooldownMinutes"] is not None:
        val = int(payload["cooldownMinutes"])
        if val < 1:
            raise ValueError("cooldownMinutes must be at least 1 minute")
        update["cooldownMinutes"] = val

    if "stabilizationSeconds" in payload and payload["stabilizationSeconds"] is not None:
        val = int(payload["stabilizationSeconds"])
        if val < 5:
            raise ValueError("stabilizationSeconds must be at least 5 seconds")
        update["stabilizationSeconds"] = val

    if "maximumRecoveryAttempts" in payload and payload["maximumRecoveryAttempts"] is not None:
        val = int(payload["maximumRecoveryAttempts"])
        if val < 1:
            raise ValueError("maximumRecoveryAttempts must be at least 1")
        update["maximumRecoveryAttempts"] = val

    if "reMitigationThreshold" in payload and payload["reMitigationThreshold"] is not None:
        val = int(payload["reMitigationThreshold"])
        if val < 1 or val > 100:
            raise ValueError("reMitigationThreshold must be between 1 and 100")
        update["reMitigationThreshold"] = val

    if "dataRetentionDays" in payload and payload["dataRetentionDays"] is not None:
        from services.retention_service import clamp_retention_days  # noqa: PLC0415

        update["dataRetentionDays"] = clamp_retention_days(payload["dataRetentionDays"])

    if "incidentRetentionDays" in payload and payload["incidentRetentionDays"] is not None:
        from services.retention_service import clamp_incident_retention_days  # noqa: PLC0415

        update["incidentRetentionDays"] = clamp_incident_retention_days(
            payload["incidentRetentionDays"]
        )

    if "stormNotifications" in payload and isinstance(payload["stormNotifications"], dict):
        current_storm = dict(
            current.get("stormNotifications") or DEFAULT_SETTINGS["stormNotifications"]
        )
        incoming = payload["stormNotifications"]
        if "enabled" in incoming and incoming["enabled"] is not None:
            current_storm["enabled"] = bool(incoming["enabled"])
        if "shutdownEmails" in incoming and incoming["shutdownEmails"] is not None:
            current_storm["shutdownEmails"] = bool(incoming["shutdownEmails"])
        if "recoveryEmails" in incoming and incoming["recoveryEmails"] is not None:
            current_storm["recoveryEmails"] = bool(incoming["recoveryEmails"])
        if "failureEmails" in incoming and incoming["failureEmails"] is not None:
            current_storm["failureEmails"] = bool(incoming["failureEmails"])
        if "toAddress" in incoming and incoming["toAddress"] is not None:
            current_storm["toAddress"] = str(incoming["toAddress"]).strip()
        update["stormNotifications"] = current_storm

    db.settings.update_one({"_id": SETTINGS_ID}, {"$set": update})
    updated_doc = get_settings()

    if "dataRetentionDays" in update or "incidentRetentionDays" in update:
        try:
            from services.retention_service import ensure_retention_ttl_indexes  # noqa: PLC0415

            ensure_retention_ttl_indexes(
                retention_days=int(updated_doc.get("dataRetentionDays", 90)),
                incident_retention_days=int(
                    updated_doc.get("incidentRetentionDays", 365)
                ),
            )
        except Exception:
            # Settings write succeeded; index refresh is best-effort and logged inside.
            pass

    return updated_doc


def get_ping_config(device=None):
    settings = get_settings()
    interval = settings.get("pingInterval", 30)
    timeout_ms = settings.get("pingTimeoutMs", 1000)
    retries = settings.get("pingRetries", 3)

    if device:
        if device.get("pingInterval") is not None:
            interval = int(device["pingInterval"])
        if device.get("pingTimeoutMs") is not None:
            timeout_ms = int(device["pingTimeoutMs"])
        if device.get("pingRetries") is not None:
            retries = int(device["pingRetries"])

    return {
        "interval": interval,
        "timeout_ms": timeout_ms,
        "retries": retries,
    }
