"""
Parser for Redfish PowerState and Chassis Power telemetry resources (Phase 3B).
"""

from __future__ import annotations

from typing import Any

from services.server_hardware.models import PowerSupply, ServerPowerSummary
from services.server_hardware.parsers.utils import (
    clean_float,
    clean_string,
    extract_component_id,
    logger,
    parse_status,
)


def parse_power_summary(
    system_data: dict[str, Any] | None,
    power_chassis_data: dict[str, Any] | None = None,
) -> ServerPowerSummary:
    """
    Parse ComputerSystem PowerState and optional Chassis Power telemetry into ServerPowerSummary.
    """
    power_state: str | None = None
    if system_data and isinstance(system_data, dict):
        power_state = clean_string(system_data.get("PowerState"))

    power_consumed_watts: float | None = None
    power_capacity_watts: float | None = None
    power_supplies: list[PowerSupply] = []

    if power_chassis_data and isinstance(power_chassis_data, dict):
        # Extract PowerControl aggregate reading if present
        power_controls = power_chassis_data.get("PowerControl")
        if isinstance(power_controls, list) and power_controls:
            ctrl = power_controls[0]
            if isinstance(ctrl, dict):
                power_consumed_watts = clean_float(ctrl.get("PowerConsumedWatts"))
                power_capacity_watts = clean_float(ctrl.get("PowerCapacityWatts"))

        # Extract PowerSupplies list
        raw_supplies = power_chassis_data.get("PowerSupplies")
        if isinstance(raw_supplies, list):
            for idx, ps_raw in enumerate(raw_supplies):
                if not isinstance(ps_raw, dict):
                    logger.warning("Skipping malformed PowerSupply entry at index %d", idx)
                    continue

                ps_id = extract_component_id(ps_raw, f"ps-{idx + 1}")
                if not ps_id:
                    logger.warning("Skipping PowerSupply missing identifier at index %d", idx)
                    continue

                ps_obj = PowerSupply(
                    id=ps_id,
                    name=clean_string(ps_raw.get("Name")),
                    model=clean_string(ps_raw.get("Model")),
                    power_capacity_watts=clean_float(ps_raw.get("PowerCapacityWatts")),
                    last_power_output_watts=clean_float(ps_raw.get("LastPowerOutputWatts")),
                    line_input_voltage=clean_float(ps_raw.get("LineInputVoltage")),
                    status=parse_status(ps_raw),
                )
                power_supplies.append(ps_obj)

    return ServerPowerSummary(
        power_state=power_state,
        power_consumed_watts=power_consumed_watts,
        power_capacity_watts=power_capacity_watts,
        power_supplies=power_supplies,
    )
