"""
Server Hardware Domain Normalization Service (Phase 3B).

Provides capability-aware normalization of HPE iLO Redfish payloads into unified domain models.
"""

from __future__ import annotations

from typing import Any

from services.server_hardware.models import (
    ComponentStatus,
    CoolingFan,
    LogicalVolume,
    PhysicalDrive,
    PowerSupply,
    ServerCapabilities,
    ServerFirmware,
    ServerHardware,
    ServerIdentity,
    ServerMemoryDimm,
    ServerNetworkInterface,
    ServerPowerSummary,
    ServerProcessor,
    ServerStorage,
    StorageController,
    TemperatureSensor,
)
from services.server_hardware.parsers import (
    RedfishParserError,
    parse_firmware,
    parse_memory,
    parse_network_interfaces,
    parse_power_summary,
    parse_processors,
    parse_storage,
    parse_system_identity,
    parse_thermals,
)


def normalize_server_hardware(
    *,
    service_root_data: dict[str, Any] | None = None,
    system_data: dict[str, Any] | None = None,
    processors_data: dict[str, Any] | list[Any] | None = None,
    memory_data: dict[str, Any] | list[Any] | None = None,
    standard_storage_data: dict[str, Any] | list[Any] | None = None,
    smart_storage_data: dict[str, Any] | list[Any] | None = None,
    host_network_data: dict[str, Any] | list[Any] | None = None,
    manager_network_data: dict[str, Any] | list[Any] | None = None,
    thermal_data: dict[str, Any] | None = None,
    power_chassis_data: dict[str, Any] | None = None,
    manager_data: dict[str, Any] | None = None,
) -> ServerHardware:
    """
    Assemble complete normalized ServerHardware from raw Redfish resource payloads.

    Performs capability detection from available resources and executes capability-first
    parsers. Does NOT perform network requests, database operations, or overall health calculation.
    """
    identity = (
        parse_system_identity(system_data)
        if system_data
        else ServerIdentity()
    )

    firmware = parse_firmware(
        service_root_data=service_root_data,
        system_data=system_data,
        manager_data=manager_data,
    )

    power = parse_power_summary(
        system_data=system_data,
        power_chassis_data=power_chassis_data,
    )

    processors = parse_processors(processors_data)
    memory = parse_memory(memory_data)

    storage = parse_storage(
        standard_storage_data=standard_storage_data,
        smart_storage_data=smart_storage_data,
    )

    network_interfaces = parse_network_interfaces(
        host_network_data=host_network_data,
        manager_network_data=manager_network_data,
    )

    temperatures, fans = parse_thermals(thermal_data)

    # Capability Discovery Matrix
    capabilities = ServerCapabilities(
        has_processors=bool(processors_data),
        has_memory=bool(memory_data),
        has_storage=bool(standard_storage_data),
        has_smart_storage=bool(smart_storage_data),
        has_thermals=bool(thermal_data and isinstance(thermal_data, dict) and "Temperatures" in thermal_data),
        has_fans=bool(thermal_data and isinstance(thermal_data, dict) and "Fans" in thermal_data),
        has_power=bool(power_chassis_data or (system_data and "PowerState" in system_data)),
        has_network=bool(host_network_data or manager_network_data),
    )

    return ServerHardware(
        identity=identity,
        firmware=firmware,
        power=power,
        processors=processors,
        memory=memory,
        storage=storage,
        network_interfaces=network_interfaces,
        temperatures=temperatures,
        fans=fans,
        capabilities=capabilities,
    )


from services.server_hardware.collector import (
    collect_all_server_hardware,
    poll_single_device_ilo_hardware,
)
from services.server_hardware.health import (
    ServerHardwareHealth,
    SubsystemHealth,
    evaluate_server_hardware_health,
    resolve_component_health,
)
from services.server_hardware.indexes import (
    ensure_server_hardware_indexes,
)
from services.server_hardware.persistence import (
    ServerHardwarePersistenceError,
    append_server_hardware_history,
    get_current_server_hardware,
    get_server_hardware_history,
    save_current_server_hardware,
    serialize_server_hardware,
)


__all__ = [
    "ComponentStatus",
    "CoolingFan",
    "LogicalVolume",
    "PhysicalDrive",
    "PowerSupply",
    "RedfishParserError",
    "ServerCapabilities",
    "ServerFirmware",
    "ServerHardware",
    "ServerHardwareHealth",
    "ServerHardwarePersistenceError",
    "ServerIdentity",
    "ServerMemoryDimm",
    "ServerNetworkInterface",
    "ServerPowerSummary",
    "ServerProcessor",
    "ServerStorage",
    "StorageController",
    "SubsystemHealth",
    "TemperatureSensor",
    "append_server_hardware_history",
    "collect_all_server_hardware",
    "ensure_server_hardware_indexes",
    "evaluate_server_hardware_health",
    "get_current_server_hardware",
    "get_server_hardware_history",
    "normalize_server_hardware",
    "poll_single_device_ilo_hardware",
    "resolve_component_health",
    "save_current_server_hardware",
    "serialize_server_hardware",
]
