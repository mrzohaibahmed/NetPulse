"""
Unit & Integration Tests for Phase 6 — Asynchronous Multi-Network Enrichment Orchestration.

All tests use mocks — NO real network scanning or external calls performed.
"""

from __future__ import annotations

import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from bson import ObjectId

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Setup database mock before importing modules
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
_db_mod.NMAP_PATH = None
_db_mod.NMAP_TIMEOUT = 120
_db_mod.NMAP_CACHE_TTL = 21600
_db_mod.NMAP_QUICK_ARGUMENTS = "-F -sV -O --version-light"
_db_mod.NMAP_ARGUMENTS = "-sS -sV -O --version-light"

from services.discovery.apply import enrich_online_host
from services.discovery.enrichment import (
    DISCOVERY_STATUS_COMPLETED,
    DISCOVERY_STATUS_ENRICHING,
    DISCOVERY_STATUS_FAILED,
    DISCOVERY_STATUS_PENDING,
    _claim_enrichment,
    _run_discovery_enrichment,
    enqueue_batch_enrichment,
    enqueue_discovery_enrichment,
)
from services.discovery.identification import (
    IdentificationContext,
    IdentificationManager,
    IdentificationResult,
)


# ---------------------------------------------------------------------------
# 1. Fast Discovery & Immediate Device Creation
# ---------------------------------------------------------------------------
@patch("services.discovery.apply.enqueue_discovery_enrichment")
@patch("services.discovery.apply.create_device")
@patch("config.database.db.devices")
def test_fast_discovery_returns_immediately(mock_devices, mock_create, mock_enqueue):
    fake_id = ObjectId()
    mock_create.return_value = {
        "_id": fake_id,
        "hostname": "Unknown",
        "ipAddress": "192.168.1.100",
        "deviceType": "UNKNOWN",
        "status": "Online",
        "discoveryStatus": "pending",
    }
    mock_devices.insert_one.return_value = MagicMock(inserted_id=fake_id)

    ping_res = {"success": True, "status": "Online", "responseTime": 5.2}

    start = time.monotonic()
    result = enrich_online_host("192.168.1.100", ping_result=ping_res, existing=None)
    elapsed = time.monotonic() - start

    assert elapsed < 0.2  # Immediate non-blocking response
    assert result["ipAddress"] == "192.168.1.100"
    assert result["discoveryStatus"] == "pending"
    mock_enqueue.assert_called_once_with(fake_id, "192.168.1.100")


# ---------------------------------------------------------------------------
# 2. Immediate Device Creation
# ---------------------------------------------------------------------------
@patch("services.discovery.apply.enqueue_discovery_enrichment")
@patch("services.discovery.apply.create_device")
@patch("config.database.db.devices")
def test_immediate_device_creation(mock_devices, mock_create, mock_enqueue):
    fake_id = ObjectId()
    mock_create.return_value = {
        "_id": fake_id,
        "hostname": "Unknown",
        "ipAddress": "10.0.0.50",
        "deviceType": "UNKNOWN",
        "status": "Online",
        "discoveryStatus": "pending",
    }
    mock_devices.insert_one.return_value = MagicMock(inserted_id=fake_id)

    ping_res = {"success": True, "status": "Online", "responseTime": 3.1}
    res = enrich_online_host("10.0.0.50", ping_result=ping_res, existing=None)

    assert res["ipAddress"] == "10.0.0.50"
    assert res["status"] == "Online"
    assert res["deviceType"] == "UNKNOWN"
    assert res["discoveryStatus"] == "pending"


# ---------------------------------------------------------------------------
# 3. Duplicate Enrichment Prevention (Atomic Claiming)
# ---------------------------------------------------------------------------
@patch("config.database.db.devices")
def test_duplicate_enrichment_prevention(mock_devices):
    fake_id = ObjectId()

    # First claim succeeds
    mock_devices.find_one_and_update.side_effect = [
        {"_id": fake_id, "ipAddress": "192.168.1.5", "discoveryStatus": "enriching"},
        None,  # Second claim fails
    ]

    claim1 = _claim_enrichment(fake_id)
    claim2 = _claim_enrichment(fake_id)

    assert claim1 is not None
    assert claim2 is None


