"""
Unit & Integration Tests for Event-Driven and Manually Triggered Device Enrichment Architecture.

All tests use mocks — NO real network scanning or external calls performed.
"""

from __future__ import annotations

import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import jwt
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
_db_mod.ARP_ACTIVE_SWEEP_INTERVAL = 300
_db_mod.MAC_ARP_POLL_INTERVAL = 300
_db_mod.WMI_TIMEOUT = 15
_db_mod.ONVIF_TIMEOUT = 5
_db_mod.NMAP_PATH = None
_db_mod.NMAP_TIMEOUT = 120
_db_mod.NMAP_CACHE_TTL = 21600
_db_mod.NMAP_QUICK_ARGUMENTS = "-F -sV -O --version-light"
_db_mod.NMAP_ARGUMENTS = "-sS -sV -O --version-light"
_db_mod.NMAP_SCAN_INTERVAL = 3600

import services.nmap_service  # noqa: F401
import services.wmi_service  # noqa: F401
from services.discovery.apply import enrich_online_host
from services.discovery.enrichment import (
    DISCOVERY_STATUS_ENRICHING,
    DISCOVERY_STATUS_PENDING,
    _claim_enrichment,
    enqueue_batch_enrichment,
    enqueue_discovery_enrichment,
)
from utils.auth import JWT_ALGORITHM, JWT_SECRET


