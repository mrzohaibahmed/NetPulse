"""Async discovery enrichment: insert before Nmap, background classification."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_mock_db_module = MagicMock()
_mock_db_module.db = MagicMock()
_mock_db_module.MAX_SCAN_THREADS = 5
sys.modules.setdefault("config.database", _mock_db_module)

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from services.discovery import apply as apply_mod
from services.discovery.classifier import ClassificationResult
from services.discovery.enrichment import (
    DISCOVERY_STATUS_COMPLETED,
    DISCOVERY_STATUS_FAILED,
    DISCOVERY_STATUS_PENDING,
    _run_discovery_enrichment,
    shutdown_discovery_enrichment_executor,
)


def teardown_function():
    shutdown_discovery_enrichment_executor()


def test_new_device_inserts_before_nmap_and_queues_enrichment():
    ping_result = {
        "responseTime": 8.0,
        "lastSeen": "2026-08-07T00:00:00Z",
    }
    inserted_id = ObjectId()

    with patch.object(apply_mod, "db") as mock_db:
        mock_db.devices.insert_one.return_value = MagicMock(inserted_id=inserted_id)

        with (
            patch("services.nmap_service.scan_device_nmap") as nmap_fn,
            patch.object(apply_mod, "classify_network_info") as classify_fn,
            patch(
                "services.discovery.apply.enqueue_discovery_enrichment",
            ) as enqueue_fn,
        ):
            started = time.monotonic()
            result = apply_mod.enrich_online_host(
                "192.168.1.99",
                ping_result=ping_result,
                existing=None,
            )
            elapsed = time.monotonic() - started

            nmap_fn.assert_not_called()
            classify_fn.assert_not_called()
            mock_db.devices.insert_one.assert_called_once()
            enqueue_fn.assert_called_once_with(inserted_id, "192.168.1.99")

            insert_doc = mock_db.devices.insert_one.call_args[0][0]
            assert insert_doc["discoveryStatus"] == DISCOVERY_STATUS_PENDING
            assert insert_doc["status"] == "Online"
            assert insert_doc["hostname"] == "Unknown"
            assert insert_doc["deviceType"] == "Unknown Device"

    assert elapsed < 1.0
    assert result["saved"] is True
    assert result["discoveryStatus"] == DISCOVERY_STATUS_PENDING
    assert result["nmapError"] is None


def test_background_enrichment_updates_device_on_nmap_success():
    device_id = ObjectId()
    device_doc = {
        "_id": device_id,
        "hostname": "Unknown",
        "ipAddress": "192.168.1.20",
        "deviceType": "Unknown Device",
        "discoveryStatus": DISCOVERY_STATUS_PENDING,
    }
    network_info = {
        "hostname": "sw1.local",
        "macAddress": "",
        "vendor": "Cisco Systems",
        "os": {"name": "IOS", "family": "", "generation": "", "accuracy": ""},
        "deviceType": "switch",
        "ports": [],
        "services": [],
        "lastScan": None,
    }
    classification = ClassificationResult(
        hostname="sw1.local",
        vendor="Cisco Systems",
        operating_system="Cisco IOS",
        device_type="Managed Switch",
        confidence=95,
        classification_method="cisco-switch",
        discovery_source="nmap",
        signals_matched=["vendor"],
    )

    with patch("services.discovery.enrichment.db") as mock_db:
        mock_db.devices.find_one_and_update.return_value = device_doc
        mock_db.devices.find_one.return_value = device_doc

        with (
            patch(
                "services.nmap_service.scan_device_nmap",
                return_value=network_info,
            ),
            patch("services.discovery_service.get_hostname", return_value=None),
            patch(
                "services.discovery.apply.classify_network_info",
                return_value=(classification, MagicMock()),
            ) as classify_fn,
            patch(
                "services.discovery.apply.apply_classification_to_device",
            ) as apply_cls_fn,
        ):
            _run_discovery_enrichment(device_id, "192.168.1.20")

            classify_fn.assert_called_once()
            apply_cls_fn.assert_called_once()
            final_update = mock_db.devices.update_one.call_args[0][1]["$set"]
            assert final_update["discoveryStatus"] == DISCOVERY_STATUS_COMPLETED


def test_background_enrichment_marks_failed_on_nmap_error():
    device_id = ObjectId()
    device_doc = {
        "_id": device_id,
        "hostname": "Unknown",
        "ipAddress": "192.168.1.21",
        "deviceType": "Unknown Device",
        "discoveryStatus": DISCOVERY_STATUS_PENDING,
    }

    with patch("services.discovery.enrichment.db") as mock_db:
        mock_db.devices.find_one_and_update.return_value = device_doc
        mock_db.devices.find_one.return_value = device_doc

        with (
            patch(
                "services.nmap_service.scan_device_nmap",
                side_effect=TimeoutError("nmap timed out"),
            ),
            patch("services.discovery_service.get_hostname", return_value=None),
            patch(
                "services.discovery.apply.classify_network_info",
                return_value=(
                    ClassificationResult(
                        hostname="Unknown",
                        vendor="",
                        operating_system="",
                        device_type="Unknown Device",
                        confidence=20,
                        classification_method="unknown",
                        discovery_source="none",
                        signals_matched=[],
                    ),
                    MagicMock(),
                ),
            ),
            patch("services.discovery.apply.apply_classification_to_device"),
        ):
            _run_discovery_enrichment(device_id, "192.168.1.21")

            final_update = mock_db.devices.update_one.call_args[0][1]["$set"]
            assert final_update["discoveryStatus"] == DISCOVERY_STATUS_FAILED
            assert "nmap timed out" in final_update["discoveryEnrichmentError"]


def test_existing_device_does_not_enqueue_enrichment():
    existing = {
        "_id": ObjectId(),
        "hostname": "core-sw1",
        "ipAddress": "192.168.1.10",
        "deviceType": "Managed Switch",
    }
    ping_result = {"responseTime": 12.5, "lastSeen": "2026-08-07T00:00:00Z"}
    refreshed = {**existing, "status": "Online"}

    with (
        patch.object(apply_mod, "db") as mock_db,
        patch("services.monitor_service.apply_ping_result") as apply_ping,
        patch("services.discovery.apply.enqueue_discovery_enrichment") as enqueue_fn,
        patch("services.nmap_service.scan_device_nmap") as nmap_fn,
    ):
        mock_db.devices.find_one.return_value = refreshed
        result = apply_mod.enrich_online_host(
            "192.168.1.10",
            ping_result=ping_result,
            existing=existing,
        )

        apply_ping.assert_called_once()
        nmap_fn.assert_not_called()
        enqueue_fn.assert_not_called()
        mock_db.devices.insert_one.assert_not_called()

    assert result["saved"] is False


def test_offline_ip_does_not_insert_or_enqueue():
    from services.discovery_service import scan_single_ip

    with (
        patch("services.discovery_service.ping_device", return_value={
            "success": False,
            "status": "Not Reachable",
        }),
        patch("services.discovery.apply.enqueue_discovery_enrichment") as enqueue_fn,
        patch("services.discovery_service.db") as mock_db,
    ):
        result = scan_single_ip("192.168.1.250")

    mock_db.devices.find_one.assert_not_called()
    enqueue_fn.assert_not_called()
    assert result["saved"] is False
    assert result["status"] == "Offline"


def test_nmap_concurrency_uses_bounded_executor():
    from services.discovery import enrichment as enrichment_mod

    enrichment_mod.shutdown_discovery_enrichment_executor()
    with patch.object(enrichment_mod, "_worker_count", return_value=5):
        executor = enrichment_mod._get_executor()
        assert executor._max_workers == 5
    enrichment_mod.shutdown_discovery_enrichment_executor()


def test_discovery_scan_single_ip_returns_without_nmap():
    from services.discovery_service import scan_single_ip

    inserted_id = ObjectId()
    ping_result = {
        "success": True,
        "status": "Online",
        "responseTime": 2.0,
        "lastSeen": "2026-08-07T00:00:00Z",
    }

    with (
        patch("services.discovery_service.ping_device", return_value=ping_result),
        patch("services.discovery_service.db") as mock_db,
        patch("services.nmap_service.scan_device_nmap") as nmap_fn,
        patch("services.discovery.apply.enqueue_discovery_enrichment"),
    ):
        mock_db.devices.find_one.return_value = None
        mock_db.devices.insert_one.return_value = MagicMock(inserted_id=inserted_id)
        result = scan_single_ip("10.0.0.8")

    nmap_fn.assert_not_called()
    assert result["saved"] is True
    assert result["discoveryStatus"] == DISCOVERY_STATUS_PENDING


def test_discovery_endpoint_returns_without_waiting_for_nmap():
    """Route layer delegates to scan_single_ip without blocking on Nmap."""
    from flask import Flask

    from routes.discovery_routes import discovery_bp, discover_device

    app = Flask(__name__)
    app.register_blueprint(discovery_bp, url_prefix="/api")

    row = {
        "hostname": "Unknown",
        "ipAddress": "10.0.0.5",
        "status": "Online",
        "responseTime": 1.2,
        "saved": True,
        "deviceType": "Unknown Device",
        "discoveryStatus": DISCOVERY_STATUS_PENDING,
        "nmapError": None,
    }

    with app.test_request_context(
        "/api/discovery/discover-device",
        method="POST",
        json={"ipAddress": "10.0.0.5"},
    ):
        with (
            patch("services.discovery_service.scan_single_ip", return_value=row) as scan_fn,
            patch("services.nmap_service.scan_device_nmap") as nmap_fn,
            patch("utils.auth.get_token_from_request", return_value="token"),
            patch(
                "utils.auth.decode_access_token",
                return_value={"role": "admin", "sub": str(ObjectId()), "username": "admin"},
            ),
            patch("config.database.db") as auth_db,
        ):
            auth_db.users.find_one.return_value = {"active": True}
            response, status_code = discover_device()

    scan_fn.assert_called_once_with("10.0.0.5")
    nmap_fn.assert_not_called()
    assert status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["devices"][0]["discoveryStatus"] == DISCOVERY_STATUS_PENDING


def test_duplicate_insert_does_not_enqueue_enrichment():
    ip_address = "192.168.1.55"
    existing_doc = {
        "_id": ObjectId(),
        "hostname": "existing-sw",
        "ipAddress": ip_address,
        "deviceType": "Switch",
    }
    ping_result = {"responseTime": 5.0, "lastSeen": "2026-08-07T00:00:00Z"}

    with patch.object(apply_mod, "db") as mock_db:
        mock_db.devices.insert_one.side_effect = DuplicateKeyError("dup")
        mock_db.devices.find_one.return_value = existing_doc

        with (
            patch("services.monitor_service.apply_ping_result") as apply_ping,
            patch("services.discovery.apply.enqueue_discovery_enrichment") as enqueue_fn,
        ):
            result = apply_mod.enrich_online_host(
                ip_address,
                ping_result=ping_result,
                existing=None,
            )

            apply_ping.assert_called_once()
            enqueue_fn.assert_not_called()

    assert result["saved"] is False


def test_scan_progress_tracks_completed_percent_and_elapsed():
    from services import discovery_service as ds

    scan_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    with ds._scan_progress_lock:
        ds._scan_progress.clear()

    ds.begin_scan_progress(scan_id, 4)
    first = ds.get_scan_progress(scan_id)
    assert first is not None
    assert first["status"] == "running"
    assert first["total"] == 4
    assert first["completed"] == 0
    assert first["percent"] == 0

    ds._record_scan_result(scan_id, {"status": "Online", "saved": True})
    ds._record_scan_result(scan_id, {"status": "Offline", "saved": False})
    mid = ds.get_scan_progress(scan_id)
    assert mid["completed"] == 2
    assert mid["percent"] == 50
    assert mid["online"] == 1
    assert mid["newlySaved"] == 1
    assert mid["elapsedSeconds"] >= 0

    ds.finish_scan_progress(scan_id, status="complete")
    done = ds.get_scan_progress(scan_id)
    assert done["status"] == "complete"
    assert done["completed"] == 4
    assert done["percent"] == 100


def test_invalid_scan_id_is_ignored():
    from services import discovery_service as ds

    ds.begin_scan_progress("not-a-uuid", 10)
    assert ds.get_scan_progress("not-a-uuid") is None
    assert ds.is_valid_scan_id("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee") is True


def test_discover_ips_updates_progress_without_waiting_on_nmap():
    from services.discovery_service import discover_ips, get_scan_progress

    scan_id = "11111111-2222-3333-4444-555555555555"

    def fake_scan(ip):
        return {
            "hostname": None,
            "ipAddress": ip,
            "status": "Offline",
            "responseTime": None,
            "saved": False,
        }

    with patch("services.discovery_service.scan_single_ip", side_effect=fake_scan):
        results = discover_ips(["10.0.0.1", "10.0.0.2"], scan_id=scan_id)

    assert len(results) == 2
    progress = get_scan_progress(scan_id)
    assert progress["percent"] == 100
    assert progress["completed"] == 2
    assert progress["status"] == "complete"
