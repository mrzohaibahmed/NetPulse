"""Phase 7: unique ipAddress index and discovery duplicate handling."""

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
from pymongo.errors import DuplicateKeyError

from services.device_indexes import ensure_device_indexes
from services.discovery import apply as apply_mod


def test_unique_index_creation_is_idempotent():
    mock_devices = MagicMock()
    mock_devices.create_index.side_effect = [
        "uniq_devices_ipAddress",
        "idx_devices_monitor_due_claim",
        "uniq_devices_ipAddress",
        "idx_devices_monitor_due_claim",
    ]

    with patch("services.device_indexes.db") as mock_db:
        mock_db.devices = mock_devices
        ensure_device_indexes()
        ensure_device_indexes()

    assert mock_devices.create_index.call_count == 4
    first_keys = [call.args[0] for call in mock_devices.create_index.call_args_list]
    assert [("ipAddress", 1)] in first_keys
    assert [("nextCheckAt", 1), ("scanClaimExpiresAt", 1)] in first_keys

    due_calls = [
        call
        for call in mock_devices.create_index.call_args_list
        if call.kwargs.get("name") == "idx_devices_monitor_due_claim"
        or (call.args and call.args[0] == [("nextCheckAt", 1), ("scanClaimExpiresAt", 1)])
    ]
    assert len(due_calls) == 2
    assert due_calls[0].kwargs["partialFilterExpression"] == {"monitor": True}
    assert due_calls[0].kwargs.get("unique") is not True


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

    with patch.object(apply_mod, "db") as mock_db:
        mock_db.devices.insert_one.side_effect = DuplicateKeyError("dup")
        mock_db.devices.find_one.return_value = existing_doc

        with (
            patch("services.monitor_service.apply_ping_result"),
            patch("services.discovery.apply.enqueue_discovery_enrichment") as enqueue_fn,
        ):
            apply_mod.enrich_online_host(
                ip_address,
                ping_result=ping_result,
                existing=None,
            )

    mock_db.devices.insert_one.assert_called_once()
    assert mock_db.devices.find_one.call_count >= 1
    assert any(
        call.args == ({"ipAddress": ip_address},)
        for call in mock_db.devices.find_one.call_args_list
    )
    enqueue_fn.assert_not_called()


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
            patch("services.monitor_service.apply_ping_result"),
            patch("services.discovery.apply.enqueue_discovery_enrichment"),
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
            patch("services.monitor_service.apply_ping_result"),
            patch("services.discovery.apply.enqueue_discovery_enrichment"),
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
            patch("services.monitor_service.apply_ping_result"),
            patch("services.discovery.apply.enqueue_discovery_enrichment"),
        ):
            apply_mod.enrich_online_host(
                ip_address,
                ping_result=ping_result,
                existing=None,
            )

    mock_db.devices.insert_one.assert_called_once()
    assert mock_db.devices.update_one.call_count == 0
