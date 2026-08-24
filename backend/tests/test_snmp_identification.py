"""
Unit tests for Phase 4 — SNMP Device Identification.

All tests use mocks — NO real network/SNMP operations are performed.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if "config.database" not in sys.modules:
    _db_mod = types.ModuleType("config.database")
    _db_mod.db = MagicMock()
    sys.modules["config.database"] = _db_mod

_db_mod = sys.modules["config.database"]
_db_mod.MAX_SCAN_THREADS = 5
_db_mod.MAX_INTERFACE_THREADS = 5
_db_mod.MAX_INTERFACE_STATS_THREADS = 8
_db_mod.INTERFACE_SCAN_INTERVAL = 3600
_db_mod.INTERFACE_STATS_INTERVAL = 60
_db_mod.INTERFACE_STATS_BATCH_SIZE = 500
_db_mod.WMI_TIMEOUT = 15
_db_mod.ONVIF_TIMEOUT = 5

from services.discovery.classifier import ClassificationResult
from services.discovery.identification import (
    IdentificationContext,
    IdentificationManager,
    IdentificationResult,
    NmapIdentifier,
    SNMPIdentifier,
)
from services.interface_collection.snmp import (
    SNMPCollectorError,
    SnmpDeviceInfo,
)


def _device_with_snmp(
    community: str = "public",
    port: int = 161,
    version: str = "2c",
    device_type: str = "Switch",
) -> dict:
    from utils.secret_crypto import encrypt_secret

    return {
        "deviceType": device_type,
        "credentials": {
            "snmpCommunity": encrypt_secret(community),
            "snmpPort": port,
            "snmpVersion": version,
        },
    }


# ---------------------------------------------------------------------------
# 1. Cisco Switch with ENTITY-MIB -> Managed Switch
# ---------------------------------------------------------------------------
@patch("services.interface_collection.snmp.snmp_available", return_value=True)
@patch("services.interface_collection.snmp.collect_snmp_inventory")
def test_snmp_switch_identification(mock_collect, mock_available):
    mock_collect.return_value = SnmpDeviceInfo(
        sys_descr="Cisco IOS Software, C3750E Software (C3750E-UNIVERSALK9-M), Version 15.0(2)SE",
        sys_object_id="1.3.6.1.4.1.9.1.516",
        sys_name="SW-CORE-01.local",
        sys_uptime="120 days, 04:12:00",
        manufacturer="Cisco",
        model="WS-C3750E-48PD",
        serial_number="FOC1234X56Y",
        hardware_rev="V02",
        firmware_rev="15.0(2)SE",
        software_rev="15.0(2)SE",
        vendor_from_oid="Cisco",
    )

    identifier = SNMPIdentifier()
    device = _device_with_snmp(device_type="Managed Switch")
    ctx = IdentificationContext(ip_address="192.168.1.10", existing=device)

    assert identifier.supports(ctx) is True
    result = identifier.identify(ctx)

    assert result.success is True
    assert result.method == "snmp"
    assert result.confidence >= 95
    assert result.device_type in ("Managed Switch", "Switch")
    assert result.evidence["manufacturer"] == "Cisco"
    assert result.evidence["model"] == "WS-C3750E-48PD"
    assert result.evidence["serialNumber"] == "FOC1234X56Y"
    assert result.evidence["sysObjectID"] == "1.3.6.1.4.1.9.1.516"
    assert result.evidence["sysName"] == "SW-CORE-01.local"


# ---------------------------------------------------------------------------
# 2. Juniper Router -> Router
# ---------------------------------------------------------------------------
@patch("services.interface_collection.snmp.snmp_available", return_value=True)
@patch("services.interface_collection.snmp.collect_snmp_inventory")
def test_snmp_router_identification(mock_collect, mock_available):
    mock_collect.return_value = SnmpDeviceInfo(
        sys_descr="Juniper Networks, Inc. srx300 internet router, kernel JUNOS 21.4R1.12",
        sys_object_id="1.3.6.1.4.1.2636.1.1.1.2.27",
        sys_name="RTR-EDGE-01",
        sys_uptime="45 days, 12:00:00",
        manufacturer="Juniper",
        model="SRX300",
        serial_number="CW0218123456",
        vendor_from_oid="Juniper",
    )

    identifier = SNMPIdentifier()
    device = _device_with_snmp(device_type="Router")
    ctx = IdentificationContext(ip_address="192.168.1.1", existing=device)

    result = identifier.identify(ctx)

    assert result.success is True
    assert result.method == "snmp"
    assert result.device_type == "Router"
    assert result.evidence["manufacturer"] == "Juniper"
    assert result.evidence["model"] == "SRX300"


# ---------------------------------------------------------------------------
# 3. HP LaserJet Printer via sysDescr -> Printer
# ---------------------------------------------------------------------------
@patch("services.interface_collection.snmp.snmp_available", return_value=True)
@patch("services.interface_collection.snmp.collect_snmp_inventory")
def test_snmp_printer_identification(mock_collect, mock_available):
    mock_collect.return_value = SnmpDeviceInfo(
        sys_descr="HP ETHERNET MULTI-FUNCTION PRINTER,JETDIRECT,HP LaserJet MFP M428fdw,V.39.2",
        sys_object_id="1.3.6.1.4.1.11.2.3.9.1",
        sys_name="PRN-OFFICE-2F",
        sys_uptime="10 days, 01:23:45",
        manufacturer="HP",
        model="HP LaserJet MFP M428fdw",
        serial_number="VNB3K98765",
        vendor_from_oid="HP",
    )

    identifier = SNMPIdentifier()
    device = _device_with_snmp(device_type="Printer")
    ctx = IdentificationContext(ip_address="192.168.1.50", existing=device)

    result = identifier.identify(ctx)

    assert result.success is True
    assert result.method == "snmp"
    assert result.device_type == "Printer"
    assert result.evidence["manufacturer"] == "HP"
    assert result.evidence["serialNumber"] == "VNB3K98765"


# ---------------------------------------------------------------------------
# 4. Aruba Access Point -> Access Point
# ---------------------------------------------------------------------------
@patch("services.interface_collection.snmp.snmp_available", return_value=True)
@patch("services.interface_collection.snmp.collect_snmp_inventory")
def test_snmp_access_point_identification(mock_collect, mock_available):
    mock_collect.return_value = SnmpDeviceInfo(
        sys_descr="ArubaOS (MODEL: 515), Version 8.10.0.5",
        sys_object_id="1.3.6.1.4.1.14823.1.2.91",
        sys_name="AP-LOBBY-01",
        sys_uptime="88 days, 10:00:00",
        manufacturer="Aruba",
        model="AP-515",
        serial_number="CNFK123456",
        vendor_from_oid="Aruba",
    )

    identifier = SNMPIdentifier()
    device = _device_with_snmp(device_type="Access Point")
    ctx = IdentificationContext(ip_address="192.168.1.75", existing=device)

    result = identifier.identify(ctx)

    assert result.success is True
    assert result.method == "snmp"
    assert result.device_type == "Access Point"
    assert result.evidence["manufacturer"] == "Aruba"


# ---------------------------------------------------------------------------
# 5. SNMP Timeout
# ---------------------------------------------------------------------------
@patch("services.interface_collection.snmp.snmp_available", return_value=True)
@patch("services.interface_collection.snmp.collect_snmp_inventory")
def test_snmp_timeout(mock_collect, mock_available):
    mock_collect.side_effect = SNMPCollectorError("No SNMP response received from 192.168.1.99 (timed out)")

    identifier = SNMPIdentifier()
    device = _device_with_snmp()
    ctx = IdentificationContext(ip_address="192.168.1.99", existing=device)

    result = identifier.identify(ctx)

    assert result.success is False
    assert result.method == "snmp"
    assert "timed out" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# 6. Authentication failure (community string error)
# ---------------------------------------------------------------------------
@patch("services.interface_collection.snmp.snmp_available", return_value=True)
@patch("services.interface_collection.snmp.collect_snmp_inventory")
def test_snmp_auth_failure(mock_collect, mock_available):
    secret_community = "SuperSecretCommunity123"
    mock_collect.side_effect = SNMPCollectorError(f"SNMP request failed with community {secret_community}")

    identifier = SNMPIdentifier()
    device = _device_with_snmp(community=secret_community)
    ctx = IdentificationContext(ip_address="192.168.1.101", existing=device)

    result = identifier.identify(ctx)

    assert result.success is False
    assert result.method == "snmp"
    # Ensure community string is redacted in error text
    assert secret_community not in (result.error or "")


# ---------------------------------------------------------------------------
# 7. Unsupported OID -> Partial response fallback to sysDescr
# ---------------------------------------------------------------------------
@patch("services.interface_collection.snmp.snmp_available", return_value=True)
@patch("services.interface_collection.snmp.collect_snmp_inventory")
def test_snmp_unsupported_oid(mock_collect, mock_available):
    # Device returns sysDescr but throws on custom OIDs
    mock_collect.return_value = SnmpDeviceInfo(
        sys_descr="Linux Router 5.10.0 #1 SMP",
        sys_object_id="1.3.6.1.4.1.8072.3.2.10",
        sys_name="router-custom",
        sys_uptime="5 days",
        vendor_from_oid="Net-SNMP",
    )

    identifier = SNMPIdentifier()
    device = _device_with_snmp(device_type="Router")
    ctx = IdentificationContext(ip_address="192.168.1.102", existing=device)

    result = identifier.identify(ctx)

    assert result.success is True
    assert result.method == "snmp"
    assert result.evidence["sysDescr"] == "Linux Router 5.10.0 #1 SMP"


# ---------------------------------------------------------------------------
# 8. ENTITY-MIB Unavailable -> Fallback to sysDescr/sysObjectID
# ---------------------------------------------------------------------------
@patch("services.interface_collection.snmp.snmp_available", return_value=True)
@patch("services.interface_collection.snmp.collect_snmp_inventory")
def test_snmp_entity_mib_unavailable(mock_collect, mock_available):
    # Device returns sys group but does NOT implement ENTITY-MIB
    mock_collect.return_value = SnmpDeviceInfo(
        sys_descr="Cisco Small Business SG300-28 28-Port Gigabit Managed Switch",
        sys_object_id="1.3.6.1.4.1.9.6.1.82.28.1",
        sys_name="SG300-28",
        sys_uptime="100 days",
        manufacturer="",  # ENTITY-MIB mfg empty
        model="",         # ENTITY-MIB model empty
        serial_number="", # ENTITY-MIB serial empty
        vendor_from_oid="Cisco",
    )

    identifier = SNMPIdentifier()
    device = _device_with_snmp(device_type="Switch")
    ctx = IdentificationContext(ip_address="192.168.1.103", existing=device)

    result = identifier.identify(ctx)

    assert result.success is True
    assert result.method == "snmp"
    assert result.evidence["sysObjectID"] == "1.3.6.1.4.1.9.6.1.82.28.1"
    assert result.evidence["vendor"] == "Cisco"


# ---------------------------------------------------------------------------
# 9. Partial response -> succeeds with available sysDescr
# ---------------------------------------------------------------------------
@patch("services.interface_collection.snmp.snmp_available", return_value=True)
@patch("services.interface_collection.snmp.collect_snmp_inventory")
def test_snmp_partial_response(mock_collect, mock_available):
    mock_collect.return_value = SnmpDeviceInfo(
        sys_descr="FortiGate-60F v7.2.4,build1396,230308 (GA.F64)",
        sys_object_id="1.3.6.1.4.1.388.1.1",
        sys_name="FG60F-MAIN",
        vendor_from_oid="Fortinet",
    )

    identifier = SNMPIdentifier()
    device = _device_with_snmp(device_type="Firewall")
    ctx = IdentificationContext(ip_address="192.168.1.104", existing=device)

    result = identifier.identify(ctx)

    assert result.success is True
    assert result.method == "snmp"
    assert result.evidence["sysDescr"] == "FortiGate-60F v7.2.4,build1396,230308 (GA.F64)"


# ---------------------------------------------------------------------------
# 10. SNMP failure -> Manager falls back to retained Nmap classification
# ---------------------------------------------------------------------------
@patch("services.interface_collection.snmp.snmp_available", return_value=True)
@patch("services.interface_collection.snmp.collect_snmp_inventory")
def test_snmp_failure_preserves_nmap_fallback(mock_collect, mock_available):
    mock_collect.side_effect = SNMPCollectorError("SNMP timeout or unreachable")

    nmap_mock = MagicMock(spec=NmapIdentifier)
    nmap_mock.supports.return_value = True
    nmap_mock.identify.return_value = IdentificationResult(
        success=True,
        method="nmap",
        device_type="Printer",
        confidence=80,
        evidence={"vendor": "HP", "os": "JetDirect"},
        metadata={"implemented": True, "source": "networkInfo", "canonicalType": "PRINTER"},
        classification=ClassificationResult(
            hostname="PRN-HP",
            vendor="HP",
            operating_system="JetDirect",
            device_type="Printer",
            confidence=80,
            classification_method="nmap-fingerprint",
            discovery_source="nmap",
            canonical_type="PRINTER",
        ),
    )

    manager = IdentificationManager({
        "snmp": SNMPIdentifier(),
        "nmap": nmap_mock,
    })

    device = _device_with_snmp(device_type="Printer")
    ctx = IdentificationContext(
        ip_address="192.168.1.105",
        preferred_device_type="Printer",
        existing=device,
        network_info={"os": {"name": "JetDirect"}, "ports": [9100], "services": ["pjl"]},
    )

    result = manager.identify(ctx)

    assert result.success is True
    assert result.method == "nmap"
    assert result.device_type == "Printer"
    assert result.metadata["plannedMethods"] == ["snmp", "nmap"]
    assert result.metadata["attemptedMethods"] == ["snmp", "nmap"]
