"""
Parser for Redfish Processor resources (Phase 3B).
"""

from __future__ import annotations

from typing import Any

from services.server_hardware.models import ServerProcessor
from services.server_hardware.parsers.utils import (
    clean_int,
    clean_string,
    extract_component_id,
    logger,
    parse_status,
)


def parse_processors(
    processors_data: dict[str, Any] | list[Any] | None,
) -> list[ServerProcessor]:
    """
    Parse Processors collection or member payloads into list[ServerProcessor].

    Skipping malformed individual processors while keeping valid items.
    """
    if not processors_data:
        return []

    raw_items: list[Any] = []
    if isinstance(processors_data, list):
        raw_items = processors_data
    elif isinstance(processors_data, dict):
        if "Members" in processors_data and isinstance(processors_data["Members"], list):
            raw_items = processors_data["Members"]
        else:
            # Single processor dict passed directly
            raw_items = [processors_data]

    processors: list[ServerProcessor] = []
    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            logger.warning("Skipping malformed processor entry at index %d", idx)
            continue

        proc_id = extract_component_id(item, f"cpu-{idx + 1}")
        if not proc_id:
            logger.warning("Skipping processor entry missing identifier at index %d", idx)
            continue

        proc_obj = ServerProcessor(
            id=proc_id,
            name=clean_string(item.get("Name")),
            manufacturer=clean_string(item.get("Manufacturer")),
            model=clean_string(item.get("Model")),
            cores=clean_int(item.get("TotalCores")),
            threads=clean_int(item.get("TotalThreads")),
            speed_mhz=clean_int(item.get("MaxSpeedMHz")),
            status=parse_status(item),
        )
        processors.append(proc_obj)

    return processors
