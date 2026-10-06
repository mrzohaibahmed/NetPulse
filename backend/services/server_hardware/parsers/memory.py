"""
Parser for Redfish Memory / DIMM resources (Phase 3B).
"""

from __future__ import annotations

from typing import Any

from services.server_hardware.models import ServerMemoryDimm
from services.server_hardware.parsers.utils import (
    clean_int,
    clean_string,
    extract_component_id,
    logger,
    parse_status,
)


def parse_memory(
    memory_data: dict[str, Any] | list[Any] | None,
) -> list[ServerMemoryDimm]:
    """
    Parse Memory DIMM collection or member payloads into list[ServerMemoryDimm].

    Normalizes capacity to Bytes (converting MiB if reported in CapacityMiB).
    Skipping malformed individual DIMMs while keeping valid items.
    """
    if not memory_data:
        return []

    raw_items: list[Any] = []
    if isinstance(memory_data, list):
        raw_items = memory_data
    elif isinstance(memory_data, dict):
        if "Members" in memory_data and isinstance(memory_data["Members"], list):
            raw_items = memory_data["Members"]
        else:
            raw_items = [memory_data]

    dimms: list[ServerMemoryDimm] = []
    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            logger.warning("Skipping malformed memory entry at index %d", idx)
            continue

        dimm_id = extract_component_id(item, f"dimm-{idx + 1}")
        if not dimm_id:
            logger.warning("Skipping memory entry missing identifier at index %d", idx)
            continue

        # Capacity normalization: convert CapacityMiB -> Bytes, or fallback to CapacityBytes
        capacity_bytes: int | None = None
        cap_mib = clean_int(item.get("CapacityMiB"))
        if cap_mib is not None:
            capacity_bytes = cap_mib * 1024 * 1024
        else:
            capacity_bytes = clean_int(item.get("CapacityBytes"))

        speed_mhz = clean_int(item.get("OperatingSpeedMhz"))
        if speed_mhz is None:
            speed_mhz = clean_int(item.get("AllowedSpeedsMHz"))

        dimm_obj = ServerMemoryDimm(
            id=dimm_id,
            name=clean_string(item.get("Name")),
            capacity_bytes=capacity_bytes,
            speed_mhz=speed_mhz,
            manufacturer=clean_string(item.get("Manufacturer")),
            part_number=clean_string(item.get("PartNumber")),
            serial_number=clean_string(item.get("SerialNumber")),
            status=parse_status(item),
        )
        dimms.append(dimm_obj)

    return dimms
