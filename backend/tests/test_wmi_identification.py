"""
Unit tests for Phase 3 — Windows PC WMI/WinRM Identification.

All tests use mocks — NO real network access is performed.
"""

from __future__ import annotations

import sys
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Mock database module if not present
if "config.database" not in sys.modules:
    _db_mod = types.ModuleType("config.database")
    _db_mod.db = MagicMock()
    _db_mod.MAX_SCAN_THREADS = 5
    _db_mod.WMI_TIMEOUT = 15
    sys.modules["config.database"] = _db_mod

from services.discovery.classifier import ClassificationResult
from services.discovery.identification import (
    IdentificationContext,
    IdentificationManager,
    IdentificationResult,
    NmapIdentifier,
    WindowsIdentifier,
)
from services.wmi_service import WmiDeviceInfo, has_winrm_credentials


def _device_with_winrm(
    username: str = "AdminUser",
    password: str = "SecretPass123",
    port: int = 5985,
    ssl: bool = False,
    device_type: str = "Windows PC",
) -> dict:
    from utils.secret_crypto import encrypt_secret

    return {
        "deviceType": device_type,
        "credentials": {
            "winrmUsername": username,
            "winrmPassword": encrypt_secret(password),
            "winrmPort": port,
            "winrmUseSsl": ssl,
        },
    }


# ---------------------------------------------------------------------------
# 1. Successful Windows PC identification
# ---------------------------------------------------------------------------
@patch("services.wmi_service._WINRM_AVAILABLE", True)
@patch("services.wmi_service.query_windows_device")
def test_wmi_successful_pc_identification(mock_query):
    mock_query.return_value = WmiDeviceInfo(
        hostname="DESKTOP-ABC123",
        manufacturer="Dell Inc.",
        model="OptiPlex 7090",
        serial_number="7X89Y02",
        operating_system="Microsoft Windows 11 Pro",
        os_version="10.0.22631",
        os_build="22631",
        system_uuid="4C4C4544-0058-3810-8039-B2C04F303232",
        cpu="11th Gen Intel(R) Core(TM) i7-11700 @ 2.50GHz",
        total_ram_gb=16.0,
    )

    identifier = WindowsIdentifier()
    device = _device_with_winrm()
    ctx = IdentificationContext(ip_address="192.168.1.50", existing=device)

    assert identifier.supports(ctx) is True
    result = identifier.identify(ctx)

    assert result.success is True
    assert result.method == "wmi"
    assert result.confidence == 97
    assert result.device_type == "Windows PC"
    assert result.evidence["manufacturer"] == "Dell Inc."
    assert result.evidence["model"] == "OptiPlex 7090"
    assert result.evidence["serialNumber"] == "7X89Y02"
    assert result.evidence["osVersion"] == "10.0.22631"
    assert result.evidence["osBuild"] == "22631"
    assert result.evidence["systemUuid"] == "4C4C4544-0058-3810-8039-B2C04F303232"
    assert result.evidence["cpu"] == "11th Gen Intel(R) Core(TM) i7-11700 @ 2.50GHz"
    assert result.evidence["totalRamGb"] == 16.0


# ---------------------------------------------------------------------------
# 2. Invalid credentials
# ---------------------------------------------------------------------------
@patch("services.wmi_service._WINRM_AVAILABLE", True)
@patch("services.wmi_service.query_windows_device")
def test_wmi_invalid_credentials(mock_query):
    mock_query.side_effect = ConnectionError("[WMI] Authentication failed for AdminUser")

    identifier = WindowsIdentifier()
    device = _device_with_winrm(password="BadPassword")
    ctx = IdentificationContext(ip_address="192.168.1.51", existing=device)

    result = identifier.identify(ctx)

    assert result.success is False
    assert result.method == "wmi"
    assert "Authentication failed" in result.error
    # Ensure password is not exposed in error string
    assert "BadPassword" not in (result.error or "")


# ---------------------------------------------------------------------------
# 3. WinRM unavailable (connection refused or port closed)
# ---------------------------------------------------------------------------
@patch("services.wmi_service._WINRM_AVAILABLE", True)
@patch("services.wmi_service.query_windows_device")
def test_wmi_winrm_unavailable(mock_query):
    mock_query.side_effect = ConnectionError("WinRM HTTP transport failed: connection refused")

    identifier = WindowsIdentifier()
    device = _device_with_winrm()
    ctx = IdentificationContext(ip_address="192.168.1.52", existing=device)

    result = identifier.identify(ctx)

    assert result.success is False
    assert result.method == "wmi"
    assert "connection refused" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# 4. Timeout
