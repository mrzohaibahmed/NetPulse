"""
Unit and Integration Tests for Phase 3B Server Hardware Normalization Parsers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.server_hardware import (
    ComponentStatus,
    LogicalVolume,
    PhysicalDrive,
    RedfishParserError,
    ServerHardware,
    StorageController,
    normalize_server_hardware,
)
from services.server_hardware.parsers import (
    parse_firmware,
    parse_memory,
    parse_network_interfaces,
    parse_power_summary,
    parse_processors,
    parse_storage,
    parse_system_identity,
    parse_thermals,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "redfish"


@pytest.fixture
def ilo4_root():
    return json.loads((FIXTURES_DIR / "ilo4_dl380.json").read_text(encoding="utf-8"))


@pytest.fixture
def ilo5_root():
    return json.loads((FIXTURES_DIR / "ilo5_dl380.json").read_text(encoding="utf-8"))


@pytest.fixture
def ilo6_root():
    return json.loads((FIXTURES_DIR / "ilo6_dl380.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. iLO 4 Fixture & SmartStorage Normalization Tests
# ---------------------------------------------------------------------------

def test_ilo4_full_normalization(ilo4_root):
    """Verify iLO 4 fixture parses correctly with SmartStorage OEM support."""
    system_payload = {
        "HostName": "srv-dl380-g9",
        "Manufacturer": "HP",
        "Model": "ProLiant DL380 Gen9",
        "SerialNumber": "USE1234567",
        "UUID": "38303035-3038-5553-4531-323334353637",
        "AssetTag": "ASSET-9901",
        "PowerState": "On",
        "BiosVersion": "P89 v2.72",
    }

    processor_payload = {
        "Members": [
            {
                "@odata.id": "/redfish/v1/Systems/1/Processors/1",
                "Id": "1",
                "Name": "Proc 1",
                "Manufacturer": "Intel",
                "Model": "Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz",
                "TotalCores": 14,
                "TotalThreads": 28,
                "MaxSpeedMHz": 2400,
                "Status": {"State": "Enabled", "Health": "OK"},
            }
        ]
    }

    memory_payload = [
        {
            "@odata.id": "/redfish/v1/Systems/1/Memory/PROC1_DIMM1",
            "Id": "PROC1_DIMM1",
            "Name": "Memory DIMM 1",
            "CapacityMiB": 32768,
            "OperatingSpeedMhz": 2400,
            "Manufacturer": "Hynix",
            "PartNumber": "HMA84GR7MFR4N-UH",
            "SerialNumber": "012345678",
            "Status": {"State": "Enabled", "Health": "OK"},
        }
    ]

    smart_storage_payload = [
        {
            "@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/1",
            "Id": "1",
            "Model": "Smart Array P440ar",
            "SerialNumber": "P440AR123",
            "FirmwareVersion": {"Current": {"VersionString": "6.06"}},
            "Status": {"State": "Enabled", "Health": "OK"},
            "DiskDrives": [
                {
                    "@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/1/DiskDrives/1",
                    "Id": "1",
                    "Model": "EG0900FCVBL",
                    "SerialNumber": "DRV123",
                    "CapacityGB": 900,
                    "MediaType": "SAS HDD",
                    "InterfaceType": "SAS",
                    "Status": {"State": "Enabled", "Health": "OK"},
                }
            ],
            "LogicalDrives": [
                {
                    "@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/1/LogicalDrives/1",
                    "Id": "1",
                    "LogicalDriveName": "Logical Drive 1",
                    "CapacityMiB": 858368,
                    "Raid": "Raid1",
                    "Status": {"State": "Enabled", "Health": "OK"},
                }
            ],
        }
    ]

    thermal_payload = {
        "Temperatures": [
            {
                "@odata.id": "/redfish/v1/Chassis/1/Thermal/1",
                "Name": "01-Inlet",
                "ReadingCelsius": 22.0,
                "UpperThresholdCritical": 42.0,
                "Status": {"State": "Enabled", "Health": "OK"},
            }
        ],
        "Fans": [
            {
                "@odata.id": "/redfish/v1/Chassis/1/Thermal/Fan1",
                "FanName": "Fan 1",
                "Reading": 30,
                "Units": "Percent",
                "Status": {"State": "Enabled", "Health": "OK"},
            }
        ],
    }

    server = normalize_server_hardware(
        service_root_data=ilo4_root,
        system_data=system_payload,
        processors_data=processor_payload,
        memory_data=memory_payload,
        smart_storage_data=smart_storage_payload,
        thermal_data=thermal_payload,
    )

    assert server.identity.hostname == "srv-dl380-g9"
    assert server.identity.serial_number == "USE1234567"
    assert server.firmware.ilo_generation == "iLO 4"
    assert server.firmware.ilo_firmware_version == "2.78"
    assert server.firmware.bios_version == "P89 v2.72"
    assert server.power.power_state == "On"

    # Processors
    assert len(server.processors) == 1
    assert server.processors[0].cores == 14
    assert server.processors[0].speed_mhz == 2400

    # Memory
    assert len(server.memory) == 1
    assert server.memory[0].capacity_bytes == 32768 * 1024 * 1024

    # Storage (SmartStorage OEM)
    assert len(server.storage.controllers) == 1
    assert server.storage.controllers[0].source_type == "SmartStorage"
    assert server.storage.controllers[0].firmware_version == "6.06"

    assert len(server.storage.physical_drives) == 1
    assert server.storage.physical_drives[0].media_type == "HDD"
    assert server.storage.physical_drives[0].capacity_bytes == 900 * 1000 * 1000 * 1000

    assert len(server.storage.logical_volumes) == 1
    assert server.storage.logical_volumes[0].capacity_bytes == 858368 * 1024 * 1024

    # Thermals & Fans
    assert len(server.temperatures) == 1
    assert server.temperatures[0].reading_celsius == 22.0
    assert len(server.fans) == 1
    assert server.fans[0].reading_percent == 30.0

    # Capabilities
    assert server.capabilities.has_smart_storage is True
    assert server.capabilities.has_storage is False
    assert server.capabilities.has_processors is True
    assert server.capabilities.has_memory is True


# ---------------------------------------------------------------------------
# 2. iLO 5 & iLO 6 Standard Redfish Storage Normalization Tests
# ---------------------------------------------------------------------------

def test_ilo5_standard_storage(ilo5_root):
    """Verify iLO 5 fixture parses standard Redfish Storage topology."""
    standard_storage = {
        "@odata.id": "/redfish/v1/Systems/1/Storage/1",
        "Id": "1",
        "StorageControllers": [
            {
                "@odata.id": "/redfish/v1/Systems/1/Storage/1/Controllers/1",
                "Name": "Smart Array P408i-a SR Gen10",
                "Model": "P408i-a SR Gen10",
                "SerialNumber": "P408I12345",
                "FirmwareVersion": "5.32",
                "Status": {"State": "Enabled", "Health": "OK"},
            }
        ],
        "Drives": [
            {
                "@odata.id": "/redfish/v1/Systems/1/Storage/1/Drives/1",
                "Name": "Drive 1",
                "Model": "VO000960KWVBR",
                "SerialNumber": "SNDRIVE960",
                "CapacityBytes": 960197124096,
                "MediaType": "SSD",
                "Protocol": "SATA",
                "Status": {"State": "Enabled", "Health": "OK"},
            }
        ],
        "Volumes": [
            {
                "@odata.id": "/redfish/v1/Systems/1/Storage/1/Volumes/1",
                "Name": "OS_Volume",
                "CapacityBytes": 960197124096,
                "RAIDType": "RAID0",
                "Status": {"State": "Enabled", "Health": "OK"},
            }
        ],
    }

    server = normalize_server_hardware(
        service_root_data=ilo5_root,
        standard_storage_data=standard_storage,
    )

    assert server.firmware.ilo_generation == "iLO 5"
    assert server.capabilities.has_storage is True
    assert server.capabilities.has_smart_storage is False

    assert len(server.storage.controllers) == 1
    assert server.storage.controllers[0].source_type == "StandardRedfish"
    assert server.storage.controllers[0].firmware_version == "5.32"

    assert len(server.storage.physical_drives) == 1
    assert server.storage.physical_drives[0].media_type == "SSD"
    assert server.storage.physical_drives[0].capacity_bytes == 960197124096

    assert len(server.storage.logical_volumes) == 1
    assert server.storage.logical_volumes[0].raid_type == "RAID0"


def test_ilo6_fixture_compatibility(ilo6_root):
    """Verify iLO 6 fixture metadata parsing."""
    server = normalize_server_hardware(service_root_data=ilo6_root)
    assert server.firmware.ilo_generation == "iLO 6"
    assert server.firmware.ilo_firmware_version == "iLO 6 v1.55"


# ---------------------------------------------------------------------------
# 3. Network Interface Isolation Tests
# ---------------------------------------------------------------------------

def test_network_interface_isolation():
    """Verify host NICs receive is_management_interface=False and iLO manager receives True."""
    host_nics = [
        {
            "@odata.id": "/redfish/v1/Systems/1/EthernetInterfaces/1",
            "Name": "Embedded NIC Port 1",
            "MACAddress": "00:11:22:33:44:55",
            "LinkStatus": "LinkUp",
            "SpeedMbps": 10000,
            "Status": {"State": "Enabled", "Health": "OK"},
        }
    ]

    ilo_nic = {
        "@odata.id": "/redfish/v1/Managers/1/EthernetInterfaces/1",
        "Name": "iLO Dedicated Management Port",
        "MACAddress": "AA:BB:CC:DD:EE:FF",
        "LinkStatus": "LinkUp",
        "SpeedMbps": 1000,
        "Status": {"State": "Enabled", "Health": "OK"},
    }

    ifaces = parse_network_interfaces(
        host_network_data=host_nics,
        manager_network_data=ilo_nic,
    )

    assert len(ifaces) == 2
    host_if = next(i for i in ifaces if not i.is_management_interface)
    mgr_if = next(i for i in ifaces if i.is_management_interface)

    assert host_if.mac_address == "00:11:22:33:44:55"
    assert host_if.speed_mbps == 10000
    assert mgr_if.mac_address == "AA:BB:CC:DD:EE:FF"
    assert mgr_if.speed_mbps == 1000


# ---------------------------------------------------------------------------
# 4. Required Edge Cases & Invariant Tests
# ---------------------------------------------------------------------------

def test_missing_optional_properties_become_none():
    """Missing fields become None, not 'N/A' or 'Unknown' placeholders."""
    raw = {"@odata.id": "/redfish/v1/Systems/1/Processors/1", "Name": "  "}
    procs = parse_processors(raw)
    assert len(procs) == 1
    assert procs[0].model is None
    assert procs[0].manufacturer is None
    assert procs[0].cores is None
    assert procs[0].speed_mhz is None


def test_valid_zero_values_preserved():
    """Real zero numeric measurements must remain 0/0.0."""
    power_raw = {"PowerControl": [{"PowerConsumedWatts": 0.0}]}
    power_obj = parse_power_summary(system_data={"PowerState": "Off"}, power_chassis_data=power_raw)
    assert power_obj.power_consumed_watts == 0.0
    assert power_obj.power_state == "Off"


def test_empty_collections():
    """Explicitly empty collection returns []."""
    assert parse_processors([]) == []
    assert parse_memory([]) == []


def test_malformed_member_skipping():
    """One malformed array item is skipped while retaining valid items."""
    raw_list = [
        "corrupt string instead of dict",
        {
            "@odata.id": "/redfish/v1/Systems/1/Processors/1",
            "Name": "Proc 1",
            "TotalCores": 8,
        },
    ]
    procs = parse_processors(raw_list)
    assert len(procs) == 1
    assert procs[0].id == "/redfish/v1/Systems/1/Processors/1"


def test_missing_required_system_identity_raises_error():
    """Missing or non-dict system identity payload raises RedfishParserError."""
    with pytest.raises(RedfishParserError):
        parse_system_identity(None)


def test_strict_media_type_mapping():
    """Media types map strictly to 'SSD', 'HDD', 'NVMe', or None."""
    raw_storage = {
        "@odata.id": "/redfish/v1/Systems/1/Storage/1",
        "Drives": [
            {"@odata.id": "d1", "MediaType": "SolidStateDrive"},
            {"@odata.id": "d2", "MediaType": "RotationalDisk"},
            {"@odata.id": "d3", "MediaType": "NVMe Flash"},
            {"@odata.id": "d4", "MediaType": "TapeDrive"},
        ],
    }
    storage = parse_storage(standard_storage_data=raw_storage)
    assert storage.physical_drives[0].media_type == "SSD"
    assert storage.physical_drives[1].media_type == "HDD"
    assert storage.physical_drives[2].media_type == "NVMe"
    assert storage.physical_drives[3].media_type is None


def test_invariants():
    """Verify architectural invariants for Phase 3B."""
    server = normalize_server_hardware()

    # Invariant 1: No raw Redfish JSON in ServerHardware object
    srv_dict = server.to_dict()
    assert "@odata.id" not in srv_dict
    assert "@odata.context" not in srv_dict

    # Invariant 2: No duplicate power_state in identity
    assert not hasattr(server.identity, "power_state")

    # Invariant 3: No calculated overall health decision attribute
    assert not hasattr(server, "overall_health")

    # Invariant 4: Capabilities describe resource discovery, not health
    assert server.capabilities.has_processors is False
