"""Unit tests for the Phase 2 identification framework."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if "config.database" not in sys.modules:
    _db_mod = types.ModuleType("config.database")
    _db_mod.db = MagicMock()
    _db_mod.MAX_SCAN_THREADS = 5
    sys.modules["config.database"] = _db_mod

from services.discovery.classifier import ClassificationResult
from services.discovery.identification import (
    CameraIdentifier,
    IdentificationContext,
    IdentificationManager,
    IdentificationResult,
    NmapIdentifier,
    SNMPIdentifier,
    SSHIdentifier,
    WindowsIdentifier,
)


def _classification(**overrides) -> ClassificationResult:
    base = dict(
        hostname="host1",
        vendor="Cisco Systems",
        operating_system="Cisco IOS XE",
        device_type="Managed Switch",
        confidence=95,
        classification_method="cisco-switch",
        discovery_source="nmap",
        signals_matched=["vendor", "ssh"],
        canonical_type="SWITCH",
        identification_evidence={"vendor": "Cisco Systems", "ports": [22, 161]},
        identification_method="nmap",
    )
    base.update(overrides)
    return ClassificationResult(**base)


def test_nmap_identifier_success():
    identifier = NmapIdentifier()
    context = IdentificationContext(
        ip_address="10.0.0.10",
        network_info={"hostname": "sw1", "ports": [], "services": [], "os": {}},
        existing={"deviceType": "Managed Switch"},
    )

    with patch(
        "services.discovery.apply.classify_network_info",
        return_value=(_classification(), {"raw": "evidence"}),
    ) as classify_fn:
        result = identifier.identify(context)

    classify_fn.assert_called_once()
    assert result.success is True
    assert result.method == "nmap"
    assert result.device_type == "Managed Switch"
    assert result.confidence == 95
    assert result.classification is not None
    assert result.metadata["canonicalType"] == "SWITCH"


def test_nmap_identifier_failure():
    identifier = NmapIdentifier()
    context = IdentificationContext(ip_address="10.0.0.11", network_info={"os": {}, "ports": [], "services": []})

    with patch(
        "services.discovery.apply.classify_network_info",
        side_effect=RuntimeError("nmap classification failed"),
    ):
        result = identifier.identify(context)

    assert result.success is False
    assert result.method == "nmap"
    assert "failed" in (result.error or "")


def test_nmap_identifier_unknown_result_still_succeeds():
    identifier = NmapIdentifier()
    context = IdentificationContext(ip_address="10.0.0.12", network_info={"os": {}, "ports": [], "services": []})

    with patch(
        "services.discovery.apply.classify_network_info",
        return_value=(
            _classification(
                device_type="Unknown Device",
                confidence=20,
                classification_method="unknown",
                canonical_type="UNKNOWN",
                identification_evidence={},
            ),
            {"raw": "unknown"},
        ),
    ):
        result = identifier.identify(context)

    assert result.success is True
    assert result.device_type == "Unknown Device"
    assert result.confidence == 20


def test_future_identifier_stubs_are_non_operational():
    context = IdentificationContext(ip_address="10.0.0.20")
    for identifier_cls in (SSHIdentifier,):
        result = identifier_cls().identify(context)
        assert result.success is False
        assert result.metadata["future"] is True
        assert "not implemented" in (result.error or "").lower()


def test_manager_plan_methods_by_device_type():
    manager = IdentificationManager()

    assert manager.plan_methods(IdentificationContext(ip_address="1.1.1.1")) == ["nmap"]
    assert manager.plan_methods(
        IdentificationContext(ip_address="1.1.1.1", preferred_device_type="Printer")
    ) == ["snmp", "nmap"]
    assert manager.plan_methods(
        IdentificationContext(ip_address="1.1.1.1", preferred_device_type="Windows PC")
    ) == ["windows", "nmap"]
    assert manager.plan_methods(
        IdentificationContext(ip_address="1.1.1.1", preferred_device_type="Managed Switch")
    ) == ["snmp", "ssh", "nmap"]
    assert manager.plan_methods(
        IdentificationContext(ip_address="1.1.1.1", preferred_device_type="IP Camera")
    ) == ["camera", "nmap"]


def test_manager_falls_back_to_nmap_after_stub_failure():
    nmap = MagicMock()
    nmap.identify.return_value = types.SimpleNamespace(
        success=True,
        method="nmap",
        device_type="Printer",
        confidence=88,
        evidence={"vendor": "HP"},
        metadata={"implemented": True},
        error=None,
        classification=_classification(
            device_type="Printer",
            confidence=88,
            classification_method="printer-fingerprint",
            canonical_type="PRINTER",
            identification_evidence={"vendor": "HP"},
            identification_method="nmap",
        ),
        raw_evidence={"raw": "nmap"},
    )
    snmp_stub = MagicMock(spec=SNMPIdentifier)
    snmp_stub.supports.return_value = True
    snmp_stub.identify.return_value = IdentificationResult(
        success=False,
        method="snmp",
        device_type="Unknown Device",
        confidence=0,
        error="snmp unreachable",
    )
    manager = IdentificationManager({"snmp": snmp_stub, "nmap": nmap})

    result = manager.identify(
        IdentificationContext(
            ip_address="10.0.0.30",
            preferred_device_type="Printer",
            network_info={"os": {}, "ports": [], "services": []},
        )
    )

    nmap.identify.assert_called_once()
    assert result.success is True
    assert result.method == "nmap"
    assert result.metadata["attemptedMethods"] == ["snmp", "nmap"]
    assert result.metadata["plannedMethods"] == ["snmp", "nmap"]


def test_manager_returns_last_failure_when_all_fail():
    nmap = MagicMock()
    nmap.identify.return_value = types.SimpleNamespace(
        success=False,
        method="nmap",
        device_type="Unknown Device",
        confidence=0,
        evidence={},
        metadata={"implemented": True},
        error="nmap unavailable",
        classification=None,
        raw_evidence=None,
    )
    win_stub = MagicMock(spec=WindowsIdentifier)
    win_stub.supports.return_value = True
    win_stub.identify.return_value = IdentificationResult(
        success=False,
        method="wmi",
        device_type="Unknown Device",
        confidence=0,
        error="winrm failed",
    )
    manager = IdentificationManager({"windows": win_stub, "nmap": nmap})

    result = manager.identify(
        IdentificationContext(
            ip_address="10.0.0.40",
            preferred_device_type="Windows PC",
            network_info={"os": {}, "ports": [], "services": []},
        )
    )

    nmap.identify.assert_called_once()
    assert result.success is False
    assert result.method == "nmap"
    assert result.metadata["attemptedMethods"] == ["windows", "nmap"]


def test_nmap_identifier_no_network_info():
    """NmapIdentifier returns failure when network_info is None."""
    identifier = NmapIdentifier()
    context = IdentificationContext(ip_address="10.0.0.50", network_info=None)

    result = identifier.identify(context)

    assert result.success is False
    assert result.method == "nmap"
    assert result.device_type == "Unknown Device"
    assert result.confidence == 0
    assert "unavailable" in (result.error or "").lower()


def test_base_identifier_supports_returns_true():
    """BaseIdentifier.supports() defaults to True for stubs/nmap."""
    for cls in (NmapIdentifier, SSHIdentifier):
        identifier = cls()
        context = IdentificationContext(ip_address="10.0.0.60")
        assert identifier.supports(context) is True


def test_stub_method_names():
    """Each identifier exposes its expected method_name."""
    assert WindowsIdentifier().method_name == "wmi"
    assert SNMPIdentifier().method_name == "snmp"
    assert SSHIdentifier().method_name == "ssh"
    assert CameraIdentifier().method_name == "onvif"
    assert NmapIdentifier().method_name == "nmap"


def test_manager_plan_methods_router():
    manager = IdentificationManager()
    result = manager.plan_methods(
        IdentificationContext(ip_address="1.1.1.1", preferred_device_type="Router")
    )
    assert result == ["snmp", "ssh", "nmap"]


def test_manager_plan_methods_firewall():
    manager = IdentificationManager()
    result = manager.plan_methods(
        IdentificationContext(ip_address="1.1.1.1", preferred_device_type="Firewall")
    )
    assert result == ["snmp", "ssh", "nmap"]


def test_manager_plan_methods_access_point():
    manager = IdentificationManager()
    result = manager.plan_methods(
        IdentificationContext(ip_address="1.1.1.1", preferred_device_type="Access Point")
    )
    assert result == ["snmp", "ssh", "nmap"]


def test_manager_plan_methods_laptop():
    manager = IdentificationManager()
    result = manager.plan_methods(
        IdentificationContext(ip_address="1.1.1.1", preferred_device_type="Laptop")
    )
    assert result == ["windows", "nmap"]


def test_manager_plan_methods_unknown_type_defaults_to_nmap():
    manager = IdentificationManager()
    result = manager.plan_methods(
        IdentificationContext(ip_address="1.1.1.1", preferred_device_type="Unknown Device")
    )
    assert result == ["nmap"]


def test_identification_result_defaults():
    """IdentificationResult carries correct defaults."""
    from services.discovery.identification import IdentificationResult

    r = IdentificationResult(success=False, method="test")
    assert r.device_type == "Unknown Device"
    assert r.confidence == 0
    assert r.evidence == {}
    assert r.metadata == {}
    assert r.error is None
    assert r.classification is None
    assert r.raw_evidence is None


def test_manager_no_matching_identifiers():
    """Manager returns failure when none of the identifiers can be matched."""
    # Pass a dict with a key that plan_methods never selects
    manager = IdentificationManager(identifiers={"nonexistent": WindowsIdentifier()})
    # Override plan_methods to return a method not in the identifiers dict
    original_plan = manager.plan_methods

    def plan_empty(ctx):
        return ["unavailable_method"]

    manager.plan_methods = plan_empty
    result = manager.identify(IdentificationContext(ip_address="10.0.0.70"))

    assert result.success is False
    assert result.method == "none"
    assert "No identifier" in (result.error or "")



def test_manager_metadata_includes_planned_methods():
    """Successful result metadata contains both planned and attempted methods."""
    nmap = MagicMock()
    nmap.supports.return_value = True
    nmap.identify.return_value = types.SimpleNamespace(
        success=True,
        method="nmap",
        device_type="Linux Server",
        confidence=85,
        evidence={"os": "Linux"},
        metadata={"implemented": True},
        error=None,
        classification=_classification(
            device_type="Linux Server",
            confidence=85,
            classification_method="linux-server",
            canonical_type="SERVER",
        ),
        raw_evidence={},
    )
    manager = IdentificationManager(identifiers={"nmap": nmap})
    result = manager.identify(IdentificationContext(ip_address="10.0.0.80"))

    assert result.metadata["plannedMethods"] == ["nmap"]
    assert result.metadata["attemptedMethods"] == ["nmap"]
