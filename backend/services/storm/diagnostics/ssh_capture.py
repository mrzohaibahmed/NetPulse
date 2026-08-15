"""
Read-only SSH capture for Storm diagnostics.

NEVER enters configuration mode.
NEVER executes shutdown / no shutdown / write / copy running-config.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.diagnostics")

_FORBIDDEN = re.compile(
    r"\b("
    r"configure|conf\s+t|config\s+t|"
    r"shutdown|no\s+shutdown|"
    r"write(\s+memory)?|copy\s+running|"
    r"reload|erase|format"
    r")\b",
    re.IGNORECASE,
)

_SHOW_ONLY = re.compile(r"^\s*(show|display)\s+", re.IGNORECASE)


class DiagnosticsSSHError(Exception):
    """Raised when a read-only diagnostics SSH operation fails."""


def assert_read_only_command(command: str) -> str:
    """Validate that a command is a safe show/display command."""
    text = (command or "").strip()
    if not text:
        raise DiagnosticsSSHError("Empty SSH command")
    if _FORBIDDEN.search(text):
        raise DiagnosticsSSHError(f"Blocked non-read-only command: {text}")
    if not _SHOW_ONLY.match(text):
        raise DiagnosticsSSHError(
            f"Only show/display commands are allowed: {text}"
        )
    return text


def build_interface_commands(interface: str, vendor: str = "cisco_ios") -> dict[str, str]:
    """Vendor-aware read-only command set for one interface."""
    from utils.ssh_security import assert_safe_interface_name  # noqa: PLC0415

    name = assert_safe_interface_name(interface)
    key = (vendor or "cisco_ios").lower()
    if key in ("juniper", "junos"):
        return {
            "interface": f"show interfaces {name}",
            "switchport": f"show ethernet-switching interface {name}",
            "mac": f"show ethernet-switching table interface {name}",
            "cpu": "show chassis routing-engine",
            "uptime": "show system uptime",
        }
    return {
        "interface": f"show interfaces {name}",
        "switchport": f"show interfaces {name} switchport",
        "mac": f"show mac address-table interface {name}",
        "cpu": "show processes cpu | include CPU",
        "uptime": "show version | include uptime",
    }


def capture_show_outputs(
    device: dict,
    interface: str,
    *,
    collector=None,
) -> dict[str, Any]:
    """
    Open a read-only SSH session and capture diagnostics show outputs.

    Returns dict with keys: success, outputs, errors, vendor
    """
    from services.interface_collection.ssh_collector import (  # noqa: PLC0415
        SSHCollectorError,
        SSHInterfaceCollector,
        resolve_ssh_credentials,
    )

    outputs: dict[str, Optional[str]] = {}
    errors: dict[str, str] = {}
    creds = resolve_ssh_credentials(device)
    commands = build_interface_commands(interface, creds.vendor)

    owned = collector is None
    try:
        if collector is None:
            collector = SSHInterfaceCollector(creds)
            collector.connect()

        for key, command in commands.items():
            try:
                safe_cmd = assert_read_only_command(command)
                raw = collector.run_command(safe_cmd)
                outputs[key] = raw
            except Exception as exc:  # noqa: BLE001
                outputs[key] = None
                errors[key] = str(exc)
                logger.warning(
                    "Diagnostics SSH soft-fail | %s | %s | %s",
                    interface,
                    key,
                    exc,
                )

        return {
            "success": bool(outputs.get("interface")),
            "outputs": outputs,
            "errors": errors,
            "vendor": creds.vendor,
        }
    except SSHCollectorError as exc:
        logger.error("Diagnostics SSH failed | %s | %s", interface, exc)
        return {
            "success": False,
            "outputs": outputs,
            "errors": {"connection": str(exc), **errors},
            "vendor": getattr(creds, "vendor", None),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("Diagnostics SSH unexpected | %s | %s", interface, exc)
        return {
            "success": False,
            "outputs": outputs,
            "errors": {"connection": str(exc), **errors},
            "vendor": getattr(creds, "vendor", None),
        }
    finally:
        if owned and collector is not None:
            try:
                collector.close()
            except Exception:  # noqa: BLE001
                pass
