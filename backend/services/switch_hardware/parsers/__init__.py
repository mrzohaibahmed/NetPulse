"""CLI output parsers for switch hardware monitoring."""

from services.switch_hardware.parsers.cisco_ios import parse_cisco_ios_outputs
from services.switch_hardware.parsers.cisco_nxos import parse_cisco_nxos_outputs
from services.switch_hardware.parsers.juniper_junos import parse_juniper_junos_outputs

__all__ = ["parse_cisco_ios_outputs", "parse_cisco_nxos_outputs", "parse_juniper_junos_outputs"]


def parse_platform_outputs(platform: str, outputs: dict[str, str | None]) -> dict:
    if platform == "juniper_junos":
        return parse_juniper_junos_outputs(outputs)
    if platform == "cisco_nxos":
        return parse_cisco_nxos_outputs(outputs)
    return parse_cisco_ios_outputs(outputs)
