"""Cisco CLI output parsers for switch hardware monitoring."""

from services.switch_hardware.parsers.cisco_ios import parse_cisco_ios_outputs
from services.switch_hardware.parsers.cisco_nxos import parse_cisco_nxos_outputs

__all__ = ["parse_cisco_ios_outputs", "parse_cisco_nxos_outputs"]


def parse_platform_outputs(platform: str, outputs: dict[str, str | None]) -> dict:
    if platform == "cisco_nxos":
        return parse_cisco_nxos_outputs(outputs)
    return parse_cisco_ios_outputs(outputs)