# ---------------------------------------------------------------------------
# 4. Multi-Network Coexistence & Acceptance Test (Network A, B, C)
# ---------------------------------------------------------------------------
import services.nmap_service  # noqa: F401


@patch("services.discovery.enrichment._nmap_semaphore")
@patch("services.nmap_service.scan_device_nmap")
@patch("services.discovery_service.get_hostname", return_value="host-dns.local")
@patch("config.database.db.devices")
def test_multi_network_coexistence_and_no_cancellation(
    mock_devices, mock_get_hostname, mock_scan_nmap, mock_semaphore
):
    mock_scan_nmap.return_value = {
        "hostname": "",
        "vendor": "Dell",
        "os": {"name": "Windows 11 Pro"},
        "ports": [{"port": 445, "state": "open", "service": "microsoft-ds"}],
        "services": ["microsoft-ds"],
    }

    processed_ids = set()

    def _mock_claim(query, *args, **kwargs):
        device_id = query.get("_id") if isinstance(query, dict) else query
        if device_id in processed_ids:
            return None
        processed_ids.add(device_id)
        return {"_id": device_id, "ipAddress": "192.168.1.1", "discoveryStatus": "enriching"}

    mock_devices.find_one_and_update.side_effect = _mock_claim
    mock_devices.find_one.return_value = {
        "_id": ObjectId(),
        "ipAddress": "192.168.1.1",
        "hostname": "Unknown",
    }

    # Simulate 3 networks queued concurrently
    net_a_items = [(ObjectId(), f"10.0.1.{i}") for i in range(10)]
    net_b_items = [(ObjectId(), f"10.0.2.{i}") for i in range(10)]
    net_c_items = [(ObjectId(), f"10.0.3.{i}") for i in range(10)]

    enqueue_batch_enrichment(net_a_items)
    enqueue_batch_enrichment(net_b_items)
    enqueue_batch_enrichment(net_c_items)

    time.sleep(0.5)  # Allow background threads to complete

    total_unique = len(processed_ids)
    assert total_unique == 30  # All 30 devices from networks A, B, C processed


# ---------------------------------------------------------------------------
# 5. Shared Bounded Worker Pool
# ---------------------------------------------------------------------------
def test_shared_bounded_worker_pool():
    from services.discovery.enrichment import _get_executor, _worker_count

    executor1 = _get_executor()
    executor2 = _get_executor()

    # Reuses the exact same singleton executor pool
    assert executor1 is executor2
    assert _worker_count() == 5


# ---------------------------------------------------------------------------
# 6. Protocol Routing: Windows PC -> WMI
# ---------------------------------------------------------------------------
def test_protocol_routing_windows_pc():
    manager = IdentificationManager()
    context = IdentificationContext(
        ip_address="192.168.1.20",
        preferred_device_type=None,
        network_info={
            "vendor": "Dell",
            "os": {"name": "Windows 11"},
            "ports": [{"port": 445, "state": "open"}],
            "services": ["microsoft-ds"],
        },
    )

    plan = manager.plan_methods(context)
    assert plan == ["windows", "nmap"]


# ---------------------------------------------------------------------------
# 7. Protocol Routing: Printer -> SNMP
# ---------------------------------------------------------------------------
def test_protocol_routing_printer():
    manager = IdentificationManager()
    context = IdentificationContext(
        ip_address="192.168.1.30",
        preferred_device_type=None,
        network_info={
            "vendor": "HP",
            "ports": [{"port": 9100, "state": "open"}],
            "services": ["jetdirect"],
        },
    )

    plan = manager.plan_methods(context)
    assert plan == ["snmp", "nmap"]


