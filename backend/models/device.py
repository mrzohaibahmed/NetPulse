from utils.secret_crypto import encrypt_secret
from utils.utc import utc_now


def create_device(
    hostname,
    ip_address=None,
    device_type="Unknown",
    critical=False,
    monitor=True,
    show_on_dashboard=False,
    ping_interval=None,
    ping_timeout_ms=None,
    ping_retries=None,
    credentials=None,
    location=None,
    ilo_address=None,
):
    """
    Build a new device document.

    ``ip_address`` may be omitted for eligible iLO-only server records.
    Unused address fields are omitted from the document (never stored as
    empty string or null). Without a usable OS IP, monitoring is forced off.
    """
    now = utc_now()

    if not ip_address:
        monitor = False

    document = {
        "hostname": hostname,
        "deviceType": device_type,
        "critical": critical,
        "monitor": monitor,
        "showOnDashboard": bool(show_on_dashboard),
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
    if ip_address:
        document["ipAddress"] = ip_address
    if ilo_address:
        document["iloAddress"] = ilo_address

    # New monitored devices are due immediately for a first check; the
    # dispatcher / claim path then advances nextCheckAt by pingInterval.
    if monitor:
        document["nextCheckAt"] = now

    if credentials:
        document["credentials"] = credentials

    if location:
        document["location"] = location

    return document


def normalize_device_credentials(raw, existing=None) -> dict | None:
    """
    Validate and normalise an optional credentials payload.

    Accepted keys: sshUsername, sshPassword, sshPort, sshSecret, sshVendor,
    snmpCommunity, snmpVersion, snmpPort, snmpTimeout,
    iloUsername, iloPassword, iloPort.

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
        community = str(raw["snmpCommunity"]).strip()
        if community:
            credentials["snmpCommunity"] = encrypt_secret(community)
        else:
            credentials["snmpCommunity"] = ""

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

    if "iloUsername" in raw and raw["iloUsername"] is not None:
        credentials["iloUsername"] = str(raw["iloUsername"]).strip()

    if "iloPassword" in raw and raw["iloPassword"] not in (None, ""):
        credentials["iloPassword"] = encrypt_secret(str(raw["iloPassword"]))

    if "iloPort" in raw and raw["iloPort"] not in ("", None):
        try:
            ilo_port = int(raw["iloPort"])
        except (TypeError, ValueError) as exc:
            raise ValueError("iloPort must be an integer") from exc
        if not (1 <= ilo_port <= 65535):
            raise ValueError("iloPort must be between 1 and 65535")
        credentials["iloPort"] = ilo_port

    return credentials or None
