from utils.secret_crypto import encrypt_secret
from utils.utc import utc_now


def create_device(
    hostname,
    ip_address,
    device_type,
    critical=False,
    monitor=True,
    ping_interval=None,
    ping_timeout_ms=None,
    ping_retries=None,
    credentials=None,
):
    now = utc_now()

    document = {
        "hostname": hostname,
        "ipAddress": ip_address,
        "deviceType": device_type,
        "critical": critical,
        "monitor": monitor,
        "status": "Unknown",
        "responseTime": None,
        "lastSeen": None,
        "lastCheckedAt": None,
        "consecutiveFailures": 0,
        "pingInterval": ping_interval,
        "pingTimeoutMs": ping_timeout_ms,
        "pingRetries": ping_retries,
        "createdAt": now,
        "updatedAt": now,
    }
    # New monitored devices are due immediately for a first check; the
    # dispatcher / claim path then advances nextCheckAt by pingInterval.
    if monitor:
        document["nextCheckAt"] = now

    if credentials:
        document["credentials"] = credentials

    return document


def normalize_device_credentials(raw, existing=None) -> dict | None:
    """
    Validate and normalise an optional SSH credentials payload.

    Accepted keys: sshUsername, sshPassword, sshPort, sshSecret, sshVendor.
    When ``existing`` is provided, omitted secret fields are preserved so a
    partial update (e.g. username only) does not wipe the password.

    Returns None when the result would be empty.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("credentials must be an object")

    credentials = dict(existing or {})

    if "sshUsername" in raw and raw["sshUsername"] is not None:
        credentials["sshUsername"] = str(raw["sshUsername"]).strip()

    if "sshPassword" in raw and raw["sshPassword"] not in (None, ""):
        credentials["sshPassword"] = encrypt_secret(str(raw["sshPassword"]))

    if "sshSecret" in raw and raw["sshSecret"] not in (None, ""):
        credentials["sshSecret"] = encrypt_secret(str(raw["sshSecret"]))

    if "sshVendor" in raw and raw["sshVendor"] is not None:
        credentials["sshVendor"] = str(raw["sshVendor"]).strip()

    if "sshPort" in raw and raw["sshPort"] not in ("", None):
        try:
            port = int(raw["sshPort"])
        except (TypeError, ValueError) as exc:
            raise ValueError("sshPort must be an integer") from exc
        if not (1 <= port <= 65535):
            raise ValueError("sshPort must be between 1 and 65535")
        credentials["sshPort"] = port

    if "snmpCommunity" in raw and raw["snmpCommunity"] is not None:
        credentials["snmpCommunity"] = str(raw["snmpCommunity"]).strip()

    if "snmpVersion" in raw and raw["snmpVersion"] is not None:
        credentials["snmpVersion"] = str(raw["snmpVersion"]).strip().lower()

    if "snmpPort" in raw and raw["snmpPort"] not in ("", None):
        try:
            snmp_port = int(raw["snmpPort"])
        except (TypeError, ValueError) as exc:
            raise ValueError("snmpPort must be an integer") from exc
        if not (1 <= snmp_port <= 65535):
            raise ValueError("snmpPort must be between 1 and 65535")
        credentials["snmpPort"] = snmp_port

    if "snmpTimeout" in raw and raw["snmpTimeout"] not in ("", None):
        try:
            credentials["snmpTimeout"] = float(raw["snmpTimeout"])
        except (TypeError, ValueError) as exc:
            raise ValueError("snmpTimeout must be a number") from exc

    return credentials or None