# ---------------------------------------------------------------------------
# 8. Protocol Routing: Camera -> ONVIF
# ---------------------------------------------------------------------------
def test_protocol_routing_camera():
    manager = IdentificationManager()
    context = IdentificationContext(
        ip_address="192.168.1.40",
        preferred_device_type=None,
        network_info={
            "vendor": "Hikvision",
            "ports": [{"port": 554, "state": "open"}],
            "services": ["rtsp"],
        },
    )

    plan = manager.plan_methods(context)
    assert plan == ["camera", "nmap"]


# ---------------------------------------------------------------------------
# 9. Protocol Routing: Switch / Router -> SNMP / SSH
# ---------------------------------------------------------------------------
def test_protocol_routing_switch():
    manager = IdentificationManager()
    context = IdentificationContext(
        ip_address="192.168.1.50",
        preferred_device_type=None,
        network_info={
            "vendor": "Cisco",
            "ports": [{"port": 161, "state": "open"}],
            "services": ["snmp"],
        },
    )

    plan = manager.plan_methods(context)
    assert plan == ["snmp", "ssh", "nmap"]


# ---------------------------------------------------------------------------
# 10. Failure Isolation: WMI Timeout retains Nmap fallback
# ---------------------------------------------------------------------------
import services.wmi_service  # noqa: F401


@patch("services.wmi_service.query_windows_device")
def test_failure_isolation_wmi_timeout(mock_wmi):
    from services.discovery.identification import WindowsIdentifier, NmapIdentifier

    mock_wmi.side_effect = Exception("WinRM connection timed out after 15s")

    nmap_mock = MagicMock(spec=NmapIdentifier)
    nmap_mock.supports.return_value = True
    nmap_mock.identify.return_value = IdentificationResult(
        success=True,
        method="nmap",
        device_type="Windows PC",
        confidence=75,
        evidence={"vendor": "Dell", "os": "Windows 10"},
        metadata={"implemented": True},
    )

    manager = IdentificationManager({
        "windows": WindowsIdentifier(),
        "nmap": nmap_mock,
    })

    ctx = IdentificationContext(
        ip_address="192.168.1.60",
        preferred_device_type="Windows PC",
        existing={
            "deviceType": "Windows PC",
            "credentials": {"winrmUsername": "Admin", "winrmPassword": "Pass"},
        },
    )

    result = manager.identify(ctx)

    assert result.success is True
    assert result.method == "nmap"
    assert result.metadata["attemptedMethods"] == ["windows", "nmap"]


# 11. Failure Isolation: SNMP Error retains Nmap fallback
@patch("services.interface_collection.snmp.collect_snmp_inventory")
def test_failure_isolation_snmp_error(mock_snmp):
    from services.discovery.identification import SNMPIdentifier, NmapIdentifier

    mock_snmp.side_effect = Exception("SNMP timeout")

    nmap_mock = MagicMock(spec=NmapIdentifier)
    nmap_mock.supports.return_value = True
    nmap_mock.identify.return_value = IdentificationResult(
        success=True,
        method="nmap",
        device_type="Printer",
        confidence=70,
        evidence={"vendor": "HP"},
        metadata={"implemented": True},
    )

    manager = IdentificationManager({
        "snmp": SNMPIdentifier(),
        "nmap": nmap_mock,
    })

    ctx = IdentificationContext(
        ip_address="192.168.1.70",
        preferred_device_type="Printer",
        existing={"deviceType": "Printer", "credentials": {"snmpCommunity": "public"}},
    )

    result = manager.identify(ctx)

    assert result.success is True
    assert result.method == "nmap"


# ---------------------------------------------------------------------------
# 12. 60-Second ICMP Monitoring Isolation
# ---------------------------------------------------------------------------
@patch("services.ping_service.ping", return_value=0.015)
def test_60s_monitoring_isolation(mock_ping):
    from services.ping_service import ping_device

    start = time.monotonic()
    res = ping_device("192.168.1.1")
    elapsed = time.monotonic() - start

    assert res["status"] == "Online"
    assert elapsed < 0.1
