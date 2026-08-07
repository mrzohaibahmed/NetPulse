"""
Optional SSH hostname lookup for discovery classification (priority 3).

Only runs when the device already has SSH credentials. Failures are silent
so discovery/nmap flows never break.
"""

from __future__ import annotations

import re
from dataclasses import replace

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


def fetch_ssh_hostname(device: dict | None, timeout: float = 8.0) -> str:
    """
    Best-effort SSH hostname retrieval.

    Returns empty string when credentials are missing or SSH fails.
    """
    if not device:
        return ""

    creds = device.get("credentials") or {}
    if not creds.get("sshUsername") or not creds.get("sshPassword"):
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
                "[DISCOVERY] SSH hostname | host=%s hostname=%s",
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
