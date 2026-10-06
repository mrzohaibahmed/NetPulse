"""
Unit and Integration Tests for Phase 3E Server Hardware Health Evaluator.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.server_hardware import (
    ComponentStatus,
    CoolingFan,
    LogicalVolume,
    PhysicalDrive,
    PowerSupply,
    ServerCapabilities,
    ServerHardware,
    ServerMemoryDimm,
    ServerNetworkInterface,
    ServerPowerSummary,
    ServerProcessor,
    ServerStorage,
    StorageController,
    TemperatureSensor,
    evaluate_server_hardware_health,
    normalize_server_hardware,
    resolve_component_health,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "redfish"


@pytest.fixture
def ilo4_root():
    return json.loads((FIXTURES_DIR / "ilo4_dl380.json").read_text(encoding="utf-8"))


@pytest.fixture
def ilo5_root():
    return json.loads((FIXTURES_DIR / "ilo5_dl380.json").read_text(encoding="utf-8"))


@pytest.fixture
def ilo6_root():
    return json.loads((FIXTURES_DIR / "ilo6_dl380.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Status Precedence Tests
# ---------------------------------------------------------------------------

def test_health_rollup_takes_precedence_over_health():
    """health_rollup takes precedence over health (e.g. health_rollup=Critical, health=OK -> CRITICAL)."""
    status = ComponentStatus(health="OK", health_rollup="Critical")
    assert resolve_component_health(status) == "CRITICAL"


def test_health_used_when_health_rollup_missing():
    """health is used when health_rollup is None."""
    status = ComponentStatus(health="Warning", health_rollup=None)
    assert resolve_component_health(status) == "WARNING"


def test_missing_health_is_unknown():
    """None status or None health resolves to UNKNOWN."""
    assert resolve_component_health(None) == "UNKNOWN"
    assert resolve_component_health(ComponentStatus()) == "UNKNOWN"


def test_unrecognized_health_is_unknown():
    """Unrecognized health strings resolve to UNKNOWN."""
    assert resolve_component_health(ComponentStatus(health="CustomState")) == "UNKNOWN"


# ---------------------------------------------------------------------------
# 2. Basic Component Subsystem Health Evaluation
# ---------------------------------------------------------------------------

def test_evaluator_all_ok():
    """All healthy components produce overall_health == OK."""
    hw = ServerHardware(
        capabilities=ServerCapabilities(has_processors=True, has_memory=True),
        processors=[ServerProcessor(id="cpu1", status=ComponentStatus(health="OK"))],
        memory=[ServerMemoryDimm(id="dimm1", status=ComponentStatus(health="OK"))],
    )
    health = evaluate_server_hardware_health(hw)
    assert health.overall_health == "OK"
    assert health.subsystems["processors"].status == "OK"
    assert health.subsystems["memory"].status == "OK"
    assert len(health.summary_reasons) == 0


def test_evaluator_dimm_warning():
    """Single DIMM warning produces overall_health == WARNING."""
    hw = ServerHardware(
        capabilities=ServerCapabilities(has_memory=True),
        memory=[
            ServerMemoryDimm(id="dimm1", name="DIMM 1", status=ComponentStatus(health="OK")),
            ServerMemoryDimm(id="dimm2", name="DIMM 2", status=ComponentStatus(health="Warning")),
        ],
    )
    health = evaluate_server_hardware_health(hw)
    assert health.overall_health == "WARNING"
    assert health.subsystems["memory"].status == "WARNING"
    assert health.subsystems["memory"].warning_components == 1
    assert "Memory DIMM 'DIMM 2' reports WARNING" in health.summary_reasons


def test_evaluator_disk_critical():
    """Physical drive failure produces overall_health == CRITICAL."""
    hw = ServerHardware(
        capabilities=ServerCapabilities(has_storage=True),
        storage=ServerStorage(
            physical_drives=[
                PhysicalDrive(id="drive1", name="Bay 1", status=ComponentStatus(health="Critical"))
            ]
        ),
    )
    health = evaluate_server_hardware_health(hw)
    assert health.overall_health == "CRITICAL"
    assert health.subsystems["storage"].status == "CRITICAL"
    assert health.subsystems["storage"].critical_components == 1
    assert "Physical drive 'Bay 1' reports CRITICAL" in health.summary_reasons


def test_evaluator_raid_degraded():
    """Logical RAID volume in Warning/Degraded state produces overall_health == WARNING."""
    hw = ServerHardware(
        capabilities=ServerCapabilities(has_smart_storage=True),
        storage=ServerStorage(
            logical_volumes=[
                LogicalVolume(id="vol1", name="Array 1", status=ComponentStatus(health="Warning"))
            ]
        ),
    )
    health = evaluate_server_hardware_health(hw)
    assert health.overall_health == "WARNING"
    assert health.subsystems["storage"].status == "WARNING"


def test_evaluator_thermal_overtemp():
    """Thermal sensor exceeding upper_threshold_critical produces overall_health == CRITICAL."""
    hw = ServerHardware(
        capabilities=ServerCapabilities(has_thermals=True),
        temperatures=[
            TemperatureSensor(
                id="temp1",
                name="CPU Temp",
                reading_celsius=85.0,
                upper_threshold_critical=70.0,
                status=ComponentStatus(health="OK"),
            )
        ],
    )
    health = evaluate_server_hardware_health(hw)
    assert health.overall_health == "CRITICAL"
    assert health.subsystems["thermals"].status == "CRITICAL"
    assert any("exceeds critical threshold" in r for r in health.summary_reasons)


def test_evaluator_fan_failed():
    """Cooling fan in Critical state produces overall_health == CRITICAL."""
    hw = ServerHardware(
        capabilities=ServerCapabilities(has_fans=True),
        fans=[CoolingFan(id="fan1", name="Fan 1", status=ComponentStatus(health="Critical"))],
    )
    health = evaluate_server_hardware_health(hw)
    assert health.overall_health == "CRITICAL"
    assert health.subsystems["fans"].status == "CRITICAL"


# ---------------------------------------------------------------------------
# 3. Capability Discovery vs Missing Telemetry Tests
# ---------------------------------------------------------------------------

def test_evaluator_missing_thermals_capability():
    """has_thermals=False and empty thermals does NOT degrade overall_health when available subsystems are OK."""
    hw = ServerHardware(
        capabilities=ServerCapabilities(has_processors=True, has_thermals=False),
        processors=[ServerProcessor(id="cpu1", status=ComponentStatus(health="OK"))],
        temperatures=[],
    )
    health = evaluate_server_hardware_health(hw)
    assert health.overall_health == "OK"
    assert health.subsystems["thermals"].status == "UNKNOWN"


def test_evaluator_empty_hardware():
    """Completely empty hardware object evaluates as UNKNOWN."""
    hw = ServerHardware()
    health = evaluate_server_hardware_health(hw)
    assert health.overall_health == "UNKNOWN"


# ---------------------------------------------------------------------------
# 4. Power State & Network Interface Tests
# ---------------------------------------------------------------------------

def test_evaluator_power_off_healthy():
    """Server powered off (power_state='Off') retains overall_health == OK if available components are OK."""
    hw = ServerHardware(
        capabilities=ServerCapabilities(has_power=True, has_processors=True),
        power=ServerPowerSummary(power_state="Off"),
        processors=[ServerProcessor(id="cpu1", status=ComponentStatus(health="OK"))],
    )
    health = evaluate_server_hardware_health(hw)
    assert health.overall_health == "OK"
    assert health.power_state == "Off"


def test_link_down_does_not_alone_mean_hardware_failure():
    """Network link_status='LinkDown' with health='OK' remains hardware health OK."""
    hw = ServerHardware(
        capabilities=ServerCapabilities(has_network=True),
        network_interfaces=[
            ServerNetworkInterface(
                id="eth0",
                name="Host NIC 1",
                link_status="LinkDown",
                status=ComponentStatus(health="OK"),
            )
        ],
    )
    health = evaluate_server_hardware_health(hw)
    assert health.overall_health == "OK"
    assert health.subsystems["network"].status == "OK"


def test_network_critical_affects_overall_health():
    """Network interface with status.health='Critical' affects overall_health."""
    hw = ServerHardware(
        capabilities=ServerCapabilities(has_network=True),
        network_interfaces=[
            ServerNetworkInterface(
                id="eth0",
                name="Host NIC 1",
                status=ComponentStatus(health="Critical"),
            )
        ],
    )
    health = evaluate_server_hardware_health(hw)
    assert health.overall_health == "CRITICAL"
    assert health.subsystems["network"].status == "CRITICAL"


# ---------------------------------------------------------------------------
# 5. Precedence & Rollup Rule Tests
# ---------------------------------------------------------------------------

def test_critical_overrides_warning():
    """CRITICAL subsystem overrides WARNING subsystem in overall_health."""
    hw = ServerHardware(
        capabilities=ServerCapabilities(has_memory=True, has_storage=True),
        memory=[ServerMemoryDimm(id="dimm1", status=ComponentStatus(health="Warning"))],
        storage=ServerStorage(
            physical_drives=[PhysicalDrive(id="drive1", status=ComponentStatus(health="Critical"))]
        ),
    )
    health = evaluate_server_hardware_health(hw)
    assert health.overall_health == "CRITICAL"


def test_warning_overrides_unknown():
    """WARNING subsystem overrides UNKNOWN subsystem in overall_health."""
    hw = ServerHardware(
        capabilities=ServerCapabilities(has_memory=True, has_processors=True),
        memory=[ServerMemoryDimm(id="dimm1", status=ComponentStatus(health="Warning"))],
        processors=[ServerProcessor(id="cpu1", status=ComponentStatus(health=None))],
    )
    health = evaluate_server_hardware_health(hw)
    assert health.overall_health == "WARNING"


# ---------------------------------------------------------------------------
# 6. Fixture Compatibility Tests (iLO 4 / 5 / 6)
# ---------------------------------------------------------------------------

def test_evaluator_ilo4_fixture_health(ilo4_root):
    """Evaluates normalized iLO 4 fixture hardware health."""
    hw = normalize_server_hardware(service_root_data=ilo4_root)
    health = evaluate_server_hardware_health(hw)
    assert health.overall_health in ("OK", "WARNING", "CRITICAL", "UNKNOWN")
    assert "power" in health.subsystems


def test_evaluator_ilo5_fixture_health(ilo5_root):
    """Evaluates normalized iLO 5 fixture hardware health."""
    hw = normalize_server_hardware(service_root_data=ilo5_root)
    health = evaluate_server_hardware_health(hw)
    assert health.overall_health in ("OK", "WARNING", "CRITICAL", "UNKNOWN")


def test_evaluator_ilo6_fixture_health(ilo6_root):
    """Evaluates normalized iLO 6 fixture hardware health."""
    hw = normalize_server_hardware(service_root_data=ilo6_root)
    health = evaluate_server_hardware_health(hw)
    assert health.overall_health in ("OK", "WARNING", "CRITICAL", "UNKNOWN")


# ---------------------------------------------------------------------------
# 7. Purity, Determinism & Security Tests
# ---------------------------------------------------------------------------

def test_evaluator_is_deterministic():
    """Identical ServerHardware input produces identical ServerHardwareHealth output."""
    hw = ServerHardware(
        capabilities=ServerCapabilities(has_processors=True),
        processors=[ServerProcessor(id="cpu1", status=ComponentStatus(health="OK"))],
    )
    res1 = evaluate_server_hardware_health(hw)
    res2 = evaluate_server_hardware_health(hw)
    assert res1.to_dict() == res2.to_dict()


def test_evaluator_does_not_mutate_hardware():
    """evaluate_server_hardware_health does not mutate the input ServerHardware dataclass."""
    hw = ServerHardware(
        capabilities=ServerCapabilities(has_processors=True),
        processors=[ServerProcessor(id="cpu1", status=ComponentStatus(health="OK"))],
    )
    dict_before = hw.to_dict()
    evaluate_server_hardware_health(hw)
    assert hw.to_dict() == dict_before


def test_evaluator_contains_no_secrets():
    """Output dict contains no passwords, credentials, tokens, or raw Redfish payloads."""
    hw = ServerHardware(
        capabilities=ServerCapabilities(has_processors=True),
        processors=[ServerProcessor(id="cpu1", status=ComponentStatus(health="OK"))],
    )
    health = evaluate_server_hardware_health(hw)
    raw_str = json.dumps(health.to_dict())

    assert "password" not in raw_str.lower()
    assert "x-auth-token" not in raw_str.lower()
    assert "@odata" not in raw_str
