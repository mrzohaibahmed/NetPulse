"""
Domain parsers for Redfish hardware endpoints (Phase 3B).
"""

from services.server_hardware.parsers.firmware import parse_firmware
from services.server_hardware.parsers.memory import parse_memory
from services.server_hardware.parsers.network import parse_network_interfaces
from services.server_hardware.parsers.power import parse_power_summary
from services.server_hardware.parsers.processor import parse_processors
from services.server_hardware.parsers.storage import parse_storage
from services.server_hardware.parsers.system import parse_system_identity
from services.server_hardware.parsers.thermal import parse_thermals
from services.server_hardware.parsers.utils import RedfishParserError, parse_status

__all__ = [
    "RedfishParserError",
    "parse_firmware",
    "parse_memory",
    "parse_network_interfaces",
    "parse_power_summary",
    "parse_processors",
    "parse_status",
    "parse_storage",
    "parse_system_identity",
    "parse_thermals",
]
