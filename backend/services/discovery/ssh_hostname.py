"""
Optional SSH hostname lookup for discovery classification (priority 4).

Only runs as a final fallback when no hostname was resolved from Nmap,
reverse DNS, or an existing device record. Failures are silent so
discovery/nmap flows never break.
"""

from __future__ import annotations

import re
from dataclasses import replace

from services.discovery.classifier import (
    evidence_from_network_info,
    is_unknown_hostname,
)
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("discovery")

_HOSTNAME_RE = re.compile(
    r"^(?:hostname|sysname)\s+(\S+)",
    re.IGNORECASE | re.MULTILINE,
)
_PROMPT_HOSTNAME_RE = re.compile(
    r"^(\S+?)(?:\((?:config[^)]*)\))?[#>]\s*$",
    re.MULTILINE,
)


def _device_label(device: dict | None, ip_address: str = "") -> str:
    if device:
        hostname = device.get("hostname")
        ip = ip_address or device.get("ipAddress") or ""
        if hostname and str(hostname).strip():
            return f"{hostname}/{ip}" if ip else str(hostname)
        if ip:
            return ip
    return ip_address or "unknown"


def has_ssh_credentials(device: dict | None) -> bool:
    if not device:
        return False
    creds = device.get("credentials") or {}
    return bool(creds.get("sshUsername") and creds.get("sshPassword"))


def is_ssh_reachable(network_info: dict | None) -> bool:
    """True when Nmap reported SSH port 22 open or an ssh service."""
    info = network_info or {}
    for port in info.get("ports") or []:
        if str(port.get("state") or "").lower() != "open":
            continue
        try:
            if int(port.get("port")) == 22:
                return True
        except (TypeError, ValueError):
            continue
    services = {str(s).lower() for s in (info.get("services") or []) if s}
    return "ssh" in services


def nmap_hostname_from_network_info(
    network_info: dict | None,
) -> tuple[str, str]:
    """Return (ptr_hostname, service_hostname) extracted from Nmap results."""
    evidence = evidence_from_network_info(network_info or {})
    ptr = evidence.hostname_ptr if not is_unknown_hostname(evidence.hostname_ptr) else ""
    service = (
        evidence.hostname_service
        if not is_unknown_hostname(evidence.hostname_service)
        else ""
    )
    return ptr, service


def should_attempt_ssh_hostname(
    device: dict | None,
    network_info: dict | None,
    *,
    ip_address: str = "",
    reverse_dns_hostname: str = "",
) -> bool:
    """
    SSH is only needed when credentials exist, SSH is reachable, and no valid
    hostname was obtained from Nmap, reverse DNS, or the stored device record.
    """
    if not has_ssh_credentials(device):
        return False
    if not is_ssh_reachable(network_info):
        return False

    ptr, service = nmap_hostname_from_network_info(network_info)
    if ptr or service:
        return False

    rdns = (reverse_dns_hostname or "").strip()
    if rdns and not is_unknown_hostname(rdns):
        return False

    existing = (device or {}).get("hostname") or ""
    if existing and not is_unknown_hostname(existing):
        return False

    return True


def log_ssh_hostname_skip(device: dict | None, *, ip_address: str = "") -> None:
    logger.info(
        "[SSH HOSTNAME] reason=existing_hostname | device=%s",
        _device_label(device, ip_address),
    )


def log_ssh_hostname_fallback(device: dict | None, *, ip_address: str = "") -> None:
    logger.info(
        "[SSH HOSTNAME] reason=fallback | device=%s",
        _device_label(device, ip_address),
    )


def fetch_ssh_hostname(device: dict | None, timeout: float = 8.0) -> str:
    """
    Best-effort SSH hostname retrieval.

    Returns empty string when credentials are missing or SSH fails.
    """
    if not device or not has_ssh_credentials(device):
        return ""

    try:
        from services.interface_collection.ssh_collector import (  # noqa: PLC0415
            SSHCollectorError,
            SSHInterfaceCollector,
            resolve_ssh_credentials,
        )
    except Exception:  # noqa: BLE001
        return ""

    client = None
    try:
        credentials = replace(
            resolve_ssh_credentials(device),
            timeout=max(3, int(timeout)),
        )

        collector = SSHInterfaceCollector(credentials)
        collector.connect()
        client = collector

        # Prefer a lightweight show command over full interface discovery.
        commands = (
            "show running-config | include ^hostname",
            "show hostname",
            "hostname",
        )
        output = ""
        for command in commands:
            try:
                output = collector.run_command(command)
            except Exception:  # noqa: BLE001
                continue
            if output and output.strip():
                break

        hostname = _parse_hostname(output or "")
        if hostname:
            logger.info(
                "[SSH HOSTNAME] resolved | host=%s hostname=%s",
                credentials.host,
                hostname,
            )
            return hostname
        return ""
    except Exception as exc:  # noqa: BLE001
        # Includes SSHCollectorError and network failures.
        logger.debug(
            "[DISCOVERY] SSH hostname lookup skipped | ip=%s | %s",
            (device or {}).get("ipAddress"),
            exc,
        )
        return ""
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass


def _parse_hostname(output: str) -> str:
    text = (output or "").strip()
    if not text:
        return ""

    match = _HOSTNAME_RE.search(text)
    if match:
        candidate = match.group(1).strip().strip('"').strip("'")
        if candidate and candidate.lower() not in {"hostname", "sysname"}:
            return candidate

    # Some devices echo only the hostname token.
    for line in text.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("show ", "#", "!")):
            continue
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}", line):
            return line

    prompt = _PROMPT_HOSTNAME_RE.search(text)
    if prompt:
        return prompt.group(1).strip()

    return ""
