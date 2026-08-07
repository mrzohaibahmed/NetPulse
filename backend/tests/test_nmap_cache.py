"""Tests for Nmap TTL scan cache (networkInfo.lastScan)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from bson import ObjectId

from services.discovery.classifier import ClassificationResult
from services.nmap_service import (
    SCAN_PROFILE_DEEP,
    get_cached_network_info,
    is_network_info_cache_fresh,
    scan_and_update_device,
    scan_device_nmap,
)


def _network_info(*, age_seconds: int | None) -> dict:
    if age_seconds is None:
        last_scan = None
    else:
        last_scan = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return {
        "hostname": "sw1",
        "macAddress": "",
        "vendor": "Cisco",
        "os": {"name": "IOS", "family": "", "generation": "", "accuracy": ""},
        "deviceType": "switch",
        "ports": [],
        "services": [],
        "lastScan": last_scan,
    }


def _online_device(network_info: dict | None = None) -> dict:
    device = {
        "_id": ObjectId(),
        "hostname": "core-sw1",
        "ipAddress": "192.168.1.10",
        "status": "Online",
    }
    if network_info is not None:
        device["networkInfo"] = network_info
    return device


@pytest.fixture
def ttl_6h():
    with patch("services.nmap_service.NMAP_CACHE_TTL", 21600):
        yield


def test_recent_scan_cache_hit_skips_nmap(ttl_6h):
    device = _online_device(_network_info(age_seconds=120))
    with patch("services.nmap_service.scan_device_nmap") as nmap_fn:
        with patch("services.discovery.apply.classify_network_info") as classify_fn:
            result = scan_and_update_device(device, force=False)
            assert result["success"] is True
            nmap_fn.assert_not_called()
            classify_fn.assert_not_called()


def test_expired_scan_performs_nmap(ttl_6h):
    device = _online_device(_network_info(age_seconds=30000))
    fresh_info = _network_info(age_seconds=0)
    classification = ClassificationResult(
        hostname="core-sw1",
        vendor="Cisco",
        operating_system="IOS",
        device_type="Managed Switch",
        confidence=90,
        classification_method="cisco-switch",
        discovery_source="nmap",
    )

    with patch(
        "services.nmap_service.scan_device_nmap",
        return_value=fresh_info,
    ) as nmap_fn:
        with patch(
            "services.discovery.apply.classify_network_info",
            return_value=(classification, MagicMock()),
        ) as classify_fn:
            with patch("services.discovery.apply.apply_classification_to_device"):
                result = scan_and_update_device(device, force=False)
                assert result["success"] is True
                nmap_fn.assert_called_once()
                classify_fn.assert_called_once()


def test_force_true_always_performs_scan(ttl_6h):
    device = _online_device(_network_info(age_seconds=60))
    fresh_info = _network_info(age_seconds=0)
    classification = ClassificationResult(
        hostname="core-sw1",
        vendor="Cisco",
        operating_system="IOS",
        device_type="Managed Switch",
        confidence=90,
        classification_method="cisco-switch",
        discovery_source="nmap",
    )

    with patch(
        "services.nmap_service.scan_device_nmap",
        return_value=fresh_info,
    ) as nmap_fn:
        with patch(
            "services.discovery.apply.classify_network_info",
            return_value=(classification, MagicMock()),
        ):
            with patch("services.discovery.apply.apply_classification_to_device"):
                result = scan_and_update_device(device, force=True)
                assert result["success"] is True
                nmap_fn.assert_called_once()
                assert nmap_fn.call_args.kwargs.get("force") is True


def test_missing_last_scan_performs_scan(ttl_6h):
    device = _online_device(_network_info(age_seconds=None))
    assert is_network_info_cache_fresh(device["networkInfo"]) is False
    assert get_cached_network_info(device) is None

    fresh_info = _network_info(age_seconds=0)
    classification = ClassificationResult(
        hostname="core-sw1",
        vendor="",
        operating_system="",
        device_type="Unknown Device",
        confidence=20,
        classification_method="unknown",
        discovery_source="nmap",
    )

    with patch(
        "services.nmap_service.scan_device_nmap",
        return_value=fresh_info,
    ) as nmap_fn:
        with patch(
            "services.discovery.apply.classify_network_info",
            return_value=(classification, MagicMock()),
        ):
            with patch("services.discovery.apply.apply_classification_to_device"):
                result = scan_and_update_device(device, force=False)
                assert result["success"] is True
                nmap_fn.assert_called_once()


def test_scan_device_nmap_returns_cached_without_subprocess(ttl_6h):
    existing = _network_info(age_seconds=300)
    with patch("services.nmap_service._create_scanner") as create_scanner:
        result = scan_device_nmap(
            "192.168.1.10",
            existing_network_info=existing,
            force=False,
        )
        assert result is existing
        create_scanner.assert_not_called()


def test_manual_deep_scan_path_uses_force(ttl_6h):
    """Simulates nmap_routes scan-details delegating with force=True."""
    device = _online_device(_network_info(age_seconds=30))
    with patch("services.nmap_service.get_cached_network_info") as cache_fn:
        cache_fn.return_value = None
        with patch(
            "services.nmap_service.scan_device_nmap",
            return_value=_network_info(age_seconds=0),
        ) as nmap_fn:
            with patch(
                "services.discovery.apply.classify_network_info",
                return_value=(
                    ClassificationResult(
                        hostname="core-sw1",
                        vendor="",
                        operating_system="",
                        device_type="Switch",
                        confidence=60,
                        classification_method="nmap-osclass",
                        discovery_source="nmap",
                    ),
                    MagicMock(),
                ),
            ):
                with patch("services.discovery.apply.apply_classification_to_device"):
                    scan_and_update_device(device, profile=SCAN_PROFILE_DEEP, force=True)
                    cache_fn.assert_called_once_with(device, force=True)
                    assert nmap_fn.call_args.kwargs.get("force") is True
