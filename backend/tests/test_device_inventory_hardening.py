"""Phase 7: unique ipAddress index and discovery duplicate handling."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from services.device_indexes import ensure_device_indexes
from services.discovery import apply as apply_mod
from services.discovery.classifier import ClassificationResult


def test_unique_index_creation_is_idempotent():
    mock_devices = MagicMock()
    mock_devices.create_index.return_value = "uniq_devices_ipAddress"

    with patch("services.device_indexes.db") as mock_db:
        mock_db.devices = mock_devices
        ensure_device_indexes()
        ensure_device_indexes()

    assert mock_devices.create_index.call_count == 2
    mock_devices.create_index.assert_called_with(
        [("ipAddress", 1)],
        unique=True,
        name="uniq_devices_ipAddress",
    )


def test_duplicate_key_error_during_discovery_insert():
    ip_address = "192.168.1.55"
    existing_id = ObjectId()
    existing_doc = {
        "_id": existing_id,
        "hostname": "existing-sw",
        "ipAddress": ip_address,
        "deviceType": "Switch",
        "vendor": "Cisco Systems",
        "operatingSystem": "IOS",
        "classificationConfidence": 90,
        "classificationMethod": "cisco-switch",
    }
    ping_result = {"responseTime": 5.0, "lastSeen": "2026-08-07T00:00:00Z"}
    network_info = {
        "hostname": "race-host.local",
        "vendor": "Cisco Systems",
        "os": {"name": "", "family": "", "generation": "", "accuracy": ""},
        "deviceType": "",
        "ports": [],
        "services": [],
        "lastScan": None,
    }
    classification = ClassificationResult(
        hostname="race-host.local",
        vendor="Cisco Systems",
        operating_system="IOS",
        device_type="Managed Switch",
        confidence=95,
        classification_method="cisco-switch",
        discovery_source="nmap",
        signals_matched=["vendor"],
    )

    with patch.object(apply_mod, "db") as mock_db:
        mock_db.devices.insert_one.side_effect = DuplicateKeyError("dup")
        mock_db.devices.find_one.return_value = existing_doc

        with (
            patch("services.nmap_service.scan_device_nmap", return_value=network_info),
            patch("services.discovery_service.get_hostname", return_value=None),
            patch.object(
                apply_mod,
                "classify_network_info",
                return_value=(classification, MagicMock()),
            ),
        ):
            result = apply_mod.enrich_online_host(
                ip_address,
                ping_result=ping_result,
                existing=None,
            )

    mock_db.devices.insert_one.assert_called_once()
    mock_db.devices.find_one.assert_called_once_with({"ipAddress": ip_address})


def test_existing_document_returned_after_duplicate():
    ip_address = "192.168.1.56"
    existing_doc = {
        "_id": ObjectId(),
        "hostname": "winner-host",
        "ipAddress": ip_address,
        "deviceType": "Router",
        "vendor": "Juniper",
        "operatingSystem": "JunOS",
        "classificationConfidence": 88,
        "classificationMethod": "router-fingerprint",
    }
    ping_result = {"responseTime": 3.0, "lastSeen": "2026-08-07T00:00:00Z"}

    with patch.object(apply_mod, "db") as mock_db:
        mock_db.devices.insert_one.side_effect = DuplicateKeyError("dup")
        mock_db.devices.find_one.return_value = existing_doc

        with (
            patch("services.nmap_service.scan_device_nmap", return_value=None),
            patch("services.discovery_service.get_hostname", return_value=None),
            patch.object(
                apply_mod,
                "classify_network_info",
                return_value=(
                    ClassificationResult(
                        hostname="Unknown",
                        vendor="",
                        operating_system="",
                        device_type="Unknown Device",
                        confidence=20,
                        classification_method="unknown",
                        discovery_source="none",
                    ),
                    MagicMock(),
                ),
            ),
        ):
            result = apply_mod.enrich_online_host(
                ip_address,
                ping_result=ping_result,
                existing=None,
            )

    assert result["hostname"] == "winner-host"
    assert result["deviceType"] == "Router"
    assert result["vendor"] == "Juniper"
    assert result["ipAddress"] == ip_address


def test_discovery_does_not_fail_on_duplicate():
    ip_address = "192.168.1.57"
    existing_doc = {
        "_id": ObjectId(),
        "hostname": "kept",
        "ipAddress": ip_address,
        "deviceType": "Switch",
    }
    ping_result = {"responseTime": 1.0, "lastSeen": "2026-08-07T00:00:00Z"}

    with patch.object(apply_mod, "db") as mock_db:
        mock_db.devices.insert_one.side_effect = DuplicateKeyError("dup")
        mock_db.devices.find_one.return_value = existing_doc

        with (
            patch("services.nmap_service.scan_device_nmap", return_value=None),
            patch("services.discovery_service.get_hostname", return_value=None),
            patch.object(
                apply_mod,
                "classify_network_info",
                return_value=(
                    ClassificationResult(
                        hostname="Unknown",
                        vendor="",
                        operating_system="",
                        device_type="Unknown Device",
                        confidence=20,
                        classification_method="unknown",
                        discovery_source="none",
                    ),
                    MagicMock(),
                ),
            ),
        ):
            result = apply_mod.enrich_online_host(
                ip_address,
                ping_result=ping_result,
                existing=None,
            )

    assert result["status"] == "Online"
    assert result["saved"] is False
    assert result["nmapError"] is None


def test_no_second_document_created_on_duplicate():
    ip_address = "192.168.1.58"
    existing_doc = {
        "_id": ObjectId(),
        "hostname": "only-one",
        "ipAddress": ip_address,
        "deviceType": "Switch",
    }
    ping_result = {"responseTime": 2.0, "lastSeen": "2026-08-07T00:00:00Z"}

    with patch.object(apply_mod, "db") as mock_db:
        mock_db.devices.insert_one.side_effect = DuplicateKeyError("dup")
        mock_db.devices.find_one.return_value = existing_doc

        with (
            patch("services.nmap_service.scan_device_nmap", return_value=None),
            patch("services.discovery_service.get_hostname", return_value=None),
            patch.object(
                apply_mod,
                "classify_network_info",
                return_value=(
                    ClassificationResult(
                        hostname="Unknown",
                        vendor="",
                        operating_system="",
                        device_type="Unknown Device",
                        confidence=20,
                        classification_method="unknown",
                        discovery_source="none",
                    ),
                    MagicMock(),
                ),
            ),
        ):
            apply_mod.enrich_online_host(
                ip_address,
                ping_result=ping_result,
                existing=None,
            )

    mock_db.devices.insert_one.assert_called_once()
    assert mock_db.devices.update_one.call_count == 0
