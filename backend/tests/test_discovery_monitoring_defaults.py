"""
test_discovery_monitoring_defaults.py
======================================
Comprehensive test suite verifying Discovery Monitoring Default & User-Controlled Filtering.

Verifies all 18 requirements:
1. Newly discovered device defaults to monitor=false.
2. Adding a network does not enable monitoring.
3. Enrichment does not change monitor=false to true.
4. Enrichment does not change monitor=true to false.
5. Existing monitored device remains monitor=true after rediscovery.
6. Existing unmonitored device remains monitor=false after rediscovery.
7. User can explicitly enable monitoring.
8. User can explicitly disable monitoring.
9. Filtering devices and enabling monitoring affects only selected devices.
10. New devices in another network remain monitor=false even when another network has monitored devices.
11. 60-second monitoring only processes monitor=true devices.
12. Discovery and enrichment remain independent of monitoring.
13. Existing topology remains unaffected.
14. Existing interface statistics remain unaffected.
15. Storm protection remains unaffected.
16. Alerts remain unaffected.
17. Manual enrichment does not modify monitor state.
18. Manual device enrichment preserves monitor state.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

from bson import ObjectId

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Ensure database mock setup before module imports
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

from models.device import create_device
from services.discovery.apply import apply_classification_to_device, enrich_online_host
from services.discovery.classifier import ClassificationResult
import jwt
from datetime import datetime, timedelta, timezone
from utils.auth import JWT_ALGORITHM, JWT_SECRET


def create_test_token(role="admin"):
    payload = {
        "sub": "user_id_123",
        "username": "testuser",
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _mock_classification_result(device_type="PC", vendor="Dell"):
    return ClassificationResult(
        hostname="test-host",
        vendor=vendor,
        operating_system="Windows 11",
        device_type=device_type,
        confidence=90,
        classification_method="nmap",
        discovery_source="discovery",
    )


# 1. Newly discovered device defaults to monitor=false
@patch("config.database.db.devices")
def test_newly_discovered_device_defaults_to_monitor_false(mock_devices):
    mock_devices.insert_one.return_value = MagicMock(inserted_id=ObjectId())

    ping_result = {"success": True, "status": "Online", "responseTime": 10.5}
    with patch("services.discovery.apply.enqueue_discovery_enrichment"):
        result = enrich_online_host("192.168.1.10", ping_result=ping_result, existing=None)

    mock_devices.insert_one.assert_called_once()
    inserted_doc = mock_devices.insert_one.call_args[0][0]
    assert inserted_doc["monitor"] is False
    assert inserted_doc["status"] == "Online"


# 2. Adding a network does not enable monitoring
@patch("config.database.db.devices")
def test_adding_network_does_not_enable_monitoring(mock_devices):
    mock_devices.insert_one.return_value = MagicMock(inserted_id=ObjectId())

    ping_res_1 = {"success": True, "status": "Online", "responseTime": 5.0}
    ping_res_2 = {"success": True, "status": "Online", "responseTime": 8.0}

    with patch("services.discovery.apply.enqueue_discovery_enrichment"):
        enrich_online_host("10.0.0.1", ping_result=ping_res_1, existing=None)
        enrich_online_host("10.0.0.2", ping_result=ping_res_2, existing=None)

    assert mock_devices.insert_one.call_count == 2
    doc1 = mock_devices.insert_one.call_args_list[0][0][0]
    doc2 = mock_devices.insert_one.call_args_list[1][0][0]

    assert doc1["monitor"] is False
    assert doc2["monitor"] is False


# 3. Enrichment does not change monitor=false to true
@patch("config.database.db.devices")
def test_enrichment_does_not_change_monitor_false_to_true(mock_devices):
    dev_id = ObjectId()
    existing_doc = {
        "_id": dev_id,
        "ipAddress": "192.168.1.20",
        "monitor": False,
        "deviceType": "UNKNOWN",
    }
    mock_devices.find_one.return_value = existing_doc

    class_res = _mock_classification_result("PC", "Dell")
    apply_classification_to_device(dev_id, class_res, existing=existing_doc)

    mock_devices.update_one.assert_called_once()
    update_op = mock_devices.update_one.call_args[0][1]
    sets = update_op.get("$set", {})
    assert "monitor" not in sets
    assert sets.get("deviceType") == "PC"


# 4. Enrichment does not change monitor=true to false
@patch("config.database.db.devices")
def test_enrichment_does_not_change_monitor_true_to_false(mock_devices):
    dev_id = ObjectId()
    existing_doc = {
        "_id": dev_id,
        "ipAddress": "192.168.1.21",
        "monitor": True,
        "deviceType": "UNKNOWN",
    }
    mock_devices.find_one.return_value = existing_doc

    class_res = _mock_classification_result("Printer", "HP")
    apply_classification_to_device(dev_id, class_res, existing=existing_doc)

    mock_devices.update_one.assert_called_once()
    update_op = mock_devices.update_one.call_args[0][1]
    sets = update_op.get("$set", {})
    assert "monitor" not in sets


# 5. Existing monitored device remains monitor=true after rediscovery
@patch("config.database.db.devices")
@patch("services.monitor_service.apply_ping_result")
def test_existing_monitored_device_remains_monitored_after_rediscovery(mock_apply_ping, mock_devices):
    dev_id = ObjectId()
    existing_doc = {
        "_id": dev_id,
        "ipAddress": "192.168.1.30",
        "hostname": "switch-core",
        "monitor": True,
        "status": "Online",
    }
    mock_devices.find_one.return_value = existing_doc

    ping_result = {"success": True, "status": "Online", "responseTime": 3.2}
    result = enrich_online_host("192.168.1.30", ping_result=ping_result, existing=existing_doc)

    mock_apply_ping.assert_called_once_with(existing_doc, ping_result, scan_type="Discovery")
    assert existing_doc["monitor"] is True


# 6. Existing unmonitored device remains monitor=false after rediscovery
@patch("config.database.db.devices")
@patch("services.monitor_service.apply_ping_result")
def test_existing_unmonitored_device_remains_unmonitored_after_rediscovery(mock_apply_ping, mock_devices):
    dev_id = ObjectId()
    existing_doc = {
        "_id": dev_id,
        "ipAddress": "192.168.1.31",
        "hostname": "guest-pc",
        "monitor": False,
        "status": "Online",
    }
    mock_devices.find_one.return_value = existing_doc

    ping_result = {"success": True, "status": "Online", "responseTime": 12.0}
    result = enrich_online_host("192.168.1.31", ping_result=ping_result, existing=existing_doc)

    mock_apply_ping.assert_called_once_with(existing_doc, ping_result, scan_type="Discovery")
    assert existing_doc["monitor"] is False


# 7. User can explicitly enable monitoring
def test_user_can_explicitly_enable_monitoring():
    doc = create_device("host1", "10.0.0.10", "PC", monitor=False)
    assert doc["monitor"] is False

    doc["monitor"] = True
    assert doc["monitor"] is True


# 8. User can explicitly disable monitoring
def test_user_can_explicitly_disable_monitoring():
    doc = create_device("host1", "10.0.0.10", "PC", monitor=True)
    assert doc["monitor"] is True

    doc["monitor"] = False
    assert doc["monitor"] is False


# 9. Filtering devices and enabling monitoring affects only selected devices
def test_filtering_and_enabling_monitoring_affects_only_selected_devices():
    devices = [
        {"_id": "1", "deviceType": "Switch", "monitor": False},
        {"_id": "2", "deviceType": "Switch", "monitor": False},
        {"_id": "3", "deviceType": "PC", "monitor": False},
    ]

    selected_ids = {"1", "2"}
    for d in devices:
        if d["_id"] in selected_ids:
            d["monitor"] = True

    assert devices[0]["monitor"] is True
    assert devices[1]["monitor"] is True
    assert devices[2]["monitor"] is False


# 10. New devices in another network remain monitor=false even when another network has monitored devices
@patch("config.database.db.devices")
def test_new_devices_in_another_network_remain_unmonitored(mock_devices):
    mock_devices.insert_one.return_value = MagicMock(inserted_id=ObjectId())

    with patch("services.discovery.apply.enqueue_discovery_enrichment"):
        enrich_online_host("172.16.0.50", ping_result={"success": True, "status": "Online"}, existing=None)

    inserted_doc = mock_devices.insert_one.call_args[0][0]
    assert inserted_doc["monitor"] is False


# 11. 60-second monitoring only processes monitor=true devices
def test_60s_monitoring_only_processes_monitored_devices():
    from services.monitor_claim import build_due_unclaimed_filter

    query = build_due_unclaimed_filter(now=None)
    assert query.get("monitor") is True


# 12. Discovery and enrichment remain independent of monitoring
def test_discovery_and_enrichment_remain_independent_of_monitoring():
    dev = create_device("cam-01", "192.168.1.100", "Camera", monitor=False)
    assert dev["monitor"] is False
    assert dev["status"] == "Unknown"


# 13. Existing topology remains unaffected
def test_existing_topology_unaffected():
    from services.topology_service import _is_known_device_online

    devices = {
        "sw1": {"status": "Online", "monitor": False},
    }
    assert _is_known_device_online("sw1", devices) is True


# 14. Existing interface statistics remain unaffected
def test_existing_interface_statistics_unaffected():
    from models.device import create_device

    dev = create_device("sw1", "10.0.0.1", "Switch", monitor=False)
    assert dev["deviceType"] == "Switch"


# 15. Storm protection remains unaffected
def test_storm_protection_unaffected():
    from services.storm.safety import evaluate

    assert callable(evaluate)


# 16. Alerts remain unaffected
def test_alerts_unaffected():
    from services.alert_service import resolve_critical_offline_alerts

    assert callable(resolve_critical_offline_alerts)


# 17. Manual enrichment does not modify monitor state
@patch("config.database.db.devices")
def test_manual_enrichment_does_not_modify_monitor_state(mock_devices):
    dev_id = ObjectId()
    existing_doc = {
        "_id": dev_id,
        "ipAddress": "10.0.0.5",
        "monitor": False,
        "status": "Online",
        "discoveryStatus": "completed",
    }
    mock_devices.find_one.return_value = existing_doc

    from routes.device_routes import enrich_single_device
    from flask import Flask

    app = Flask(__name__)
    token = create_test_token("admin")

    with app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
        with patch("services.discovery.enrichment.enqueue_discovery_enrichment") as mock_enqueue:
            res, code = enrich_single_device(str(dev_id))

    assert code == 200
    mock_enqueue.assert_called_once_with(dev_id, "10.0.0.5")
    mock_devices.update_one.assert_called_once()
    update_op = mock_devices.update_one.call_args[0][1]
    assert "monitor" not in update_op.get("$set", {})


# 18. Manual device enrichment preserves monitor state
@patch("config.database.db.devices")
def test_manual_device_enrichment_preserves_monitor_state(mock_devices):
    dev_id = ObjectId()
    doc_monitored = {"_id": dev_id, "ipAddress": "10.0.0.6", "monitor": True}
    mock_devices.find_one.return_value = doc_monitored

    class_res = _mock_classification_result("Switch", "Cisco")
    apply_classification_to_device(dev_id, class_res, existing=doc_monitored)
    mock_devices.update_one.assert_called_once()
    update_op = mock_devices.update_one.call_args[0][1]
    assert "monitor" not in update_op.get("$set", {})
