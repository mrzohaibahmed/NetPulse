"""Phase 1 discovery optimization: skip Nmap for already-monitored hosts."""

from __future__ import annotations

import sys
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

from services.discovery import apply as apply_mod
from services.discovery.enrichment import DISCOVERY_STATUS_PENDING


def test_existing_device_skips_nmap_and_classification():
    existing = {
        "_id": ObjectId(),
        "hostname": "core-sw1",
        "ipAddress": "192.168.1.10",
        "deviceType": "Managed Switch",
        "vendor": "Cisco Systems",
        "operatingSystem": "Cisco IOS XE",
        "classificationConfidence": 98,
        "classificationMethod": "cisco-switch",
    }
    ping_result = {
        "responseTime": 12.5,
        "lastSeen": "2026-08-07T00:00:00Z",
    }
    refreshed = {**existing, "status": "Online"}

    with (
        patch.object(apply_mod, "db") as mock_db,
        patch("services.monitor_service.apply_ping_result") as apply_ping,
        patch("services.nmap_service.scan_device_nmap") as nmap_fn,
        patch("services.discovery_service.get_hostname") as dns_fn,
        patch.object(apply_mod, "classify_network_info") as classify_fn,
        patch("services.discovery.apply.enqueue_discovery_enrichment") as enqueue_fn,
    ):
        mock_db.devices.find_one.return_value = refreshed
        result = apply_mod.enrich_online_host(
            "192.168.1.10",
            ping_result=ping_result,
            existing=existing,
        )

        nmap_fn.assert_not_called()
        dns_fn.assert_not_called()
        classify_fn.assert_not_called()
        enqueue_fn.assert_not_called()
        apply_ping.assert_called_once()
        mock_db.devices.insert_one.assert_not_called()

    assert result["saved"] is False
    assert result["hostname"] == "core-sw1"
    assert result["deviceType"] == "Managed Switch"
    assert result["vendor"] == "Cisco Systems"
    assert result["operatingSystem"] == "Cisco IOS XE"
    assert result["classificationConfidence"] == 98
    assert result["nmapError"] is None


def test_new_device_inserts_immediately_and_queues_background_nmap():
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
            result = apply_mod.enrich_online_host(
                "192.168.1.99",
                ping_result=ping_result,
                existing=None,
            )

            nmap_fn.assert_not_called()
            classify_fn.assert_not_called()
            mock_db.devices.insert_one.assert_called_once()
            enqueue_fn.assert_called_once_with(inserted_id, "192.168.1.99")

            insert_doc = mock_db.devices.insert_one.call_args[0][0]
            assert insert_doc["discoveryStatus"] == DISCOVERY_STATUS_PENDING

    assert result["saved"] is True
    assert result["hostname"] == "Unknown"
    assert result["deviceType"] == "Unknown Device"
    assert result["discoveryStatus"] == DISCOVERY_STATUS_PENDING
