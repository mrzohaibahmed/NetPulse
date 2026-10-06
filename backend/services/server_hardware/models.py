"""
Normalized Domain Models for Server Hardware (Phase 3B).

Defines strongly-typed dataclasses for HPE server hardware telemetry and identity.
Hides Redfish and generation-specific (iLO 4/5/6) schema differences from upper layers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ComponentStatus:
    """Normalized status structure for hardware components."""

    state: str | None = None
    health: str | None = None
    health_rollup: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServerIdentity:
    """Server identity attributes (excluding power state)."""

    hostname: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    uuid: str | None = None
    asset_tag: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PowerSupply:
    """Individual power supply unit."""

    id: str
    name: str | None = None
    model: str | None = None
    power_capacity_watts: float | None = None
    last_power_output_watts: float | None = None
    line_input_voltage: float | None = None
    status: ComponentStatus | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServerPowerSummary:
    """Server power state and telemetry summary."""

    power_state: str | None = None
    power_consumed_watts: float | None = None
    power_capacity_watts: float | None = None
    power_supplies: list[PowerSupply] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServerProcessor:
    """Individual CPU socket or processor."""

    id: str
    name: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    cores: int | None = None
    threads: int | None = None
    speed_mhz: int | None = None
    status: ComponentStatus | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServerMemoryDimm:
    """Individual RAM DIMM module."""

    id: str
    name: str | None = None
    capacity_bytes: int | None = None
    speed_mhz: int | None = None
    manufacturer: str | None = None
    part_number: str | None = None
    serial_number: str | None = None
    status: ComponentStatus | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StorageController:
    """Storage array controller or HBA."""

    id: str
    name: str | None = None
    model: str | None = None
    serial_number: str | None = None
    firmware_version: str | None = None
    status: ComponentStatus | None = None
    source_type: str | None = None  # "SmartStorage" or "StandardRedfish"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PhysicalDrive:
    """Physical hard disk drive or SSD."""

    id: str
    name: str | None = None
    controller_id: str | None = None
    model: str | None = None
    serial_number: str | None = None
    capacity_bytes: int | None = None
    media_type: str | None = None  # "SSD", "HDD", "NVMe", or None
    protocol: str | None = None
    status: ComponentStatus | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LogicalVolume:
    """Logical RAID volume or array."""

    id: str
    name: str | None = None
    controller_id: str | None = None
    capacity_bytes: int | None = None
    raid_type: str | None = None
    status: ComponentStatus | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServerStorage:
    """Unified storage topology containing controllers, physical drives, and logical volumes."""

    controllers: list[StorageController] = field(default_factory=list)
    physical_drives: list[PhysicalDrive] = field(default_factory=list)
    logical_volumes: list[LogicalVolume] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServerNetworkInterface:
    """Host operating system or iLO management network adapter/port."""

    id: str
    name: str | None = None
    mac_address: str | None = None
    link_status: str | None = None
    speed_mbps: int | None = None
    manufacturer: str | None = None
    model: str | None = None
    is_management_interface: bool = False
    status: ComponentStatus | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TemperatureSensor:
    """Thermal sensor reading and critical threshold."""

    id: str
    name: str | None = None
    reading_celsius: float | None = None
    upper_threshold_critical: float | None = None
    status: ComponentStatus | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CoolingFan:
    """Cooling fan tachometer or percentage reading."""

    id: str
    name: str | None = None
    reading_rpm: int | None = None
    reading_percent: float | None = None
    status: ComponentStatus | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServerFirmware:
    """Firmware version metadata for iLO management subsystem and system BIOS."""

    ilo_generation: str | None = None  # "iLO 4", "iLO 5", "iLO 6"
    ilo_firmware_version: str | None = None
    bios_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServerCapabilities:
    """
    Capability discovery state matrix.

    Flags indicate that the corresponding Redfish resource/capability was
    discovered and available to the parser. They do NOT indicate hardware health
    or query success.
    """

    has_processors: bool = False
    has_memory: bool = False
    has_storage: bool = False
    has_smart_storage: bool = False
    has_thermals: bool = False
    has_fans: bool = False
    has_power: bool = False
    has_network: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServerHardware:
    """
    Complete normalized domain representation of a server endpoint.

    Contains no raw Redfish JSON payloads and no calculated overall health decisions.
    """

    identity: ServerIdentity = field(default_factory=ServerIdentity)
    firmware: ServerFirmware = field(default_factory=ServerFirmware)
    power: ServerPowerSummary = field(default_factory=ServerPowerSummary)
    processors: list[ServerProcessor] = field(default_factory=list)
    memory: list[ServerMemoryDimm] = field(default_factory=list)
    storage: ServerStorage = field(default_factory=ServerStorage)
    network_interfaces: list[ServerNetworkInterface] = field(default_factory=list)
    temperatures: list[TemperatureSensor] = field(default_factory=list)
    fans: list[CoolingFan] = field(default_factory=list)
    capabilities: ServerCapabilities = field(default_factory=ServerCapabilities)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