# ---------------------------------------------------------------------------
@patch("services.wmi_service._WINRM_AVAILABLE", True)
@patch("services.wmi_service.query_windows_device")
def test_wmi_timeout(mock_query):
    mock_query.side_effect = ConnectionError("WinRM operation timed out after 15 seconds")

    identifier = WindowsIdentifier()
    device = _device_with_winrm()
    ctx = IdentificationContext(ip_address="192.168.1.53", existing=device)

    result = identifier.identify(ctx)

    assert result.success is False
    assert result.method == "wmi"
    assert "timed out" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# 5. Missing serial number
# ---------------------------------------------------------------------------
@patch("services.wmi_service._WINRM_AVAILABLE", True)
@patch("services.wmi_service.query_windows_device")
def test_wmi_missing_serial(mock_query):
    mock_query.return_value = WmiDeviceInfo(
        hostname="DESKTOP-NOSERIAL",
        manufacturer="Lenovo",
        model="ThinkCentre M70q",
        serial_number="",  # empty serial
        operating_system="Microsoft Windows 10 Pro",
        os_version="10.0.19045",
        os_build="19045",
    )

    identifier = WindowsIdentifier()
    device = _device_with_winrm()
    ctx = IdentificationContext(ip_address="192.168.1.54", existing=device)

    result = identifier.identify(ctx)

    assert result.success is True
    assert result.evidence["manufacturer"] == "Lenovo"
    assert result.evidence["model"] == "ThinkCentre M70q"
    assert "serialNumber" not in result.evidence  # clean omission


# ---------------------------------------------------------------------------
# 6. Missing model
# ---------------------------------------------------------------------------
@patch("services.wmi_service._WINRM_AVAILABLE", True)
@patch("services.wmi_service.query_windows_device")
def test_wmi_missing_model(mock_query):
    mock_query.return_value = WmiDeviceInfo(
        hostname="CUSTOM-PC",
        manufacturer="Custom System",
        model="",  # empty model
        serial_number="SYS123",
        operating_system="Microsoft Windows 11 Enterprise",
        os_version="10.0.22631",
    )

    identifier = WindowsIdentifier()
    device = _device_with_winrm()
    ctx = IdentificationContext(ip_address="192.168.1.55", existing=device)

    result = identifier.identify(ctx)

    assert result.success is True
    assert result.evidence["manufacturer"] == "Custom System"
    assert "model" not in result.evidence
    assert result.evidence["serialNumber"] == "SYS123"


# ---------------------------------------------------------------------------
# 7. Windows Server classification
# ---------------------------------------------------------------------------
@patch("services.wmi_service._WINRM_AVAILABLE", True)
@patch("services.wmi_service.query_windows_device")
def test_wmi_windows_server(mock_query):
    mock_query.return_value = WmiDeviceInfo(
        hostname="WIN-SERVER-2022",
        manufacturer="Hewlett-Packard",
        model="ProLiant DL380 Gen10",
        serial_number="USE123456",
        operating_system="Microsoft Windows Server 2022 Datacenter",
        os_version="10.0.20348",
        os_build="20348",
    )

    identifier = WindowsIdentifier()
    device = _device_with_winrm(device_type="Server")
    ctx = IdentificationContext(ip_address="192.168.1.100", existing=device)

    result = identifier.identify(ctx)

    assert result.success is True
    assert result.device_type == "Server"
    assert result.metadata["canonicalType"] == "SERVER"
    assert result.evidence["os"] == "Microsoft Windows Server 2022 Datacenter"


# ---------------------------------------------------------------------------
# 8. Non-Windows device / missing credentials -> supports() returns False
# ---------------------------------------------------------------------------
def test_wmi_skipped_for_non_windows_or_no_credentials():
    identifier = WindowsIdentifier()

    # Printer device with no WinRM credentials
    printer_device = {"deviceType": "Printer", "credentials": {}}
    ctx1 = IdentificationContext(ip_address="192.168.1.200", existing=printer_device)

    # Cisco switch with only SSH credentials
    switch_device = {
        "deviceType": "Switch",
        "credentials": {"sshUsername": "admin", "sshPassword": "enc_password"},
    }
    ctx2 = IdentificationContext(ip_address="192.168.1.201", existing=switch_device)

    assert identifier.supports(ctx1) is False
    assert identifier.supports(ctx2) is False


