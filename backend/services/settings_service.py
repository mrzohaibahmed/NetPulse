import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from config.database import db
from utils.secret_crypto import encrypt_secret

SETTINGS_ID = "global"

DEFAULT_SETTINGS: dict[str, Any] = {
    "_id": SETTINGS_ID,
    # Per-device monitoring cadence (seconds). Target: 60s for 500+ fleets.
    "pingInterval": int(os.getenv("SCAN_INTERVAL", "60")),
    "pingTimeoutMs": int(os.getenv("PING_TIMEOUT_MS", "1000")),
    # Total ICMP attempts per scan (not "retries after first success/fail").
    "pingRetries": int(os.getenv("PING_RETRIES", "3")),
    # Complete failed SCANS required before leaving Online (not per-ICMP attempt).
    "pingFailureConfirmationScans": int(
        os.getenv("PING_FAILURE_CONFIRMATION_SCANS", "2")
    ),
    # Max concurrent device pings (bounded workers). Target band: 25–40.
    # 40 leaves headroom for 500-device all-timeout waves within a 60s cadence.
    "pingConcurrency": int(os.getenv("MONITOR_PING_CONCURRENCY", "40")),
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
    "reMitigationThreshold": int(os.getenv("STORM_RE_MITIGATION_THRESHOLD", "25")),
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
        "pingInterval": settings.get("pingInterval", 60),
        "pingTimeoutMs": settings.get("pingTimeoutMs", 1000),
        "pingRetries": settings.get("pingRetries", 3),
        "pingFailureConfirmationScans": int(
            settings.get(
                "pingFailureConfirmationScans",
                DEFAULT_SETTINGS["pingFailureConfirmationScans"],
            )
        ),
        "pingConcurrency": int(
            settings.get("pingConcurrency", DEFAULT_SETTINGS["pingConcurrency"])
        ),
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
        "reMitigationThreshold": int(settings.get("reMitigationThreshold", 25)),
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

    if (
        "pingFailureConfirmationScans" in payload
        and payload["pingFailureConfirmationScans"] is not None
    ):
        value = int(payload["pingFailureConfirmationScans"])
        if value < 1:
            raise ValueError("pingFailureConfirmationScans must be at least 1")
        update["pingFailureConfirmationScans"] = value

    if "pingConcurrency" in payload and payload["pingConcurrency"] is not None:
        value = int(payload["pingConcurrency"])
        if value < 1 or value > 64:
            raise ValueError("pingConcurrency must be between 1 and 64")
        update["pingConcurrency"] = value

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


def get_failure_confirmation_scans() -> int:
    """Complete failed scans required before Online → Not Reachable / Offline (Critical)."""
    settings = get_settings()
    raw = settings.get(
        "pingFailureConfirmationScans",
        DEFAULT_SETTINGS["pingFailureConfirmationScans"],
    )
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        return int(DEFAULT_SETTINGS["pingFailureConfirmationScans"])


def get_monitor_ping_concurrency() -> int:
    """
    Bounded parallelism for automatic monitoring scans.

    Cap at 64 to avoid unbounded socket/load spikes. Missing Mongo field falls
    back to DEFAULT_SETTINGS / MONITOR_PING_CONCURRENCY env.
    """
    settings = get_settings()
    raw = settings.get("pingConcurrency", DEFAULT_SETTINGS["pingConcurrency"])
    try:
        return max(1, min(int(raw), 64))
    except (TypeError, ValueError):
        return max(1, min(int(DEFAULT_SETTINGS["pingConcurrency"]), 64))


def get_monitor_runtime_mode() -> str:
    """
    Ping monitoring runtime mode.

    ``dispatch`` (default): ``nextCheckAt`` + atomic claim + bounded workers.
    ``legacy``: APScheduler wave / ``monitor_all_devices`` (compat / rollback).

    Env: MONITOR_RUNTIME_MODE. Unknown values fall back to ``dispatch``.
    """
    raw = (os.getenv("MONITOR_RUNTIME_MODE") or "dispatch").strip().lower()
    if raw in ("legacy", "dispatch"):
        return raw
    return "dispatch"


def get_monitor_dispatcher_interval_seconds() -> int:
    """
    APScheduler period for the dispatch-mode monitor job.

    Independent of ``pingInterval``. Env: MONITOR_DISPATCHER_INTERVAL_SECONDS.
    Default 5, clamped to 1–15.
    """
    raw = os.getenv("MONITOR_DISPATCHER_INTERVAL_SECONDS", "5")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 5
    return max(1, min(value, 15))


def get_ping_config(device=None):
    """
    Resolve ping interval / timeout / retries for a device (or globals).

    ``interval`` (from ``pingInterval``) is the per-device cadence used when
    advancing ``nextCheckAt`` at claim time in dispatch mode. It is **not** the
    APScheduler dispatcher period — that is ``get_monitor_dispatcher_interval_seconds``.
    """
    settings = get_settings()
    interval = settings.get("pingInterval", 60)
    timeout_ms = settings.get("pingTimeoutMs", 1000)
    retries = settings.get("pingRetries", 3)
    confirmation_scans = settings.get(
        "pingFailureConfirmationScans",
        DEFAULT_SETTINGS["pingFailureConfirmationScans"],
    )

    if device:
        if device.get("pingInterval") is not None:
            interval = int(device["pingInterval"])
        if device.get("pingTimeoutMs") is not None:
            timeout_ms = int(device["pingTimeoutMs"])
        if device.get("pingRetries") is not None:
            retries = int(device["pingRetries"])

    try:
        confirmation_scans = max(int(confirmation_scans), 1)
    except (TypeError, ValueError):
        confirmation_scans = int(DEFAULT_SETTINGS["pingFailureConfirmationScans"])

    return {
        "interval": interval,
        "timeout_ms": timeout_ms,
        "retries": retries,
        "failure_confirmation_scans": confirmation_scans,
    }
