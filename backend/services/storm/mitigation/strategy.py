"""
Mitigation Strategy Interface and Implementations.
Supports Cisco and Juniper command sets.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod


class MitigationStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the strategy (e.g. SHUTDOWN, NO_SHUTDOWN)."""
        pass

    @abstractmethod
    def get_commands(self, interface: str, vendor: str) -> list[str]:
        """Get command sequence to execute this strategy."""
        pass

    @abstractmethod
    def get_verification_commands(self, interface: str, vendor: str) -> list[str]:
        """Get command sequence to verify execution."""
        pass

    @abstractmethod
    def verify_output(self, output: str, vendor: str) -> bool:
        """Verify output of the verification commands."""
        pass

    @abstractmethod
    def get_rollback_commands(self, interface: str, vendor: str) -> list[str]:
        """Get commands to rollback this strategy."""
        pass


class ShutdownInterfaceStrategy(MitigationStrategy):
    @property
    def name(self) -> str:
        return "SHUTDOWN"

    def get_commands(self, interface: str, vendor: str) -> list[str]:
        v = (vendor or "").lower()
        if "juniper" in v or "junos" in v:
            return [
                "configure",
                f"set interfaces {interface} disable",
                "commit",
                "exit",
            ]
        # Cisco IOS / XE / NXOS or generic
        return [
            "configure terminal",
            f"interface {interface}",
            "shutdown",
            "end",
        ]

    def get_verification_commands(self, interface: str, vendor: str) -> list[str]:
        v = (vendor or "").lower()
        if "juniper" in v or "junos" in v:
            return [f"show configuration interfaces {interface}"]
        return [f"show running-config interface {interface}"]

    def verify_output(self, output: str, vendor: str) -> bool:
        text = output or ""
        v = (vendor or "").lower()
        if "juniper" in v or "junos" in v:
            # Expect "disable;" under configuration
            return "disable" in text
        # Cisco: expect "shutdown" on a line by itself (with optional leading space)
        return bool(re.search(r"^\s*shutdown\b", text, re.MULTILINE | re.IGNORECASE))

    def get_rollback_commands(self, interface: str, vendor: str) -> list[str]:
        v = (vendor or "").lower()
        if "juniper" in v or "junos" in v:
            return [
                "configure",
                f"delete interfaces {interface} disable",
                "commit",
                "exit",
            ]
        return [
            "configure terminal",
            f"interface {interface}",
            "no shutdown",
            "end",
        ]


class NoShutdownRecoveryStrategy(MitigationStrategy):
    @property
    def name(self) -> str:
        return "NO_SHUTDOWN"

    def get_commands(self, interface: str, vendor: str) -> list[str]:
        v = (vendor or "").lower()
        if "juniper" in v or "junos" in v:
            return [
                "configure",
                f"delete interfaces {interface} disable",
                "commit",
                "exit",
            ]
        # Cisco IOS / XE / NXOS or generic
        return [
            "configure terminal",
            f"interface {interface}",
            "no shutdown",
            "end",
        ]

    def get_verification_commands(self, interface: str, vendor: str) -> list[str]:
        v = (vendor or "").lower()
        if "juniper" in v or "junos" in v:
            return [f"show configuration interfaces {interface}"]
        return [f"show running-config interface {interface}"]

    def verify_output(self, output: str, vendor: str) -> bool:
        text = output or ""
        v = (vendor or "").lower()
        if "juniper" in v or "junos" in v:
            # Expect "disable" NOT to be in output
            return "disable" not in text
        # Cisco: expect "shutdown" NOT to be on any line
        return not bool(re.search(r"^\s*shutdown\b", text, re.MULTILINE | re.IGNORECASE))

    def get_rollback_commands(self, interface: str, vendor: str) -> list[str]:
        v = (vendor or "").lower()
        if "juniper" in v or "junos" in v:
            return [
                "configure",
                f"set interfaces {interface} disable",
                "commit",
                "exit",
            ]
        return [
            "configure terminal",
            f"interface {interface}",
            "shutdown",
            "end",
        ]
