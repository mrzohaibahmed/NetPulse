"""Unit tests for the device discovery classification engine."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.discovery.apply import merge_hostname
from services.discovery.classifier import (
    ClassificationEvidence,
    classify_device,
    evidence_from_network_info,
    is_unknown_hostname,
    log_classification,
    resolve_hostname,
)


def _port(num: int, service: str = "", product: str = "", state: str = "open") -> dict:
    return {
        "port": num,
        "protocol": "tcp",
        "state": state,
        "service": service,
        "product": product,
        "version": "",
        "extraInfo": "",
    }


def test_cisco_switch_high_confidence():
    evidence = ClassificationEvidence(
        ip_address="192.168.1.10",
        hostname_ptr="core-sw1.lab.local",
        vendor="Cisco Systems",
        mac_vendor="Cisco Systems",
        os_name="Cisco IOS XE",
        os_family="IOS",
        nmap_device_type="switch",
        ports=[
            _port(22, "ssh", "Cisco SSH"),
            _port(80, "http"),
            _port(443, "https"),
            _port(161, "snmp"),
        ],
        services=["ssh", "http", "https", "snmp"],
        products=["Cisco SSH"],
    )
    result = classify_device(evidence)
    assert result.hostname == "core-sw1.lab.local"
    assert "Cisco" in result.vendor
    assert result.device_type == "Managed Switch"
    assert result.confidence > 90
    assert result.operating_system == "Cisco IOS XE"


def test_windows_pc():
    evidence = ClassificationEvidence(
        ip_address="192.168.1.50",
        hostname_ptr="DESKTOP-ABC123",
        vendor="Dell Inc.",
        os_name="Microsoft Windows 10",
        os_family="Windows",
        nmap_device_type="general purpose",
        ports=[
            _port(139, "netbios-ssn"),
            _port(445, "microsoft-ds"),
            _port(3389, "ms-wbt-server"),
        ],
        services=["microsoft-ds", "ms-wbt-server", "netbios-ssn"],
    )
    result = classify_device(evidence)
    assert result.hostname == "DESKTOP-ABC123"
    assert result.device_type == "Windows PC"
    assert result.confidence >= 70


def test_printer_hp():
    evidence = ClassificationEvidence(
        ip_address="192.168.1.20",
        hostname_ptr="hp-laserjet.local",
        vendor="Hewlett Packard",
        os_name="",
        nmap_device_type="printer",
        ports=[_port(9100, "jetdirect", "HP JetDirect"), _port(80, "http")],
        services=["jetdirect", "http"],
        products=["HP JetDirect"],
    )
    result = classify_device(evidence)
    assert result.vendor
    assert result.device_type == "Printer"
    assert result.confidence >= 60


def test_unknown_device_low_confidence():
    evidence = ClassificationEvidence(
        ip_address="192.168.1.99",
        hostname_ptr="",
        vendor="",
        os_name="",
        ports=[],
        services=[],
    )
    result = classify_device(evidence)
    assert result.hostname == "Unknown"
    assert result.device_type == "Unknown Device"
    assert result.confidence <= 30


def test_never_overwrite_hostname_with_unknown():
    assert merge_hostname({"hostname": "existing-sw"}, "Unknown") == "existing-sw"
    assert merge_hostname({"hostname": "existing-sw"}, "Unknown Device") == "existing-sw"
    assert merge_hostname({"hostname": "Unknown"}, "core-sw1") == "core-sw1"
    assert merge_hostname(None, "Unknown") == "Unknown"


def test_hostname_priority_ptr_over_service():
    evidence = ClassificationEvidence(
        hostname_ptr="from-ptr.local",
        hostname_service="from-service",
        hostname_ssh="from-ssh",
    )
    name, source = resolve_hostname(evidence)
    assert name == "from-ptr.local"
    assert source == "nmap-ptr"


def test_hostname_priority_ssh_when_no_ptr():
    evidence = ClassificationEvidence(
        hostname_ptr="",
        hostname_service="",
        hostname_ssh="sw-core-01",
        hostname_existing="Unknown",
    )
    name, source = resolve_hostname(evidence)
    assert name == "sw-core-01"
    assert source == "ssh"


def test_evidence_from_network_info_hikvision():
    network_info = {
        "hostname": "cam1.local",
        "vendor": "Hikvision Digital Technology",
        "os": {"name": "", "family": "", "generation": "", "accuracy": ""},
        "deviceType": "",
        "ports": [_port(554, "rtsp"), _port(8000, "http-alt")],
        "services": ["rtsp"],
    }
    evidence = evidence_from_network_info(network_info, ip_address="10.0.0.5")
    result = classify_device(evidence)
    assert result.device_type == "IP Camera"
    assert result.hostname == "cam1.local"


def test_is_unknown_hostname():
    assert is_unknown_hostname("Unknown")
    assert is_unknown_hostname("unknown device")
    assert not is_unknown_hostname("sw1")


def test_linux_server():
    evidence = ClassificationEvidence(
        hostname_ptr="app-server-01",
        vendor="Dell Inc.",
        os_name="Linux 5.15",
        os_family="Linux",
        ports=[_port(22, "ssh"), _port(80, "http"), _port(443, "https")],
        services=["ssh", "http", "https"],
    )
    result = classify_device(evidence)
    assert result.device_type == "Linux Server"
    assert 80 <= result.confidence <= 95


def test_hypervisor_esxi():
    evidence = ClassificationEvidence(
        hostname_ptr="esxi01",
        vendor="VMware",
        os_name="VMware ESXi",
        ports=[_port(443, "https"), _port(902, "vmware-auth")],
        services=["https"],
    )
    result = classify_device(evidence)
    assert result.device_type == "Hypervisor"
    assert result.confidence >= 70


def test_cisco_router_routing_features():
    evidence = ClassificationEvidence(
        hostname_ptr="edge-rtr",
        vendor="Cisco",
        os_name="Cisco IOS",
        nmap_device_type="router",
        ports=[_port(22, "ssh"), _port(161, "snmp")],
        services=["ssh", "snmp"],
        products=["Cisco ISR Router"],
    )
    result = classify_device(evidence)
    assert result.device_type == "Router"
    assert result.confidence >= 60


def test_rescan_does_not_require_duplicate_logic():
    """Existing hostname preserved when classification returns Unknown."""
    existing = {"hostname": "kept-hostname", "ipAddress": "10.0.0.1"}
    evidence = ClassificationEvidence(
        hostname_existing=existing["hostname"],
        ports=[],
        services=[],
    )
    result = classify_device(evidence)
    merged = merge_hostname(existing, result.hostname)
    assert merged == "kept-hostname"


def test_cisco_mixed_routing_classifies_as_router():
    """routing_hints + switch_hints must hit cisco-mixed-routing, not cisco-switch."""
    evidence = ClassificationEvidence(
        ip_address="10.0.0.20",
        hostname_ptr="core-rtr-sw",
        vendor="Cisco Systems",
        os_name="Cisco IOS",
        os_family="IOS",
        nmap_device_type="router",
        ports=[_port(22, "ssh"), _port(161, "snmp")],
        services=["ssh", "snmp"],
        products=["Cisco Catalyst Switch Router"],
    )
    result = classify_device(evidence)
    assert result.device_type == "Router"
    assert result.classification_method == "cisco-mixed-routing"
    assert 80 <= result.confidence <= 95


def test_confidence_vendor_only_band():
    evidence = ClassificationEvidence(
        vendor="Cisco Systems",
        ports=[],
        services=[],
    )
    result = classify_device(evidence)
    assert result.classification_method == "vendor-only"
    assert 40 <= result.confidence <= 60


def test_confidence_vendor_os_ports_services_band():
    evidence = ClassificationEvidence(
        vendor="Cisco Systems",
        os_name="Cisco IOS XE",
        os_family="IOS",
        nmap_device_type="switch",
        ports=[_port(22, "ssh"), _port(161, "snmp")],
        services=["ssh", "snmp"],
    )
    result = classify_device(evidence)
    assert result.classification_method == "cisco-switch"
    assert 92 <= result.confidence <= 95


def test_confidence_vendor_os_band():
    evidence = ClassificationEvidence(
        vendor="Cisco",
        os_name="Cisco IOS",
        os_family="IOS",
        nmap_device_type="router",
        products=["Cisco ISR Router"],
        ports=[],
        services=[],
    )
    result = classify_device(evidence)
    assert result.classification_method == "cisco-router"
    assert 65 <= result.confidence <= 80


def test_competing_rules_highest_score_wins():
    evidence = ClassificationEvidence(
        vendor="VMware",
        os_name="VMware ESXi 7.0",
        ports=[
            _port(9100, "jetdirect"),
            _port(443, "https"),
            _port(902, "vmware-auth"),
        ],
        services=["jetdirect", "https"],
        products=["HP JetDirect", "VMware ESXi"],
        nmap_device_type="specialized",
    )
    result = classify_device(evidence)
    assert result.device_type == "Hypervisor"
    assert result.classification_method == "hypervisor-fingerprint"


def test_tie_break_preserves_first_highest_rule():
    """Equal scores: stable sort keeps the first-appended match."""
    from services.discovery.classifier import _RuleMatch

    matches = [
        _RuleMatch("Router", 88, "cisco-mixed-routing", ("routing", "switch")),
        _RuleMatch("Managed Switch", 88, "cisco-switch", ("switch-or-cisco",)),
    ]
    matches.sort(key=lambda m: m.score, reverse=True)
    assert matches[0].method == "cisco-mixed-routing"
    assert matches[0].device_type == "Router"


def test_log_classification_includes_metadata():
    evidence = ClassificationEvidence(
        ip_address="10.0.0.5",
        hostname_ptr="sw1.local",
        vendor="Cisco Systems",
        os_name="Cisco IOS",
        ports=[_port(22, "ssh")],
        services=["ssh"],
    )
    result = classify_device(evidence)
    mock_logger = MagicMock()

    log_classification(mock_logger, "10.0.0.5", evidence, result)

    mock_logger.info.assert_called_once()
    fmt, device_label, rule, confidence, signals, host_source, dtype = (
        mock_logger.info.call_args[0]
    )
    assert "[CLASSIFICATION]" in fmt
    assert device_label == "sw1.local/10.0.0.5"
    assert rule == "cisco-switch"
    assert confidence == result.confidence
    assert "ssh" in signals
    assert host_source == "nmap-ptr"
    assert dtype == result.device_type
