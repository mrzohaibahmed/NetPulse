"""
Parser for Redfish ComputerSystem identity resources (Phase 3B).
"""

from __future__ import annotations

from typing import Any

from services.server_hardware.models import ServerIdentity
from services.server_hardware.parsers.utils import (
    RedfishParserError,
    clean_string,
)


def parse_system_identity(data: dict[str, Any] | None) -> ServerIdentity:
    """
    Parse ComputerSystem resource into ServerIdentity.

    Raises RedfishParserError if the payload is missing or not a dict.
    Missing fields become None.
    """
    if not data or not isinstance(data, dict):
        raise RedfishParserError("ComputerSystem payload is missing or malformed")

    hostname = clean_string(data.get("HostName"))
    manufacturer = clean_string(data.get("Manufacturer"))
    model = clean_string(data.get("Model"))
    serial_number = clean_string(data.get("SerialNumber"))
    uuid = clean_string(data.get("UUID"))
    asset_tag = clean_string(data.get("AssetTag"))

    return ServerIdentity(
        hostname=hostname,
        manufacturer=manufacturer,
        model=model,
        serial_number=serial_number,
        uuid=uuid,
        asset_tag=asset_tag,
    )
