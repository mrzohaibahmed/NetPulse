"""
Parser for iLO Subsystem Firmware and System BIOS version metadata (Phase 3B).
"""

from __future__ import annotations

from typing import Any

from services.server_hardware.models import ServerFirmware
from services.server_hardware.parsers.utils import clean_string


def parse_firmware(
    service_root_data: dict[str, Any] | None = None,
    system_data: dict[str, Any] | None = None,
    manager_data: dict[str, Any] | None = None,
) -> ServerFirmware:
    """
    Extract iLO generation, iLO firmware version, and system BIOS version.

    Firmware metadata is purely diagnostic and does not affect operational health.
    """
    ilo_generation: str | None = None
    ilo_firmware_version: str | None = None
    bios_version: str | None = None

    if system_data and isinstance(system_data, dict):
        bios_version = clean_string(system_data.get("BiosVersion"))

    if manager_data and isinstance(manager_data, dict):
        ilo_generation = clean_string(manager_data.get("ManagerType"))
        ilo_firmware_version = clean_string(manager_data.get("FirmwareVersion"))

    # Inspect ServiceRoot OEM structures (iLO 4 / 5 / 6)
    if service_root_data and isinstance(service_root_data, dict):
        oem = service_root_data.get("Oem")
        if isinstance(oem, dict):
            # iLO 4 format: Oem -> Hp -> Type / Manager
            hp_oem = oem.get("Hp")
            if isinstance(hp_oem, dict):
                if not ilo_generation:
                    ilo_generation = clean_string(hp_oem.get("Type"))
                hp_mgrs = hp_oem.get("Manager")
                if isinstance(hp_mgrs, list) and hp_mgrs and isinstance(hp_mgrs[0], dict):
                    if not ilo_generation:
                        ilo_generation = clean_string(hp_mgrs[0].get("ManagerType"))
                    if not ilo_firmware_version:
                        ilo_firmware_version = clean_string(hp_mgrs[0].get("FirmwareVersion"))

            # iLO 5/6 format: Oem -> Hpe -> Manager
            hpe_oem = oem.get("Hpe")
            if isinstance(hpe_oem, dict):
                hpe_mgrs = hpe_oem.get("Manager")
                if isinstance(hpe_mgrs, list) and hpe_mgrs and isinstance(hpe_mgrs[0], dict):
                    if not ilo_generation:
                        ilo_generation = clean_string(hpe_mgrs[0].get("ManagerType"))
                    if not ilo_firmware_version:
                        ilo_firmware_version = clean_string(hpe_mgrs[0].get("FirmwareVersion"))

    return ServerFirmware(
        ilo_generation=ilo_generation,
        ilo_firmware_version=ilo_firmware_version,
        bios_version=bios_version,
    )
