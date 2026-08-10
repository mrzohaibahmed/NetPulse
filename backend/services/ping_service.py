from ping3 import ping

from services.settings_service import get_ping_config
from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

STATUS_ONLINE = "Online"
STATUS_NOT_REACHABLE = "Not Reachable"
STATUS_OFFLINE_CRITICAL = "Offline (Critical)"

logger = get_monitor_logger("ping")


def classify_failure_status(critical=False):
    if critical:
        return STATUS_OFFLINE_CRITICAL
    return STATUS_NOT_REACHABLE


def _format_raw_ping_result(response) -> str:
    if response is None:
        return "None"
    if response is False:
        return "False"
    if isinstance(response, (int, float)):
        return f"rtt_s={response}"
    return f"invalid:{type(response).__name__}"


def ping_device(ip_address, critical=False, timeout_ms=None, retries=None, device=None):
    """
    ICMP ping with configurable timeout and retries (FR2.2, FR2.3).
    Classifies failures as Not Reachable or Offline (Critical) (FR3.1–FR3.3).
    lastSeen is only set on success (FR3.4) — always timezone-aware UTC.

    ``pingRetries`` / retries is the TOTAL number of ICMP attempts per scan
    (not additional retries after a first attempt).
    """
    config = get_ping_config(device)
    timeout_ms = int(timeout_ms if timeout_ms is not None else config["timeout_ms"])
    retries = int(retries if retries is not None else config["retries"])
    timeout_s = max(timeout_ms, 100) / 1000.0
    attempts = max(retries, 1)

    ping_started_at = utc_now()
    last_error = "Device is unreachable"
    hostname = (device or {}).get("hostname", "unknown")

    for attempt_num in range(1, attempts + 1):
        try:
            response = ping(ip_address, timeout=timeout_s)
            raw = _format_raw_ping_result(response)
            logger.info(
                "ICMP attempt | hostname=%s | ip=%s | attempt=%s/%s | "
                "timeoutMs=%s | result=%s",
                hostname,
                ip_address,
                attempt_num,
                attempts,
                timeout_ms,
                raw,
            )

            if response is None or response is False or not isinstance(response, (int, float)):
                last_error = "Device is unreachable"
                continue

            response_time = round(response * 1000, 2)
            completed = utc_now()
            logger.info(
                "ICMP scan final | hostname=%s | ip=%s | final=%s | rttMs=%s | "
                "attempts=%s | timeoutMs=%s | pingStartedAt=%s | pingCompletedAt=%s",
                hostname,
                ip_address,
                STATUS_ONLINE,
                response_time,
                attempts,
                timeout_ms,
                ping_started_at.isoformat(),
                completed.isoformat(),
            )
            return {
                "success": True,
                "status": STATUS_ONLINE,
                "responseTime": response_time,
                "lastSeen": completed,
                "message": "Device is reachable",
                "attempts": attempts,
                "timeoutMs": timeout_ms,
                "pingStartedAt": ping_started_at,
                "pingCompletedAt": completed,
            }
        except Exception as error:
            last_error = str(error)
            logger.info(
                "ICMP attempt | hostname=%s | ip=%s | attempt=%s/%s | "
                "timeoutMs=%s | result=exception | exceptionType=%s | error=%s",
                hostname,
                ip_address,
                attempt_num,
                attempts,
                timeout_ms,
                type(error).__name__,
                last_error,
            )

    completed = utc_now()
    failure_status = classify_failure_status(critical)
    logger.info(
        "ICMP scan final | hostname=%s | ip=%s | final=%s | rttMs=None | "
        "attempts=%s | timeoutMs=%s | message=%s | pingStartedAt=%s | "
        "pingCompletedAt=%s",
        hostname,
        ip_address,
        failure_status,
        attempts,
        timeout_ms,
        last_error,
        ping_started_at.isoformat(),
        completed.isoformat(),
    )
    return {
        "success": False,
        "status": failure_status,
        "responseTime": None,
        "lastSeen": None,
        "message": last_error,
        "attempts": attempts,
        "timeoutMs": timeout_ms,
        "pingStartedAt": ping_started_at,
        "pingCompletedAt": completed,
    }
