"""Tests for manual vs automatic device identity ownership."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from bson import ObjectId

from services.discovery.apply import apply_classification_to_device
from services.discovery.classifier import ClassificationResult
from services.discovery.identity_management import (
    OWNERSHIP_AUTO,
    OWNERSHIP_MANUAL,
    get_identity_management,
    ownership_for_device_edit,
    reset_identity_management,
)


def _result(**overrides) -> ClassificationResult:
    base = dict(
        hostname="detected-host",
        vendor="Cisco Systems",
        operating_system="Cisco IOS XE",
        device_type="Managed Switch",
        confidence=95,
        classification_method="cisco-switch",
        discovery_source="nmap",
        signals_matched=["vendor"],
    )
    base.update(overrides)
    return ClassificationResult(**base)


def test_auto_device_classification_updates_hostname_and_type():
    device_id = ObjectId()
    existing = {
        "_id": device_id,
        "hostname": "Unknown",
        "ipAddress": "10.0.0.1",
        "deviceType": "Unknown Device",
    }
    result = _result()

    with patch("services.discovery.apply.db") as mock_db:
        update_fields = apply_classification_to_device(
            device_id,
            result,
            network_info={"ports": [], "lastScan": None},
            existing=existing,
        )

    assert update_fields["hostname"] == "detected-host"
    assert update_fields["deviceType"] == "Managed Switch"
    assert update_fields["vendor"] == "Cisco Systems"
    assert update_fields["operatingSystem"] == "Cisco IOS XE"
    mock_db.devices.update_one.assert_called_once()


def test_manual_hostname_preserved():
    device_id = ObjectId()
    existing = {
        "_id": device_id,
        "hostname": "operator-name",
        "ipAddress": "10.0.0.2",
        "deviceType": "Switch",
        "identityManagement": {"hostname": OWNERSHIP_MANUAL, "deviceType": OWNERSHIP_AUTO},
    }
    result = _result(hostname="nmap-name", device_type="Router")

    with patch("services.discovery.apply.db") as mock_db:
        update_fields = apply_classification_to_device(
            device_id,
            result,
            existing=existing,
        )

    assert "hostname" not in update_fields
    assert update_fields["deviceType"] == "Router"
    assert update_fields["vendor"] == "Cisco Systems"
    mock_db.devices.update_one.assert_called_once()


def test_manual_device_type_preserved():
    device_id = ObjectId()
    existing = {
        "_id": device_id,
        "hostname": "sw1",
        "ipAddress": "10.0.0.3",
        "deviceType": "Firewall",
        "identityManagement": {"hostname": OWNERSHIP_AUTO, "deviceType": OWNERSHIP_MANUAL},
    }
    result = _result(device_type="Managed Switch")

    with patch("services.discovery.apply.db") as mock_db:
        update_fields = apply_classification_to_device(
            device_id,
            result,
            existing=existing,
        )

    assert update_fields["hostname"] == "detected-host"
    assert "deviceType" not in update_fields
    mock_db.devices.update_one.assert_called_once()


def test_vendor_and_os_still_update_when_identity_manual():
    device_id = ObjectId()
    existing = {
        "_id": device_id,
        "hostname": "locked",
        "ipAddress": "10.0.0.4",
        "deviceType": "Printer",
        "identityManagement": {
            "hostname": OWNERSHIP_MANUAL,
            "deviceType": OWNERSHIP_MANUAL,
        },
    }
    result = _result(
        vendor="Hewlett Packard",
        operating_system="HP JetDirect",
        device_type="Printer",
    )

    with patch("services.discovery.apply.db"):
        update_fields = apply_classification_to_device(
            device_id,
            result,
            existing=existing,
        )

    assert "hostname" not in update_fields
    assert "deviceType" not in update_fields
    assert update_fields["vendor"] == "Hewlett Packard"
    assert update_fields["operatingSystem"] == "HP JetDirect"


def test_missing_identity_management_behaves_as_auto():
    existing = {"hostname": "old", "ipAddress": "10.0.0.5", "deviceType": "Other"}
    mgmt = get_identity_management(existing)
    assert mgmt == {"hostname": OWNERSHIP_AUTO, "deviceType": OWNERSHIP_AUTO}


def test_unknown_hostname_not_overwritten_on_auto_device():
    device_id = ObjectId()
    existing = {
        "_id": device_id,
        "hostname": "real-name",
        "ipAddress": "10.0.0.6",
        "deviceType": "Switch",
    }
    result = _result(hostname="Unknown")

    with patch("services.discovery.apply.db"):
        update_fields = apply_classification_to_device(
            device_id,
            result,
            existing=existing,
        )

    assert update_fields["hostname"] == "real-name"


def test_manual_hostname_never_becomes_unknown():
    device_id = ObjectId()
    existing = {
        "_id": device_id,
        "hostname": "operator-name",
        "ipAddress": "10.0.0.7",
        "deviceType": "Switch",
        "identityManagement": {"hostname": OWNERSHIP_MANUAL},
    }
    result = _result(hostname="Unknown")

    with patch("services.discovery.apply.db"):
        update_fields = apply_classification_to_device(
            device_id,
            result,
            existing=existing,
        )

    assert "hostname" not in update_fields


def test_ownership_for_device_edit_marks_changed_fields_only():
    device = {
        "hostname": "sw1",
        "deviceType": "Switch",
        "identityManagement": {"hostname": OWNERSHIP_AUTO, "deviceType": OWNERSHIP_AUTO},
    }
    mgmt = ownership_for_device_edit(device, {"hostname": "sw1-new", "monitor": True})
    assert mgmt == {"hostname": OWNERSHIP_MANUAL, "deviceType": OWNERSHIP_AUTO}

    unchanged = ownership_for_device_edit(device, {"hostname": "sw1"})
    assert unchanged is None


def test_reset_identity_management():
    device_id = ObjectId()
    with patch("services.discovery.identity_management.db") as mock_db:
        mock_db.devices.update_one.return_value = MagicMock(matched_count=1)
        assert reset_identity_management(device_id) is True
        payload = mock_db.devices.update_one.call_args[0][1]["$set"]
        assert payload["identityManagement"] == {
            "hostname": OWNERSHIP_AUTO,
            "deviceType": OWNERSHIP_AUTO,
        }