# ---------------------------------------------------------------------------
# 9. WMI failure -> Nmap fallback retained
# ---------------------------------------------------------------------------
@patch("services.wmi_service._WINRM_AVAILABLE", True)
@patch("services.wmi_service.query_windows_device")
def test_wmi_failure_preserves_nmap_fallback(mock_query):
    # WMI fails with connection error
    mock_query.side_effect = ConnectionError("WinRM port 5985 unreachable")

    nmap_mock = MagicMock(spec=NmapIdentifier)
    nmap_mock.supports.return_value = True
    nmap_mock.identify.return_value = IdentificationResult(
        success=True,
        method="nmap",
        device_type="Windows PC",
        confidence=75,
        evidence={"os": "Windows 10", "vendor": "Dell"},
        metadata={"implemented": True, "source": "networkInfo", "canonicalType": "PC"},
        classification=ClassificationResult(
            hostname="DESKTOP-TEST",
            vendor="Dell",
            operating_system="Windows 10",
            device_type="Windows PC",
            confidence=75,
            classification_method="nmap-os-fingerprint",
            discovery_source="nmap",
            canonical_type="PC",
        ),
    )

    manager = IdentificationManager({
        "windows": WindowsIdentifier(),
        "nmap": nmap_mock,
    })

    device = _device_with_winrm()
    ctx = IdentificationContext(
        ip_address="192.168.1.60",
        preferred_device_type="Windows PC",
        existing=device,
        network_info={"os": {"name": "Windows 10"}, "ports": [], "services": []},
    )

    result = manager.identify(ctx)

    # First attempted windows, failed, fell back to nmap, succeeded
    assert result.success is True
    assert result.method == "nmap"
    assert result.device_type == "Windows PC"
    assert result.confidence == 75
    assert result.metadata["attemptedMethods"] == ["windows", "nmap"]
    assert result.metadata["plannedMethods"] == ["windows", "nmap"]


# ---------------------------------------------------------------------------
# 10. Multiple concurrent devices
# ---------------------------------------------------------------------------
@patch("services.wmi_service._WINRM_AVAILABLE", True)
@patch("services.wmi_service.query_windows_device")
def test_wmi_concurrent_devices(mock_query):
    def _mock_query(ip, *args, **kwargs):
        return WmiDeviceInfo(
            hostname=f"PC-{ip.replace('.', '-')}",
            manufacturer="HP",
            model="EliteDesk 800",
            serial_number=f"SN-{ip}",
            operating_system="Microsoft Windows 11 Pro",
        )

    mock_query.side_effect = _mock_query

    identifier = WindowsIdentifier()
    ips = [f"192.168.1.{10 + i}" for i in range(10)]

    def _worker(ip: str):
        device = _device_with_winrm()
        ctx = IdentificationContext(ip_address=ip, existing=device)
        return identifier.identify(ctx)

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(_worker, ips))

    assert len(results) == 10
    for i, res in enumerate(results):
        assert res.success is True
        assert res.method == "wmi"
        assert res.evidence["serialNumber"] == f"SN-{ips[i]}"


# ---------------------------------------------------------------------------
# 11. Security check: Credentials not logged or returned in error
# ---------------------------------------------------------------------------
@patch("services.wmi_service._WINRM_AVAILABLE", True)
@patch("services.wmi_service.query_windows_device")
def test_wmi_credentials_not_exposed(mock_query, caplog):
    secret_pass = "SuperSecretP@ssw0rd99!"
    mock_query.side_effect = Exception(f"Failed to authenticate user DomainUser with pass {secret_pass}")

    identifier = WindowsIdentifier()
    device = _device_with_winrm(username="DomainUser", password=secret_pass)
    ctx = IdentificationContext(ip_address="192.168.1.70", existing=device)

    result = identifier.identify(ctx)

    assert result.success is False
    # Username redacted in error string if present
    assert secret_pass not in (result.error or "")
    assert secret_pass not in caplog.text


# ---------------------------------------------------------------------------
# 12. Pywinrm missing handling
# ---------------------------------------------------------------------------
@patch("services.wmi_service._WINRM_AVAILABLE", False)
def test_wmi_pywinrm_missing():
    identifier = WindowsIdentifier()
    device = _device_with_winrm()
    ctx = IdentificationContext(ip_address="192.168.1.80", existing=device)

    result = identifier.identify(ctx)

    assert result.success is False
    assert "pywinrm is not installed" in (result.error or "")
