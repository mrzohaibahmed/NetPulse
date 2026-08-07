"""Tests for SSH hostname fallback gating and resolution."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.discovery.apply import classify_network_info
from services.discovery.classifier import ClassificationEvidence, resolve_hostname
from services.discovery.ssh_hostname import (
    fetch_ssh_hostname,
    has_ssh_credentials,
    is_ssh_reachable,
    should_attempt_ssh_hostname,
)


def _port(port: int, state: str = "open", service: str = "ssh") -> dict:
    return {"port": port, "state": state, "service": service}


def _device(**overrides) -> dict:
    base = {
        "hostname": "Unknown",
        "ipAddress": "10.0.0.50",
        "credentials": {
            "sshUsername": "admin",
            "sshPassword": "secret",
        },
    }
    base.update(overrides)
    return base


def _network_info(**overrides) -> dict:
    base = {
        "hostname": "",
        "vendor": "",
        "os": {"name": "", "family": "", "generation": "", "accuracy": ""},
        "deviceType": "",
        "ports": [_port(22)],
        "services": ["ssh"],
    }
    base.update(overrides)
    return base


def test_nmap_hostname_exists_ssh_never_called():
    device = _device()
    network_info = _network_info(hostname="sw1.lab.local")

    with patch("services.discovery.apply.fetch_ssh_hostname") as ssh_fn:
        result, _ = classify_network_info(
            network_info,
            ip_address="10.0.0.50",
            existing=device,
            try_ssh=True,
        )

    ssh_fn.assert_not_called()
    assert result.hostname == "sw1.lab.local"


def test_reverse_dns_hostname_exists_ssh_never_called():
    device = _device()
    network_info = _network_info(hostname="")

    with (
        patch("services.discovery.apply.fetch_ssh_hostname") as ssh_fn,
        patch("services.discovery_service.get_hostname", return_value="rdns.local"),
    ):
        result, _ = classify_network_info(
            network_info,
            ip_address="10.0.0.50",
            existing=device,
            try_ssh=True,
        )

    ssh_fn.assert_not_called()
    assert result.hostname == "rdns.local"


def test_unknown_hostname_ssh_called():
    device = _device(hostname="Unknown")
    network_info = _network_info(hostname="")

    with (
        patch("services.discovery.apply.fetch_ssh_hostname", return_value="sw-core-01") as ssh_fn,
        patch("services.discovery_service.get_hostname", return_value=None),
    ):
        result, _ = classify_network_info(
            network_info,
            ip_address="10.0.0.50",
            existing=device,
            try_ssh=True,
        )

    ssh_fn.assert_called_once_with(device)
    assert result.hostname == "sw-core-01"


def test_ssh_returns_hostname_resolved_correctly():
    device = _device()
    network_info = _network_info(hostname="")

    with (
        patch(
            "services.discovery.ssh_hostname.fetch_ssh_hostname",
            wraps=lambda d, timeout=8.0: "ssh-switch-01",
        ),
        patch("services.discovery.apply.fetch_ssh_hostname", return_value="ssh-switch-01"),
        patch("services.discovery_service.get_hostname", return_value=None),
    ):
        result, evidence = classify_network_info(
            network_info,
            ip_address="10.0.0.50",
            existing=device,
            try_ssh=True,
        )

    assert evidence.hostname_ssh == "ssh-switch-01"
    assert result.hostname == "ssh-switch-01"
    assert result.discovery_source == "ssh"


def test_ssh_failure_preserves_existing_behavior():
    device = _device(hostname="Unknown")
    network_info = _network_info(hostname="")

    with (
        patch("services.discovery.apply.fetch_ssh_hostname", return_value="") as ssh_fn,
        patch("services.discovery_service.get_hostname", return_value=None),
    ):
        result, _ = classify_network_info(
            network_info,
            ip_address="10.0.0.50",
            existing=device,
            try_ssh=True,
        )

    ssh_fn.assert_called_once()
    assert result.hostname == "Unknown"


def test_no_credentials_ssh_skipped():
    device = _device(credentials={})
    network_info = _network_info(hostname="")

    assert not has_ssh_credentials(device)
    assert not should_attempt_ssh_hostname(device, network_info)

    with patch("services.discovery.apply.fetch_ssh_hostname") as ssh_fn:
        classify_network_info(
            network_info,
            ip_address="10.0.0.50",
            existing=device,
            try_ssh=True,
        )

    ssh_fn.assert_not_called()


def test_hostname_priority_unchanged():
    evidence = ClassificationEvidence(
        hostname_ptr="from-ptr.local",
        hostname_service="from-service",
        hostname_ssh="from-ssh",
    )
    name, source = resolve_hostname(evidence)
    assert name == "from-ptr.local"
    assert source == "nmap-ptr"

    evidence = ClassificationEvidence(
        hostname_ptr="",
        hostname_service="from-service",
        hostname_ssh="from-ssh",
    )
    name, source = resolve_hostname(evidence)
    assert name == "from-service"
    assert source == "nmap-service"

    evidence = ClassificationEvidence(
        hostname_ptr="",
        hostname_service="",
        hostname_ssh="from-ssh",
        hostname_existing="Unknown",
    )
    name, source = resolve_hostname(evidence)
    assert name == "from-ssh"
    assert source == "ssh"


def test_service_banner_hostname_skips_ssh():
    device = _device()
    network_info = _network_info(
        hostname="",
        ports=[
            {
                "port": 443,
                "state": "open",
                "service": "https",
                "product": "SSL",
                "extraInfo": "CN=banner-host.local",
            },
            _port(22),
        ],
    )

    with patch("services.discovery.apply.fetch_ssh_hostname") as ssh_fn:
        result, _ = classify_network_info(
            network_info,
            ip_address="10.0.0.50",
            existing=device,
            try_ssh=True,
        )

    ssh_fn.assert_not_called()
    assert result.hostname == "banner-host.local"


def test_ssh_unreachable_skips_connection():
    device = _device()
    network_info = _network_info(ports=[_port(22, state="closed")], services=[])

    assert not is_ssh_reachable(network_info)

    with patch("services.discovery.apply.fetch_ssh_hostname") as ssh_fn:
        classify_network_info(
            network_info,
            ip_address="10.0.0.50",
            existing=device,
            try_ssh=True,
        )

    ssh_fn.assert_not_called()


def test_fetch_ssh_hostname_returns_empty_on_failure():
    device = _device()
    mock_collector = MagicMock()
    mock_collector.connect.side_effect = RuntimeError("connection refused")

    with patch(
        "services.interface_collection.ssh_collector.SSHInterfaceCollector",
        return_value=mock_collector,
    ):
        assert fetch_ssh_hostname(device) == ""
