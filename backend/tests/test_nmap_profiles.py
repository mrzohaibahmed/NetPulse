"""Unit tests for Nmap quick/deep scan profile resolution."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from bson import ObjectId

from services.discovery.classifier import ClassificationResult
from services.nmap_service import (
    SCAN_PROFILE_DEEP,
    SCAN_PROFILE_QUICK,
    normalize_scan_profile,
    resolve_nmap_arguments,
    scan_and_update_device,
    scan_device_nmap,
)


class _HostStub(dict):
    def all_protocols(self):
        return []


def test_resolve_quick_and_deep_arguments():
    from config.database import NMAP_ARGUMENTS, NMAP_QUICK_ARGUMENTS

    assert resolve_nmap_arguments(SCAN_PROFILE_QUICK) == NMAP_QUICK_ARGUMENTS
    assert resolve_nmap_arguments(SCAN_PROFILE_DEEP) == NMAP_ARGUMENTS
    assert resolve_nmap_arguments("unknown") == NMAP_ARGUMENTS
    assert resolve_nmap_arguments("") == NMAP_ARGUMENTS


def test_normalize_scan_profile():
    assert normalize_scan_profile("quick") == SCAN_PROFILE_QUICK
    assert normalize_scan_profile("DEEP") == SCAN_PROFILE_DEEP
    assert normalize_scan_profile(None) == SCAN_PROFILE_DEEP
    assert normalize_scan_profile("nope") == SCAN_PROFILE_DEEP


def test_scan_device_nmap_passes_profile_arguments():
    fake_scanner = MagicMock()
    fake_scanner.scan.return_value = {
        "scan": {
            "10.0.0.1": _HostStub({
                "addresses": {"ipv4": "10.0.0.1"},
                "vendor": {},
                "hostnames": [],
                "osmatch": [],
            })
        }
    }

    with (
        patch("services.nmap_service._get_scanner", return_value=fake_scanner),
        patch(
            "services.nmap_service.NMAP_QUICK_ARGUMENTS",
            "-O -sV -T4 --top-ports 100",
        ),
        patch("services.nmap_service.NMAP_ARGUMENTS", "-A -T4"),
        patch("services.nmap_service.NMAP_TIMEOUT", 30),
    ):
        scan_device_nmap("10.0.0.1", profile="quick")
        assert fake_scanner.scan.call_args.kwargs["arguments"] == (
            "-O -sV -T4 --top-ports 100"
        )

        scan_device_nmap("10.0.0.1", profile="deep")
        assert fake_scanner.scan.call_args.kwargs["arguments"] == "-A -T4"


def test_scan_and_update_device_defaults_to_deep():
    device = {
        "_id": ObjectId(),
        "ipAddress": "10.0.0.2",
        "hostname": "sw1",
        "status": "Online",
    }
    network_info = {
        "hostname": "sw1",
        "macAddress": "",
        "vendor": "",
        "os": {"name": "", "family": "", "generation": "", "accuracy": ""},
        "deviceType": "",
        "ports": [],
        "services": [],
    }
    classification = ClassificationResult(
        hostname="sw1",
        vendor="",
        operating_system="",
        device_type="Switch",
        confidence=60,
        classification_method="nmap-osclass",
        discovery_source="nmap",
    )

    with (
        patch(
            "services.nmap_service.scan_device_nmap",
            return_value=network_info,
        ) as nmap_fn,
        patch(
            "services.discovery.apply.classify_network_info",
            return_value=(classification, MagicMock()),
        ),
        patch("services.discovery.apply.apply_classification_to_device"),
    ):
        result = scan_and_update_device(device)
        assert result["success"] is True
        nmap_fn.assert_called_once_with("10.0.0.2", profile=SCAN_PROFILE_DEEP)


def test_scan_and_update_device_accepts_quick():
    device = {
        "_id": ObjectId(),
        "ipAddress": "10.0.0.3",
        "hostname": "pc1",
        "status": "Online",
    }
    network_info = {
        "hostname": "pc1",
        "macAddress": "",
        "vendor": "",
        "os": {"name": "", "family": "", "generation": "", "accuracy": ""},
        "deviceType": "",
        "ports": [],
        "services": [],
    }
    classification = ClassificationResult(
        hostname="pc1",
        vendor="",
        operating_system="",
        device_type="Unknown Device",
        confidence=20,
        classification_method="unknown",
        discovery_source="nmap",
    )

    with (
        patch(
            "services.nmap_service.scan_device_nmap",
            return_value=network_info,
        ) as nmap_fn,
        patch(
            "services.discovery.apply.classify_network_info",
            return_value=(classification, MagicMock()),
        ),
        patch("services.discovery.apply.apply_classification_to_device"),
    ):
        result = scan_and_update_device(device, profile=SCAN_PROFILE_QUICK)
        assert result["success"] is True
        nmap_fn.assert_called_once_with("10.0.0.3", profile=SCAN_PROFILE_QUICK)
