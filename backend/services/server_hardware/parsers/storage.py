"""
Parser for Redfish Storage resources (Phase 3B).

Abstracts HPE iLO 4 SmartStorage OEM resources and HPE iLO 5/6 Standard Redfish Storage
resources into unified ServerStorage, StorageController, PhysicalDrive, and LogicalVolume models.
"""

from __future__ import annotations

from typing import Any

from services.server_hardware.models import (
    LogicalVolume,
    PhysicalDrive,
    ServerStorage,
    StorageController,
)
from services.server_hardware.parsers.utils import (
    clean_float,
    clean_int,
    clean_string,
    extract_component_id,
    logger,
    parse_status,
)


def _normalize_media_type(raw: Any) -> str | None:
    """
    Strictly map raw media type string to "SSD", "HDD", "NVMe", or None.

    Does not guess or default unknown values to HDD or SSD.
    """
    text = clean_string(raw)
    if not text:
        return None
    upper = text.upper()
    if "NVME" in upper:
        return "NVMe"
    if "SSD" in upper or "SOLID" in upper or "FLASH" in upper:
        return "SSD"
    if "HDD" in upper or "ROTATIONAL" in upper or "MAGNETIC" in upper or "SPINNING" in upper:
        return "HDD"
    return None


def parse_storage(
    standard_storage_data: dict[str, Any] | list[Any] | None = None,
    smart_storage_data: dict[str, Any] | list[Any] | None = None,
) -> ServerStorage:
    """
    Parse discovered storage resources using capability-first precedence.

    Tries Standard Redfish Storage resources first (iLO 5/6). If unavailable, falls back
    to HPE SmartStorage resources (iLO 4 OEM).
    """
    if standard_storage_data:
        res = parse_standard_storage(standard_storage_data)
        if res.controllers or res.physical_drives or res.logical_volumes:
            return res

    if smart_storage_data:
        res = parse_smart_storage(smart_storage_data)
        if res.controllers or res.physical_drives or res.logical_volumes:
            return res

    return ServerStorage()


def parse_standard_storage(storage_payloads: dict[str, Any] | list[Any]) -> ServerStorage:
    """Parse iLO 5 / iLO 6 Standard Redfish Storage resources."""
    controllers: list[StorageController] = []
    drives: list[PhysicalDrive] = []
    volumes: list[LogicalVolume] = []

    items = storage_payloads if isinstance(storage_payloads, list) else [storage_payloads]

    for idx, storage_obj in enumerate(items):
        if not isinstance(storage_obj, dict):
            logger.warning("Skipping malformed standard storage object at index %d", idx)
            continue

        parent_id = extract_component_id(storage_obj, f"storage-{idx + 1}")

        # Parse StorageControllers
        raw_controllers = storage_obj.get("StorageControllers")
        if isinstance(raw_controllers, list):
            for c_idx, c_raw in enumerate(raw_controllers):
                if not isinstance(c_raw, dict):
                    continue
                c_id = extract_component_id(c_raw, f"{parent_id}/controller/{c_idx + 1}")
                if not c_id:
                    continue
                ctrl_obj = StorageController(
                    id=c_id,
                    name=clean_string(c_raw.get("Name")),
                    model=clean_string(c_raw.get("Model")),
                    serial_number=clean_string(c_raw.get("SerialNumber")),
                    firmware_version=clean_string(c_raw.get("FirmwareVersion")),
                    status=parse_status(c_raw),
                    source_type="StandardRedfish",
                )
                controllers.append(ctrl_obj)

        # Parse Physical Drives
        raw_drives = storage_obj.get("Drives")
        if isinstance(raw_drives, list):
            for d_idx, d_raw in enumerate(raw_drives):
                if not isinstance(d_raw, dict):
                    continue
                d_id = extract_component_id(d_raw, f"{parent_id}/drive/{d_idx + 1}")
                if not d_id:
                    continue

                # Capacity calculation
                capacity_bytes = clean_int(d_raw.get("CapacityBytes"))
                if capacity_bytes is None:
                    cap_gb = clean_float(d_raw.get("CapacityGB"))
                    if cap_gb is not None:
                        capacity_bytes = int(cap_gb * 1000 * 1000 * 1000)

                drive_obj = PhysicalDrive(
                    id=d_id,
                    name=clean_string(d_raw.get("Name")),
                    controller_id=parent_id,
                    model=clean_string(d_raw.get("Model")),
                    serial_number=clean_string(d_raw.get("SerialNumber")),
                    capacity_bytes=capacity_bytes,
                    media_type=_normalize_media_type(d_raw.get("MediaType")),
                    protocol=clean_string(d_raw.get("Protocol")),
                    status=parse_status(d_raw),
                )
                drives.append(drive_obj)

        # Parse Volumes
        raw_volumes = storage_obj.get("Volumes")
        if isinstance(raw_volumes, list):
            for v_idx, v_raw in enumerate(raw_volumes):
                if not isinstance(v_raw, dict):
                    continue
                v_id = extract_component_id(v_raw, f"{parent_id}/volume/{v_idx + 1}")
                if not v_id:
                    continue

                capacity_bytes = clean_int(v_raw.get("CapacityBytes"))
                if capacity_bytes is None:
                    cap_bytes_raw = clean_float(v_raw.get("CapacityBytes"))
                    if cap_bytes_raw is not None:
                        capacity_bytes = int(cap_bytes_raw)

                vol_obj = LogicalVolume(
                    id=v_id,
                    name=clean_string(v_raw.get("Name") or v_raw.get("VolumeName")),
                    controller_id=parent_id,
                    capacity_bytes=capacity_bytes,
                    raid_type=clean_string(v_raw.get("RAIDType") or v_raw.get("Raid")),
                    status=parse_status(v_raw),
                )
                volumes.append(vol_obj)

    return ServerStorage(
        controllers=controllers,
        physical_drives=drives,
        logical_volumes=volumes,
    )


