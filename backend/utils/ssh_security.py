"""
SSH host-key policy and shared interface-name validation.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import paramiko

from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("ssh.security")

# Cisco/Juniper-style interface names: GigabitEthernet1/0/1, Gi1/0/1, et-0/0/1, etc.
INTERFACE_NAME_RE = re.compile(r"^[a-zA-Z0-9/.:_-]+$")


def assert_safe_interface_name(interface: str) -> str:
    """
    Reject interface names that could inject extra CLI lines or shell metacharacters.

    Raises ValueError on rejection.
    """
    name = (interface or "").strip()
    if not name:
        raise ValueError("Interface name is required")
    if len(name) > 128:
        raise ValueError("Interface name is too long")
    if any(ch in name for ch in ("\n", "\r", ";", "&", "|", "`", "$", "'", '"', "\\")):
        raise ValueError("Interface name contains forbidden characters")
    if not INTERFACE_NAME_RE.match(name):
        raise ValueError("Interface name contains invalid characters")
    return name


def _flask_debug_enabled() -> bool:
    return os.getenv("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")


def resolve_known_hosts_path() -> Path | None:
    """
    Return path to known_hosts file when configured.

    Env: SSH_KNOWN_HOSTS_FILE — absolute or relative to backend/.
    """
    raw = (os.getenv("SSH_KNOWN_HOSTS_FILE") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        backend_root = Path(__file__).resolve().parent.parent
        path = backend_root / path
    return path


def apply_host_key_policy(client: paramiko.SSHClient) -> str:
    """
    Apply secure host-key verification to a Paramiko client.

    Production / non-debug:
      - Load SSH_KNOWN_HOSTS_FILE when set
      - RejectPolicy for unknown / changed keys

    Debug only (FLASK_DEBUG=true) and SSH_ALLOW_UNKNOWN_HOSTS=true:
      - AutoAddPolicy for local lab convenience

    Returns the policy name applied (for tests / logging).
    """
    allow_unknown = (os.getenv("SSH_ALLOW_UNKNOWN_HOSTS") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if _flask_debug_enabled() and allow_unknown:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        logger.warning(
            "SSH AutoAddPolicy enabled (FLASK_DEBUG + SSH_ALLOW_UNKNOWN_HOSTS) — "
            "not for production"
        )
        return "AutoAddPolicy"

    known_hosts = resolve_known_hosts_path()
    if known_hosts is not None:
        if known_hosts.is_file():
            client.load_host_keys(str(known_hosts))
            logger.info("SSH known_hosts loaded | path=%s", known_hosts)
        else:
            logger.warning(
                "SSH_KNOWN_HOSTS_FILE set but file missing | path=%s — "
                "unknown hosts will be rejected",
                known_hosts,
            )

    # Also load system / user known_hosts when present (best-effort).
    try:
        client.load_system_host_keys()
    except Exception:  # noqa: BLE001
        pass

    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    return "RejectPolicy"
