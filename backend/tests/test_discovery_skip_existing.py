"""Phase 1 discovery optimization: skip Nmap for already-monitored hosts."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from bson import ObjectId

from services.discovery import apply as apply_mod
from services.discovery.classifier import ClassificationResult


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

    with (
        patch.object(apply_mod, "db") as mock_db,
        patch("services.nmap_service.scan_device_nmap") as nmap_fn,
        patch("services.discovery_service.get_hostname") as dns_fn,
        patch.object(apply_mod, "classify_network_info") as classify_fn,
    ):
        result = apply_mod.enrich_online_host(
            "192.168.1.10",
            ping_result=ping_result,
            existing=existing,
        )

        nmap_fn.assert_not_called()
        dns_fn.assert_not_called()
        classify_fn.assert_not_called()

        mock_db.devices.update_one.assert_called_once()
        update_doc = mock_db.devices.update_one.call_args[0][1]["$set"]
        assert set(update_doc.keys()) == {
            "status",
            "responseTime",
            "lastSeen",
            "updatedAt",
        }
        assert update_doc["status"] == "Online"
        assert update_doc["responseTime"] == 12.5
        mock_db.devices.insert_one.assert_not_called()

    assert result["saved"] is False
    assert result["hostname"] == "core-sw1"
    assert result["deviceType"] == "Managed Switch"
    assert result["vendor"] == "Cisco Systems"
    assert result["operatingSystem"] == "Cisco IOS XE"
    assert result["classificationConfidence"] == 98
    assert result["nmapError"] is None


def test_new_device_still_runs_nmap():
    ping_result = {
        "responseTime": 8.0,
        "lastSeen": "2026-08-07T00:00:00Z",
    }
    network_info = {
        "hostname": "new-host.local",
        "macAddress": "",
        "vendor": "Cisco Systems",
        "os": {
            "name": "Cisco IOS XE",
            "family": "IOS",
            "generation": "",
            "accuracy": "98",
        },
        "deviceType": "switch",
        "ports": [],
        "services": [],
        "lastScan": None,
    }
    classification = ClassificationResult(
        hostname="new-host.local",
        vendor="Cisco Systems",
        operating_system="Cisco IOS XE",
        device_type="Managed Switch",
        confidence=95,
        classification_method="cisco-switch",
        discovery_source="nmap",
        signals_matched=["vendor", "os", "ssh"],
    )
    inserted_id = ObjectId()

    with patch.object(apply_mod, "db") as mock_db:
        mock_db.devices.insert_one.return_value = MagicMock(inserted_id=inserted_id)

        with (
            patch(
                "services.nmap_service.scan_device_nmap",
                return_value=network_info,
            ) as nmap_fn,
            patch("services.discovery_service.get_hostname", return_value=None),
            patch.object(
                apply_mod,
                "classify_network_info",
                return_value=(classification, MagicMock()),
            ) as classify_fn,
        ):
            result = apply_mod.enrich_online_host(
                "192.168.1.99",
                ping_result=ping_result,
                existing=None,
            )

            nmap_fn.assert_called_once_with("192.168.1.99")
            classify_fn.assert_called_once()
            mock_db.devices.insert_one.assert_called_once()
            mock_db.devices.update_one.assert_not_called()

    assert result["saved"] is True
    assert result["hostname"] == "new-host.local"
    assert result["deviceType"] == "Managed Switch"
    assert result["vendor"] == "Cisco Systems"
    assert result["classificationConfidence"] == 95