def parse_smart_storage(smart_payloads: dict[str, Any] | list[Any]) -> ServerStorage:
    """Parse iLO 4 HPE SmartStorage OEM resources."""
    controllers: list[StorageController] = []
    drives: list[PhysicalDrive] = []
    volumes: list[LogicalVolume] = []

    items = smart_payloads if isinstance(smart_payloads, list) else [smart_payloads]

    for idx, ctrl_obj in enumerate(items):
        if not isinstance(ctrl_obj, dict):
            logger.warning("Skipping malformed SmartStorage object at index %d", idx)
            continue

        c_id = extract_component_id(ctrl_obj, f"smartstorage-controller-{idx + 1}")
        if not c_id:
            continue

        # Extract SmartStorage firmware version
        fw_ver: str | None = None
        fw_obj = ctrl_obj.get("FirmwareVersion")
        if isinstance(fw_obj, dict):
            curr = fw_obj.get("Current")
            if isinstance(curr, dict):
                fw_ver = clean_string(curr.get("VersionString"))
        elif isinstance(fw_obj, str):
            fw_ver = clean_string(fw_obj)

        controller_item = StorageController(
            id=c_id,
            name=clean_string(ctrl_obj.get("Name") or ctrl_obj.get("Model")),
            model=clean_string(ctrl_obj.get("Model")),
            serial_number=clean_string(ctrl_obj.get("SerialNumber")),
            firmware_version=fw_ver,
            status=parse_status(ctrl_obj),
            source_type="SmartStorage",
        )
        controllers.append(controller_item)

        # Parse DiskDrives
        disk_drives = ctrl_obj.get("DiskDrives") or ctrl_obj.get("PhysicalDrives")
        if isinstance(disk_drives, list):
            for d_idx, d_raw in enumerate(disk_drives):
                if not isinstance(d_raw, dict):
                    continue
                d_id = extract_component_id(d_raw, f"{c_id}/disk/{d_idx + 1}")
                if not d_id:
                    continue

                capacity_bytes = clean_int(d_raw.get("CapacityBytes"))
                if capacity_bytes is None:
                    cap_gb = clean_float(d_raw.get("CapacityGB") or d_raw.get("CapacityMiB"))
                    if cap_gb is not None:
                        # If CapacityGB, convert GB to bytes; if CapacityMiB, convert MiB to bytes
                        if "MiB" in str(d_raw.get("CapacityMiB") or ""):
                            capacity_bytes = int(cap_gb * 1024 * 1024)
                        else:
                            capacity_bytes = int(cap_gb * 1000 * 1000 * 1000)

                drive_obj = PhysicalDrive(
                    id=d_id,
                    name=clean_string(d_raw.get("Name") or d_raw.get("Model")),
                    controller_id=c_id,
                    model=clean_string(d_raw.get("Model")),
                    serial_number=clean_string(d_raw.get("SerialNumber")),
                    capacity_bytes=capacity_bytes,
                    media_type=_normalize_media_type(d_raw.get("MediaType") or d_raw.get("InterfaceType")),
                    protocol=clean_string(d_raw.get("Protocol") or d_raw.get("InterfaceType")),
                    status=parse_status(d_raw),
                )
                drives.append(drive_obj)

        # Parse LogicalDrives
        logical_drives = ctrl_obj.get("LogicalDrives")
        if isinstance(logical_drives, list):
            for v_idx, v_raw in enumerate(logical_drives):
                if not isinstance(v_raw, dict):
                    continue
                v_id = extract_component_id(v_raw, f"{c_id}/logical-drive/{v_idx + 1}")
                if not v_id:
                    continue

                capacity_bytes = clean_int(v_raw.get("CapacityBytes"))
                if capacity_bytes is None:
                    cap_mib = clean_float(v_raw.get("CapacityMiB"))
                    if cap_mib is not None:
                        capacity_bytes = int(cap_mib * 1024 * 1024)

                vol_obj = LogicalVolume(
                    id=v_id,
                    name=clean_string(v_raw.get("LogicalDriveName") or v_raw.get("Name")),
                    controller_id=c_id,
                    capacity_bytes=capacity_bytes,
                    raid_type=clean_string(v_raw.get("Raid") or v_raw.get("RaidLevel")),
                    status=parse_status(v_raw),
                )
                volumes.append(vol_obj)

    return ServerStorage(
        controllers=controllers,
        physical_drives=drives,
        logical_volumes=volumes,
    )
