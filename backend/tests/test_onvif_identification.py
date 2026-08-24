"""
Unit tests for Phase 5 — IP Camera ONVIF Identification.

All tests use mocks — NO real network/SOAP operations are performed.
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
    CameraIdentifier,
    IdentificationContext,
    IdentificationManager,
    IdentificationResult,
    NmapIdentifier,
)
from services.onvif_service import OnvifCollectorError, OnvifDeviceInfo


def _device_with_onvif(
    username: str = "admin",
    password: str = "CamPass123",
    port: int = 80,
    device_type: str = "IP Camera",
) -> dict:
    from utils.secret_crypto import encrypt_secret

    return {
        "deviceType": device_type,
        "credentials": {
            "onvifUsername": username,
            "onvifPassword": encrypt_secret(password),
            "onvifPort": port,
        },
    }


# ---------------------------------------------------------------------------
# 1. Successful ONVIF Camera Identification
# ---------------------------------------------------------------------------
@patch("services.onvif_service.query_onvif_device")
def test_onvif_successful_camera_identification(mock_query):
    mock_query.return_value = OnvifDeviceInfo(
        manufacturer="Hikvision",
        model="DS-2CD2143G0-I",
        firmware_version="V5.5.80",
        serial_number="DS-2CD2143G0-I20190101AAWR123456789W",
        hardware_id="NR-1.0",
        device_name="CAM-LOBBY-MAIN",
        onvif_port=80,
    )

    identifier = CameraIdentifier()
    device = _device_with_onvif()
    ctx = IdentificationContext(ip_address="192.168.1.80", existing=device)

    assert identifier.supports(ctx) is True
    result = identifier.identify(ctx)

    assert result.success is True
    assert result.method == "onvif"
    assert result.confidence == 96
    assert result.device_type == "IP Camera"
    assert result.evidence["manufacturer"] == "Hikvision"
    assert result.evidence["model"] == "DS-2CD2143G0-I"
    assert result.evidence["serialNumber"] == "DS-2CD2143G0-I20190101AAWR123456789W"
    assert result.evidence["firmwareRev"] == "V5.5.80"
    assert result.evidence["deviceName"] == "CAM-LOBBY-MAIN"
    assert result.evidence["onvifHardwareId"] == "NR-1.0"


# ---------------------------------------------------------------------------
# 2. Invalid Credentials / Auth Failure
# ---------------------------------------------------------------------------
@patch("services.onvif_service.query_onvif_device")
def test_onvif_invalid_credentials(mock_query):
    secret_pass = "BadPass999!"
    mock_query.side_effect = OnvifCollectorError(f"HTTP 401: Unauthorized for user admin with password {secret_pass}")

    identifier = CameraIdentifier()
    device = _device_with_onvif(password=secret_pass)
    ctx = IdentificationContext(ip_address="192.168.1.81", existing=device)

    result = identifier.identify(ctx)

    assert result.success is False
    assert result.method == "onvif"
    assert "401" in result.error
    # Ensure password is not exposed in error string
    assert secret_pass not in (result.error or "")


# ---------------------------------------------------------------------------
# 3. ONVIF Timeout
# ---------------------------------------------------------------------------
@patch("services.onvif_service.query_onvif_device")
def test_onvif_timeout(mock_query):
    mock_query.side_effect = OnvifCollectorError("ONVIF device service unreachable on 192.168.1.82: timed out")

    identifier = CameraIdentifier()
    device = _device_with_onvif()
    ctx = IdentificationContext(ip_address="192.168.1.82", existing=device)

    result = identifier.identify(ctx)

    assert result.success is False
    assert result.method == "onvif"
    assert "timed out" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# 4. Unsupported Device (Returns HTML / 404)
# ---------------------------------------------------------------------------
@patch("services.onvif_service.query_onvif_device")
def test_onvif_unsupported_device(mock_query):
    mock_query.side_effect = OnvifCollectorError("HTTP 404: Not Found")

    identifier = CameraIdentifier()
    device = _device_with_onvif()
    ctx = IdentificationContext(ip_address="192.168.1.83", existing=device)

    result = identifier.identify(ctx)

    assert result.success is False
    assert result.method == "onvif"
    assert "404" in result.error


# ---------------------------------------------------------------------------
# 5. Malformed Response
# ---------------------------------------------------------------------------
@patch("services.onvif_service.query_onvif_device")
def test_onvif_malformed_xml(mock_query):
    mock_query.side_effect = OnvifCollectorError("Request failed: syntax error in XML response")

    identifier = CameraIdentifier()
    device = _device_with_onvif()
    ctx = IdentificationContext(ip_address="192.168.1.84", existing=device)

    result = identifier.identify(ctx)

    assert result.success is False
    assert result.method == "onvif"
    assert "syntax error" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# 6. Camera with Missing Model
# ---------------------------------------------------------------------------
@patch("services.onvif_service.query_onvif_device")
def test_onvif_camera_missing_model(mock_query):
    mock_query.return_value = OnvifDeviceInfo(
        manufacturer="Axis Communications",
        model="",  # missing model
        firmware_version="9.80.1",
        serial_number="ACC12345",
        device_name="CAM-PARKING-01",
    )

    identifier = CameraIdentifier()
    device = _device_with_onvif()
    ctx = IdentificationContext(ip_address="192.168.1.85", existing=device)

    result = identifier.identify(ctx)

    assert result.success is True
    assert result.evidence["manufacturer"] == "Axis Communications"
    assert "model" not in result.evidence
    assert result.evidence["serialNumber"] == "ACC12345"


# ---------------------------------------------------------------------------
# 7. Camera with Missing Serial
# ---------------------------------------------------------------------------
@patch("services.onvif_service.query_onvif_device")
def test_onvif_camera_missing_serial(mock_query):
    mock_query.return_value = OnvifDeviceInfo(
        manufacturer="Dahua",
        model="IPC-HDBW2431E-S2",
        firmware_version="2.800.0000000.10.R",
        serial_number="",  # missing serial
        device_name="CAM-GATE-01",
    )

    identifier = CameraIdentifier()
    device = _device_with_onvif()
    ctx = IdentificationContext(ip_address="192.168.1.86", existing=device)

    result = identifier.identify(ctx)

    assert result.success is True
    assert result.evidence["manufacturer"] == "Dahua"
    assert result.evidence["model"] == "IPC-HDBW2431E-S2"
    assert "serialNumber" not in result.evidence


# ---------------------------------------------------------------------------
# 8. ONVIF Failure -> Manager Falls Back to Retained Nmap Classification
# ---------------------------------------------------------------------------
@patch("services.onvif_service.query_onvif_device")
def test_onvif_failure_preserves_nmap_fallback(mock_query):
    mock_query.side_effect = OnvifCollectorError("ONVIF timeout on 192.168.1.87")

    nmap_mock = MagicMock(spec=NmapIdentifier)
    nmap_mock.supports.return_value = True
    nmap_mock.identify.return_value = IdentificationResult(
        success=True,
        method="nmap",
        device_type="IP Camera",
        confidence=70,
        evidence={"vendor": "Hikvision", "os": "Linux 3.x"},
        metadata={"implemented": True, "source": "networkInfo", "canonicalType": "CAMERA"},
        classification=ClassificationResult(
            hostname="CAM-87",
            vendor="Hikvision",
            operating_system="Linux 3.x",
            device_type="IP Camera",
            confidence=70,
            classification_method="nmap-fingerprint",
            discovery_source="nmap",
            canonical_type="CAMERA",
        ),
    )

    manager = IdentificationManager({
        "camera": CameraIdentifier(),
        "nmap": nmap_mock,
    })

    device = _device_with_onvif()
    ctx = IdentificationContext(
        ip_address="192.168.1.87",
        preferred_device_type="IP Camera",
        existing=device,
        network_info={"os": {"name": "Linux 3.x"}, "ports": [554], "services": ["rtsp"]},
    )

    result = manager.identify(ctx)

    assert result.success is True
    assert result.method == "nmap"
    assert result.device_type == "IP Camera"
    assert result.metadata["plannedMethods"] == ["camera", "nmap"]
    assert result.metadata["attemptedMethods"] == ["camera", "nmap"]


# ---------------------------------------------------------------------------
# 9. Non-Camera Device (Port 80 alone does NOT trigger camera identification)
# ---------------------------------------------------------------------------
def test_onvif_non_camera_device():
    identifier = CameraIdentifier()

    # Generic web server with port 80 open (no RTSP, no camera vendor, no camera hostname)
    web_server_context = IdentificationContext(
        ip_address="192.168.1.200",
        preferred_device_type="Linux Server",
        existing={"deviceType": "Linux Server"},
        network_info={
            "vendor": "Dell",
            "hostname": "web-01",
            "ports": [{"port": 80, "state": "open", "service": "http"}],
            "services": ["http"],
        },
    )

    assert identifier.supports(web_server_context) is False


# ---------------------------------------------------------------------------
# 10. Concurrent Camera Identification
# ---------------------------------------------------------------------------
@patch("services.onvif_service.query_onvif_device")
def test_onvif_concurrent_devices(mock_query):
    def _mock_query(ip, *args, **kwargs):
        return OnvifDeviceInfo(
            manufacturer="Uniview",
            model="IPC3614SR3-DPF28M",
            firmware_version="3.2.1",
            serial_number=f"SN-{ip}",
            device_name=f"CAM-{ip}",
        )

    mock_query.side_effect = _mock_query

    identifier = CameraIdentifier()
    ips = [f"192.168.1.{120 + i}" for i in range(5)]

    def _worker(ip: str):
        device = _device_with_onvif()
        ctx = IdentificationContext(ip_address=ip, existing=device)
        return identifier.identify(ctx)

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(_worker, ips))

    assert len(results) == 5
    for i, res in enumerate(results):
        assert res.success is True
        assert res.method == "onvif"
        assert res.evidence["serialNumber"] == f"SN-{ips[i]}"
