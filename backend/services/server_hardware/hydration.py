"""
BSON Document Hydration / Deserializer Service for Server Hardware (Phase 3F).

Hydrates persisted MongoDB server_hardware_current dictionaries into normalized
ServerHardware domain dataclasses for evaluation by Phase 3E health engine.
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


def _parse_status(data: dict[str, Any] | None) -> ComponentStatus | None:
    if not isinstance(data, dict):
        return None
    return ComponentStatus(
        state=data.get("state"),
        health=data.get("health"),
        health_rollup=data.get("healthRollup"),
    )


def _parse_identity(data: dict[str, Any] | None) -> ServerIdentity:
    if not isinstance(data, dict):
        return ServerIdentity()
    return ServerIdentity(
        hostname=data.get("hostname"),
        manufacturer=data.get("manufacturer"),
        model=data.get("model"),
        serial_number=data.get("serialNumber"),
        uuid=data.get("uuid"),
        asset_tag=data.get("assetTag"),
    )


def _parse_firmware(data: dict[str, Any] | None) -> ServerFirmware:
    if not isinstance(data, dict):
        return ServerFirmware()
    return ServerFirmware(
        ilo_generation=data.get("iloGeneration"),
        ilo_firmware_version=data.get("iloFirmwareVersion"),
        bios_version=data.get("biosVersion"),
    )


def _parse_power(data: dict[str, Any] | None) -> ServerPowerSummary:
    if not isinstance(data, dict):
        return ServerPowerSummary()
    psus = []
    for ps in data.get("powerSupplies") or []:
        if isinstance(ps, dict):
            psus.append(
                PowerSupply(
                    id=str(ps.get("id", "")),
                    name=ps.get("name"),
                    model=ps.get("model"),
                    power_capacity_watts=ps.get("powerCapacityWatts"),
                    last_power_output_watts=ps.get("lastPowerOutputWatts"),
                    line_input_voltage=ps.get("lineInputVoltage"),
                    status=_parse_status(ps.get("status")),
                )
            )
    return ServerPowerSummary(
        power_state=data.get("powerState"),
        power_consumed_watts=data.get("powerConsumedWatts"),
        power_capacity_watts=data.get("powerCapacityWatts"),
        power_supplies=psus,
    )


def _parse_processors(data: list[Any] | None) -> list[ServerProcessor]:
    res = []
    for p in data or []:
        if isinstance(p, dict):
            res.append(
                ServerProcessor(
                    id=str(p.get("id", "")),
                    name=p.get("name"),
                    manufacturer=p.get("manufacturer"),
                    model=p.get("model"),
                    cores=p.get("cores"),
                    threads=p.get("threads"),
                    speed_mhz=p.get("speedMhz"),
                    status=_parse_status(p.get("status")),
                )
            )
    return res


def _parse_memory(data: list[Any] | None) -> list[ServerMemoryDimm]:
    res = []
    for m in data or []:
        if isinstance(m, dict):
            res.append(
                ServerMemoryDimm(
                    id=str(m.get("id", "")),
                    name=m.get("name"),
                    capacity_bytes=m.get("capacityBytes"),
                    speed_mhz=m.get("speedMhz"),
                    manufacturer=m.get("manufacturer"),
                    part_number=m.get("partNumber"),
                    serial_number=m.get("serialNumber"),
                    status=_parse_status(m.get("status")),
                )
            )
    return res


def _parse_storage(data: dict[str, Any] | None) -> ServerStorage:
    if not isinstance(data, dict):
        return ServerStorage()
    controllers = []
    for c in data.get("controllers") or []:
        if isinstance(c, dict):
            controllers.append(
                StorageController(
                    id=str(c.get("id", "")),
                    name=c.get("name"),
                    model=c.get("model"),
                    serial_number=c.get("serialNumber"),
                    firmware_version=c.get("firmwareVersion"),
                    status=_parse_status(c.get("status")),
                    source_type=c.get("sourceType"),
                )
            )
    drives = []
    for d in data.get("physicalDrives") or []:
        if isinstance(d, dict):
            drives.append(
                PhysicalDrive(
                    id=str(d.get("id", "")),
                    name=d.get("name"),
                    controller_id=d.get("controllerId"),
                    model=d.get("model"),
                    serial_number=d.get("serialNumber"),
                    capacity_bytes=d.get("capacityBytes"),
                    media_type=d.get("mediaType"),
                    protocol=d.get("protocol"),
                    status=_parse_status(d.get("status")),
                )
            )
    volumes = []
    for v in data.get("logicalVolumes") or []:
        if isinstance(v, dict):
            volumes.append(
                LogicalVolume(
                    id=str(v.get("id", "")),
                    name=v.get("name"),
                    controller_id=v.get("controllerId"),
                    capacity_bytes=v.get("capacityBytes"),
                    raid_type=v.get("raidType"),
                    status=_parse_status(v.get("status")),
                )
            )
    return ServerStorage(
        controllers=controllers,
        physical_drives=drives,
        logical_volumes=volumes,
    )


def _parse_network_interfaces(data: list[Any] | None) -> list[ServerNetworkInterface]:
    res = []
    for n in data or []:
        if isinstance(n, dict):
            res.append(
                ServerNetworkInterface(
                    id=str(n.get("id", "")),
                    name=n.get("name"),
                    mac_address=n.get("macAddress"),
                    link_status=n.get("linkStatus"),
                    speed_mbps=n.get("speedMbps"),
                    manufacturer=n.get("manufacturer"),
                    model=n.get("model"),
                    is_management_interface=bool(n.get("isManagementInterface", False)),
                    status=_parse_status(n.get("status")),
                )
            )
    return res


def _parse_temperatures(data: list[Any] | None) -> list[TemperatureSensor]:
    res = []
    for t in data or []:
        if isinstance(t, dict):
            res.append(
                TemperatureSensor(
                    id=str(t.get("id", "")),
                    name=t.get("name"),
                    reading_celsius=t.get("readingCelsius"),
                    upper_threshold_critical=t.get("upperThresholdCritical"),
                    status=_parse_status(t.get("status")),
                )
            )
    return res


def _parse_fans(data: list[Any] | None) -> list[CoolingFan]:
    res = []
    for f in data or []:
        if isinstance(f, dict):
            res.append(
                CoolingFan(
                    id=str(f.get("id", "")),
                    name=f.get("name"),
                    reading_rpm=f.get("readingRpm"),
                    reading_percent=f.get("readingPercent"),
                    status=_parse_status(f.get("status")),
                )
            )
    return res


def _parse_capabilities(data: dict[str, Any] | None) -> ServerCapabilities:
    if not isinstance(data, dict):
        return ServerCapabilities()
    return ServerCapabilities(
        has_processors=bool(data.get("hasProcessors", False)),
        has_memory=bool(data.get("hasMemory", False)),
        has_storage=bool(data.get("hasStorage", False)),
        has_smart_storage=bool(data.get("hasSmartStorage", False)),
        has_thermals=bool(data.get("hasThermals", False)),
        has_fans=bool(data.get("hasFans", False)),
        has_power=bool(data.get("hasPower", False)),
        has_network=bool(data.get("hasNetwork", False)),
    )


def deserialize_server_hardware(doc: dict[str, Any] | None) -> ServerHardware:
    """
    Hydrate a BSON dictionary from server_hardware_current into a ServerHardware dataclass.
    """
    if not isinstance(doc, dict):
        return ServerHardware()
    return ServerHardware(
        identity=_parse_identity(doc.get("identity")),
        firmware=_parse_firmware(doc.get("firmware")),
        power=_parse_power(doc.get("power")),
        processors=_parse_processors(doc.get("processors")),
        memory=_parse_memory(doc.get("memory")),
        storage=_parse_storage(doc.get("storage")),
        network_interfaces=_parse_network_interfaces(doc.get("networkInterfaces")),
        temperatures=_parse_temperatures(doc.get("temperatures")),
        fans=_parse_fans(doc.get("fans")),
        capabilities=_parse_capabilities(doc.get("capabilities")),
    )
