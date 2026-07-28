"""
Safe SSH command execution for Mitigation strategies.
Validates commands against templates and checks outputs for error signatures.
"""

from __future__ import annotations

import re
from typing import Any

from services.interface_collection.ssh_collector import (
    SSHCollectorError,
    SSHInterfaceCollector,
    resolve_ssh_credentials,
)
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.mitigation.ssh")


def assert_safe_mitigation_command(command: str, interface: str) -> None:
    """Validate that the command matches an approved static command template."""
    # Strict regex check for interface safety to prevent injection
    if not re.match(r"^[a-zA-Z0-9/.:-]+$", interface):
        raise ValueError(f"Command rejected: Invalid interface name '{interface}'")

    cmd = (command or "").strip()
    if not cmd:
        raise ValueError("Command rejected: Empty command string")

    # Approved template patterns
    allowed_patterns = [
        r"^configure terminal$",
        r"^configure$",
        rf"^interface\s+{re.escape(interface)}$",
        r"^shutdown$",
        r"^no shutdown$",
        r"^end$",
        rf"^show running-config interface\s+{re.escape(interface)}$",
        rf"^set interfaces\s+{re.escape(interface)}\s+disable$",
        rf"^delete interfaces\s+{re.escape(interface)}\s+disable$",
        r"^commit$",
        r"^exit$",
        rf"^show configuration interfaces\s+{re.escape(interface)}$",
    ]

    # Verify that the command exactly matches one of the patterns (case insensitive)
    if not any(re.match(pat, cmd, re.IGNORECASE) for pat in allowed_patterns):
        raise ValueError(
            f"Mitigation command safety violation: Command '{cmd}' "
            f"on interface '{interface}' does not match any approved template."
        )


def check_for_errors(output: str) -> None:
    """Scan CLI output for config rejection or command error signatures."""
    text = (output or "").lower()
    errors = [
        "% invalid input detected",
        "% incomplete command",
        "% ambiguous command",
        "% command rejected",
        "syntax error",
        "error:",
        "failed",
        "invalid command",
    ]
    for err in errors:
        if err in text:
            raise ValueError(f"CLI configuration command rejected: {output.strip()}")


class SSHMitigationExecutor:
    """Context-managed SSH configuration and execution wrapper."""

    def __init__(self, device: dict):
        self.device = device
        self.creds = resolve_ssh_credentials(device)
        self.collector: SSHInterfaceCollector | None = None

    def __enter__(self) -> SSHMitigationExecutor:
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def connect(self) -> None:
        try:
            self.collector = SSHInterfaceCollector(self.creds)
            self.collector.connect()
        except SSHCollectorError as exc:
            raise RuntimeError(f"SSH reachability check failed: {exc}") from exc

    def close(self) -> None:
        if self.collector:
            try:
                self.collector.close()
            except Exception:
                pass
            self.collector = None

    def execute_commands(self, commands: list[str], interface: str) -> list[str]:
        """Execute whitelisted configuration command list and assert no errors."""
        if not self.collector:
            raise RuntimeError("SSH executor is not connected")

        results = []
        for cmd in commands:
            assert_safe_mitigation_command(cmd, interface)
            try:
                logger.info(
                    "Executing mitigation command | host=%s | %s",
                    self.creds.host,
                    cmd,
                )
                output = self.collector.run_command(cmd)
                check_for_errors(output)
                results.append(output)
            except Exception as exc:
                raise RuntimeError(
                    f"Command execution failed on '{cmd}': {exc}"
                ) from exc
        return results