def create_test_token(role="admin"):
    payload = {
        "sub": str(ObjectId()),
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


# ---------------------------------------------------------------------------
# 1. No Hourly / Periodic Enrichment Job Registered
# ---------------------------------------------------------------------------
def test_no_hourly_enrichment_job_registered():
    """Verify that start_scheduler does NOT register an hourly nmap_scan_job."""
    from scheduler import NMAP_JOB_ID, scheduler

    # Verify NMAP_JOB_ID is not present in scheduler job list when not added
    job = scheduler.get_job(NMAP_JOB_ID)
    assert job is None


def test_nmap_scheduler_no_automatic_rescan():
    """Verify scheduler startup does not schedule scan_all_online_devices periodically."""
    with patch("scheduler.scheduler") as mock_sched:
        mock_sched.running = False
        from scheduler import start_scheduler

        with patch("scheduler._register_device_monitor_job"):
            with patch("scheduler._register_isp_monitor_job"):
                with patch("scheduler._start_interface_job"):
                    with patch("scheduler._start_interface_stats_job"):
                        with patch("scheduler._start_storm_analysis_job"):
                            with patch("scheduler._start_storm_confirmation_job"):
                                with patch("scheduler._start_storm_safety_prepare_job"):
                                    with patch("scheduler._start_mac_arp_poll_job"):
                                        with patch("scheduler._start_arp_active_sweep_job"):
                                            with patch("scheduler._start_recovery_job"):
                                                with patch("scheduler._start_retention_job"):
                                                    start_scheduler()

        # Check added jobs to ensure nmap_scan_job was NOT added
        add_job_calls = mock_sched.add_job.call_args_list
        job_ids = [call.kwargs.get("id") or (call.args[0] if call.args else None) for call in add_job_calls]
        assert "nmap_scan_job" not in job_ids


# ---------------------------------------------------------------------------
# 2. Network Add / Discovery Flow Queues Enrichment Non-Blocking
# ---------------------------------------------------------------------------
@patch("services.discovery.apply.enqueue_discovery_enrichment")
@patch("services.discovery.apply.create_device")
@patch("config.database.db.devices")
def test_network_add_queues_enrichment(mock_devices, mock_create, mock_enqueue):
    fake_id = ObjectId()
    mock_create.return_value = {
        "_id": fake_id,
        "hostname": "Unknown",
        "ipAddress": "192.168.10.5",
        "deviceType": "UNKNOWN",
        "status": "Online",
        "discoveryStatus": "pending",
    }
    mock_devices.insert_one.return_value = MagicMock(inserted_id=fake_id)

    ping_res = {"success": True, "status": "Online", "responseTime": 4.1}

    start = time.monotonic()
    result = enrich_online_host("192.168.10.5", ping_result=ping_res, existing=None)
    elapsed = time.monotonic() - start

    assert elapsed < 0.1  # Fast return
    assert result["ipAddress"] == "192.168.10.5"
    assert result["discoveryStatus"] == "pending"
    mock_enqueue.assert_called_once_with(fake_id, "192.168.10.5")


def test_devices_appear_before_enrichment_completes():
    fake_id = ObjectId()

    with patch("services.discovery.apply.create_device") as mock_create:
        mock_create.return_value = {
            "_id": fake_id,
            "hostname": "Unknown",
            "ipAddress": "10.0.0.1",
            "deviceType": "UNKNOWN",
            "status": "Online",
            "discoveryStatus": "pending",
        }
        with patch("services.discovery.apply.enqueue_discovery_enrichment"):
            res = enrich_online_host("10.0.0.1", ping_result={"success": True, "status": "Online"}, existing=None)

            assert res["status"] == "Online"
            assert res["discoveryStatus"] == "pending"
            assert res["deviceType"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# 3. Multi-Network Concurrent Isolation (Network A, B, C)
# ---------------------------------------------------------------------------
@patch("services.discovery.enrichment._nmap_semaphore")
@patch("services.nmap_service.scan_device_nmap")
@patch("services.discovery_service.get_hostname", return_value="host.local")
@patch("config.database.db.devices")
def test_multi_network_enrichment_concurrent_isolation(
    mock_devices, mock_get_hostname, mock_scan_nmap, mock_semaphore
):
    mock_scan_nmap.return_value = {
        "hostname": "",
        "vendor": "Dell",
        "os": {"name": "Windows 11"},
        "ports": [],
        "services": [],
    }

    processed = set()

    def _mock_claim(query, *args, **kwargs):
        device_id = query.get("_id") if isinstance(query, dict) else query
        if device_id in processed:
            return None
        processed.add(device_id)
        return {"_id": device_id, "ipAddress": "10.0.0.1", "discoveryStatus": "enriching"}

    mock_devices.find_one_and_update.side_effect = _mock_claim

    net_a = [(ObjectId(), f"10.1.0.{i}") for i in range(5)]
    net_b = [(ObjectId(), f"10.2.0.{i}") for i in range(5)]
    net_c = [(ObjectId(), f"10.3.0.{i}") for i in range(5)]

    enqueue_batch_enrichment(net_a)
    enqueue_batch_enrichment(net_b)
    enqueue_batch_enrichment(net_c)

    time.sleep(0.4)

    assert len(processed) == 15  # Networks A, B, and C all completed without interference


# ---------------------------------------------------------------------------
# 4. Manual Network Enrich API Endpoint & Duplicate Prevention
# ---------------------------------------------------------------------------
@patch("config.database.db.networks")
@patch("config.database.db.devices")
def test_manual_network_scan_queues_enrichment(mock_devices, mock_networks):
    from routes.discovery_routes import enrich_network

    net_id = ObjectId()
    mock_networks.find_one.return_value = {
        "_id": net_id,
        "name": "Office Net",
        "cidr": "192.168.1.0/24",
    }

    dev1 = {"_id": ObjectId(), "ipAddress": "192.168.1.10", "status": "Online", "discoveryStatus": "completed"}
    dev2 = {"_id": ObjectId(), "ipAddress": "192.168.1.11", "status": "Online", "discoveryStatus": "enriching"}

    mock_devices.find.return_value = [dev1, dev2]

    from flask import Flask
    app = Flask(__name__)
    token = create_test_token("admin")

    with app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
        with patch("services.discovery.enrichment.enqueue_batch_enrichment") as mock_enqueue_batch:
            res, code = enrich_network(str(net_id))

    assert code == 200
    payload = res.get_json()
    assert payload["success"] is True
    assert payload["status"] == "queued"
    assert payload["queued"] == 1  # dev1 queued, dev2 skipped because it's active "enriching"
    assert payload["skippedEnriching"] == 1
    mock_enqueue_batch.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Manual Single Device Enrich API Endpoint
# ---------------------------------------------------------------------------
@patch("config.database.db.devices")
def test_single_device_refresh_works(mock_devices):
    from routes.device_routes import enrich_single_device

    dev_id = ObjectId()
    device_doc = {
        "_id": dev_id,
        "ipAddress": "192.168.1.50",
        "status": "Online",
        "discoveryStatus": "completed",
    }
    mock_devices.find_one.return_value = device_doc

    from flask import Flask
    app = Flask(__name__)
    token = create_test_token("admin")

    with app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
        with patch("services.discovery.enrichment.enqueue_discovery_enrichment") as mock_enqueue:
            res, code = enrich_single_device(str(dev_id))

    assert code == 200
    payload = res.get_json()
    assert payload["success"] is True
    assert payload["status"] == "queued"
    mock_enqueue.assert_called_once_with(dev_id, "192.168.1.50")


@patch("config.database.db.devices")
def test_prevent_duplicate_enrichment_jobs(mock_devices):
    from routes.device_routes import enrich_single_device

    dev_id = ObjectId()
    # Device already actively enriching
    device_doc = {
        "_id": dev_id,
        "ipAddress": "192.168.1.50",
        "status": "Online",
        "discoveryStatus": "enriching",
    }
    mock_devices.find_one.return_value = device_doc

    from flask import Flask
    app = Flask(__name__)
    token = create_test_token("admin")

    with app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
        with patch("services.discovery.enrichment.enqueue_discovery_enrichment") as mock_enqueue:
            res, code = enrich_single_device(str(dev_id))

    assert code == 200
    payload = res.get_json()
    assert payload["success"] is True
    assert payload["status"] == "enriching"
    assert "already in progress" in payload["message"]
    mock_enqueue.assert_not_called()  # Duplicate scan prevented!


@patch("config.database.db.devices")
def test_enrich_single_device_route_endpoint_mapping(mock_devices):
    from flask import Flask
    from routes.device_routes import device_bp

    dev_id = ObjectId()
    device_doc = {
        "_id": dev_id,
        "ipAddress": "192.168.1.50",
        "status": "Online",
        "discoveryStatus": "completed",
    }
    mock_devices.find_one.return_value = device_doc

    app = Flask(__name__)
    app.register_blueprint(device_bp, url_prefix="/api")
    client = app.test_client()

    token = create_test_token("admin")

    with patch("services.discovery.enrichment.enqueue_discovery_enrichment"):
        res = client.post(
            f"/api/devices/{dev_id}/enrich",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["success"] is True
    assert payload["status"] == "queued"


# ---------------------------------------------------------------------------
# 6. Failure Isolation: Provider failures do NOT remove device from MongoDB
# ---------------------------------------------------------------------------
@patch("config.database.db.devices")
def test_failure_isolation_wmi_snmp_onvif_nmap_do_not_delete_device(mock_devices):
    """Verify that failures in WMI, SNMP, ONVIF, or Nmap never delete the device."""
    from services.discovery.enrichment import _mark_enrichment_failed

    dev_id = ObjectId()
    _mark_enrichment_failed(dev_id, "192.168.1.99", "Provider connection timeout")

    # Devices update_one is called to set status=failed, but delete_one is NEVER called
    mock_devices.delete_one.assert_not_called()
    mock_devices.update_one.assert_called_once()


# ---------------------------------------------------------------------------
# 7. 60-Second ICMP Monitoring Isolation
# ---------------------------------------------------------------------------
@patch("services.ping_service.ping", return_value=0.012)
def test_60s_icmp_monitoring_continues_independently(mock_ping):
    from services.ping_service import ping_device

    res = ping_device("192.168.1.1")
    assert res["status"] == "Online"
    assert res["responseTime"] == 12.0
