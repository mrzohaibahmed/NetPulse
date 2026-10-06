"""
Parser for Redfish Thermal resources (Temperature sensors and Cooling Fans) (Phase 3B).
"""

from __future__ import annotations

from typing import Any

from services.server_hardware.models import CoolingFan, TemperatureSensor
from services.server_hardware.parsers.utils import (
    clean_float,
    clean_int,
    clean_string,
    extract_component_id,
    logger,
    parse_status,
)


def parse_thermals(
    thermal_data: dict[str, Any] | None,
) -> tuple[list[TemperatureSensor], list[CoolingFan]]:
    """
    Parse Chassis Thermal payload into (list[TemperatureSensor], list[CoolingFan]).

    Does not invent thresholds or guess missing unit readings.
    """
    temperatures: list[TemperatureSensor] = []
    fans: list[CoolingFan] = []

    if not thermal_data or not isinstance(thermal_data, dict):
        return temperatures, fans

    # 1. Parse Temperatures
    raw_temps = thermal_data.get("Temperatures")
    if isinstance(raw_temps, list):
        for idx, t_raw in enumerate(raw_temps):
            if not isinstance(t_raw, dict):
                logger.warning("Skipping malformed temperature entry at index %d", idx)
                continue

            t_id = extract_component_id(t_raw, f"temp-{idx + 1}")
            if not t_id:
                logger.warning("Skipping temperature entry missing identifier at index %d", idx)
                continue

            t_obj = TemperatureSensor(
                id=t_id,
                name=clean_string(t_raw.get("Name")),
                reading_celsius=clean_float(t_raw.get("ReadingCelsius")),
                upper_threshold_critical=clean_float(t_raw.get("UpperThresholdCritical")),
                status=parse_status(t_raw),
            )
            temperatures.append(t_obj)

    # 2. Parse Fans
    raw_fans = thermal_data.get("Fans")
    if isinstance(raw_fans, list):
        for idx, f_raw in enumerate(raw_fans):
            if not isinstance(f_raw, dict):
                logger.warning("Skipping malformed fan entry at index %d", idx)
                continue

            f_id = extract_component_id(f_raw, f"fan-{idx + 1}")
            if not f_id:
                logger.warning("Skipping fan entry missing identifier at index %d", idx)
                continue

            reading_rpm: int | None = clean_int(f_raw.get("ReadingRPM"))
            reading_percent: float | None = clean_float(f_raw.get("ReadingPercent"))

            units = clean_string(f_raw.get("Units"))
            raw_reading = f_raw.get("Reading")

            if raw_reading is not None:
                if units == "RPM" and reading_rpm is None:
                    reading_rpm = clean_int(raw_reading)
                elif units in ("Percent", "%") and reading_percent is None:
                    reading_percent = clean_float(raw_reading)

            fan_obj = CoolingFan(
                id=f_id,
                name=clean_string(f_raw.get("FanName") or f_raw.get("Name")),
                reading_rpm=reading_rpm,
                reading_percent=reading_percent,
                status=parse_status(f_raw),
            )
            fans.append(fan_obj)

    return temperatures, fans
