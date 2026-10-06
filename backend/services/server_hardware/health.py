"""
Deterministic Server Hardware Health Evaluation Service (Phase 3E).

Evaluates normalized ServerHardware dataclass instances to derive component-level,
subsystem-level, and aggregate server hardware health summaries.
Operating exclusively on Phase 3B normalized models. Pure function layer without I/O,
database calls, Redfish requests, or side effects.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from services.server_hardware.models import (
    ComponentStatus,
    CoolingFan,
    LogicalVolume,
    PhysicalDrive,
    PowerSupply,
    ServerHardware,
    ServerMemoryDimm,
    ServerNetworkInterface,
    ServerProcessor,
    StorageController,
    TemperatureSensor,
)


@dataclass
class SubsystemHealth:
    """Health summary and metrics for an individual hardware subsystem."""

    name: str
    status: str  # "OK", "WARNING", "CRITICAL", "UNKNOWN"
    total_components: int
    healthy_components: int
    warning_components: int
    critical_components: int
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServerHardwareHealth:
    """Aggregate operational health summary for a server endpoint."""

    overall_health: str  # "OK", "WARNING", "CRITICAL", "UNKNOWN"
    power_state: str | None
    summary_reasons: list[str] = field(default_factory=list)
    subsystems: dict[str, SubsystemHealth] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        res["subsystems"] = {k: v.to_dict() for k, v in self.subsystems.items()}
        return res


def resolve_component_health(status: ComponentStatus | None) -> str:
    """
    Resolve health status for a component with precedence:
    1. health_rollup (if present)
    2. health (if present)
    3. UNKNOWN

    Case-insensitively normalizes to "OK", "WARNING", "CRITICAL", or "UNKNOWN".
    """
    if status is None or not isinstance(status, ComponentStatus):
        return "UNKNOWN"

    raw_val = status.health_rollup if status.health_rollup is not None else status.health
    if raw_val is None:
        return "UNKNOWN"

    text = str(raw_val).strip().upper()
    if text == "OK":
        return "OK"
    if text in ("WARNING", "DEGRADED"):
        return "WARNING"
    if text in ("CRITICAL", "FAILED"):
        return "CRITICAL"

    return "UNKNOWN"


def evaluate_server_hardware_health(hardware: ServerHardware) -> ServerHardwareHealth:
    """
    Pure, deterministic evaluation of normalized ServerHardware health.

    Derives subsystem metrics for power, processors, memory, storage, network, thermals,
    and fans, and computes the aggregate overall server health.
    """
    if not isinstance(hardware, ServerHardware):
        return ServerHardwareHealth(
            overall_health="UNKNOWN",
            power_state=None,
            summary_reasons=["Invalid ServerHardware object"],
            subsystems={},
        )

    caps = hardware.capabilities
    subsystems: dict[str, SubsystemHealth] = {}

    # 1. Power Subsystem
    subsystems["power"] = _evaluate_power(hardware.power.power_supplies, caps.has_power)

    # 2. Processors Subsystem
    subsystems["processors"] = _evaluate_processors(hardware.processors, caps.has_processors)

    # 3. Memory Subsystem
    subsystems["memory"] = _evaluate_memory(hardware.memory, caps.has_memory)

    # 4. Storage Subsystem
    subsystems["storage"] = _evaluate_storage(hardware.storage, caps.has_storage or caps.has_smart_storage)

    # 5. Network Subsystem
    subsystems["network"] = _evaluate_network(hardware.network_interfaces, caps.has_network)

    # 6. Thermals Subsystem
    subsystems["thermals"] = _evaluate_thermals(hardware.temperatures, caps.has_thermals)

    # 7. Fans Subsystem
    subsystems["fans"] = _evaluate_fans(hardware.fans, caps.has_fans)

    # Determine Aggregate Overall Health
    overall_health, summary_reasons = _compute_aggregate_health(subsystems, caps)

    power_state = hardware.power.power_state if hardware.power else None

    return ServerHardwareHealth(
        overall_health=overall_health,
        power_state=power_state,
        summary_reasons=summary_reasons,
        subsystems=subsystems,
    )


def _evaluate_power(power_supplies: list[PowerSupply], has_power_capability: bool) -> SubsystemHealth:
    reasons: list[str] = []
    healthy = 0
    warning = 0
    critical = 0

    sorted_psus = sorted(power_supplies, key=lambda p: str(p.id))
    for ps in sorted_psus:
        h = resolve_component_health(ps.status)
        ps_name = ps.name or ps.id
        if h == "OK":
            healthy += 1
        elif h == "WARNING":
            warning += 1
            reasons.append(f"Power supply '{ps_name}' reports WARNING")
        elif h == "CRITICAL":
            critical += 1
            reasons.append(f"Power supply '{ps_name}' reports CRITICAL")

    total = len(power_supplies)
    status = _resolve_subsystem_status(total, healthy, warning, critical, has_power_capability)

    return SubsystemHealth(
        name="power",
        status=status,
        total_components=total,
        healthy_components=healthy,
        warning_components=warning,
        critical_components=critical,
        reasons=reasons,
    )


def _evaluate_processors(processors: list[ServerProcessor], has_capability: bool) -> SubsystemHealth:
    reasons: list[str] = []
    healthy = 0
    warning = 0
    critical = 0

    sorted_procs = sorted(processors, key=lambda p: str(p.id))
    for p in sorted_procs:
        h = resolve_component_health(p.status)
        p_name = p.name or p.id
        if h == "OK":
            healthy += 1
        elif h == "WARNING":
            warning += 1
            reasons.append(f"Processor '{p_name}' reports WARNING")
        elif h == "CRITICAL":
            critical += 1
            reasons.append(f"Processor '{p_name}' reports CRITICAL")

    total = len(processors)
    status = _resolve_subsystem_status(total, healthy, warning, critical, has_capability)

    return SubsystemHealth(
        name="processors",
        status=status,
        total_components=total,
        healthy_components=healthy,
        warning_components=warning,
        critical_components=critical,
        reasons=reasons,
    )


def _evaluate_memory(memory: list[ServerMemoryDimm], has_capability: bool) -> SubsystemHealth:
    reasons: list[str] = []
    healthy = 0
    warning = 0
    critical = 0

    sorted_dimms = sorted(memory, key=lambda m: str(m.id))
    for m in sorted_dimms:
        h = resolve_component_health(m.status)
        m_name = m.name or m.id
        if h == "OK":
            healthy += 1
        elif h == "WARNING":
            warning += 1
            reasons.append(f"Memory DIMM '{m_name}' reports WARNING")
        elif h == "CRITICAL":
            critical += 1
            reasons.append(f"Memory DIMM '{m_name}' reports CRITICAL")

    total = len(memory)
    status = _resolve_subsystem_status(total, healthy, warning, critical, has_capability)

    return SubsystemHealth(
        name="memory",
        status=status,
        total_components=total,
        healthy_components=healthy,
        warning_components=warning,
        critical_components=critical,
        reasons=reasons,
    )


def _evaluate_storage(storage: Any, has_capability: bool) -> SubsystemHealth:
    reasons: list[str] = []
    healthy = 0
    warning = 0
    critical = 0

    controllers = getattr(storage, "controllers", []) if storage else []
    drives = getattr(storage, "physical_drives", []) if storage else []
    volumes = getattr(storage, "logical_volumes", []) if storage else []

    total = len(controllers) + len(drives) + len(volumes)

    for c in sorted(controllers, key=lambda x: str(x.id)):
        h = resolve_component_health(c.status)
        c_name = c.name or c.id
        if h == "OK":
            healthy += 1
        elif h == "WARNING":
            warning += 1
            reasons.append(f"Storage controller '{c_name}' reports WARNING")
        elif h == "CRITICAL":
            critical += 1
            reasons.append(f"Storage controller '{c_name}' reports CRITICAL")

    for d in sorted(drives, key=lambda x: str(x.id)):
        h = resolve_component_health(d.status)
        d_name = d.name or d.id
        if h == "OK":
            healthy += 1
        elif h == "WARNING":
            warning += 1
            reasons.append(f"Physical drive '{d_name}' reports WARNING")
        elif h == "CRITICAL":
            critical += 1
            reasons.append(f"Physical drive '{d_name}' reports CRITICAL")

    for v in sorted(volumes, key=lambda x: str(x.id)):
        h = resolve_component_health(v.status)
        v_name = v.name or v.id
        if h == "OK":
            healthy += 1
        elif h == "WARNING":
            warning += 1
            reasons.append(f"Logical volume '{v_name}' reports WARNING")
        elif h == "CRITICAL":
            critical += 1
            reasons.append(f"Logical volume '{v_name}' reports CRITICAL")

    status = _resolve_subsystem_status(total, healthy, warning, critical, has_capability)

    return SubsystemHealth(
        name="storage",
        status=status,
        total_components=total,
        healthy_components=healthy,
        warning_components=warning,
        critical_components=critical,
        reasons=reasons,
    )


def _evaluate_network(interfaces: list[ServerNetworkInterface], has_capability: bool) -> SubsystemHealth:
    reasons: list[str] = []
    healthy = 0
    warning = 0
    critical = 0

    sorted_ifaces = sorted(interfaces, key=lambda i: str(i.id))
    for iface in sorted_ifaces:
        h = resolve_component_health(iface.status)
        iface_name = iface.name or iface.id
        if h == "OK":
            healthy += 1
        elif h == "WARNING":
            warning += 1
            reasons.append(f"Network interface '{iface_name}' reports WARNING")
        elif h == "CRITICAL":
            critical += 1
            reasons.append(f"Network interface '{iface_name}' reports CRITICAL")

    total = len(interfaces)
    status = _resolve_subsystem_status(total, healthy, warning, critical, has_capability)

    return SubsystemHealth(
        name="network",
        status=status,
        total_components=total,
        healthy_components=healthy,
        warning_components=warning,
        critical_components=critical,
        reasons=reasons,
    )


def _evaluate_thermals(temperatures: list[TemperatureSensor], has_capability: bool) -> SubsystemHealth:
    reasons: list[str] = []
    healthy = 0
    warning = 0
    critical = 0

    sorted_temps = sorted(temperatures, key=lambda t: str(t.id))
    for t in sorted_temps:
        t_name = t.name or t.id
        h = resolve_component_health(t.status)

        # Check explicit reading vs critical threshold
        if t.reading_celsius is not None and t.upper_threshold_critical is not None:
            if t.reading_celsius > t.upper_threshold_critical:
                h = "CRITICAL"
                reasons.append(
                    f"Temperature sensor '{t_name}' reading ({t.reading_celsius:.1f}°C) "
                    f"exceeds critical threshold ({t.upper_threshold_critical:.1f}°C)"
                )

        if h == "OK":
            healthy += 1
        elif h == "WARNING":
            warning += 1
            reasons.append(f"Temperature sensor '{t_name}' reports WARNING")
        elif h == "CRITICAL" and not any(t_name in r for r in reasons):
            critical += 1
            reasons.append(f"Temperature sensor '{t_name}' reports CRITICAL")
        elif h == "CRITICAL":
            critical += 1

    total = len(temperatures)
    status = _resolve_subsystem_status(total, healthy, warning, critical, has_capability)

    return SubsystemHealth(
        name="thermals",
        status=status,
        total_components=total,
        healthy_components=healthy,
        warning_components=warning,
        critical_components=critical,
        reasons=reasons,
    )


def _evaluate_fans(fans: list[CoolingFan], has_capability: bool) -> SubsystemHealth:
    reasons: list[str] = []
    healthy = 0
    warning = 0
    critical = 0

    sorted_fans = sorted(fans, key=lambda f: str(f.id))
    for f in sorted_fans:
        h = resolve_component_health(f.status)
        f_name = f.name or f.id
        if h == "OK":
            healthy += 1
        elif h == "WARNING":
            warning += 1
            reasons.append(f"Cooling fan '{f_name}' reports WARNING")
        elif h == "CRITICAL":
            critical += 1
            reasons.append(f"Cooling fan '{f_name}' reports CRITICAL")

    total = len(fans)
    status = _resolve_subsystem_status(total, healthy, warning, critical, has_capability)

    return SubsystemHealth(
        name="fans",
        status=status,
        total_components=total,
        healthy_components=healthy,
        warning_components=warning,
        critical_components=critical,
        reasons=reasons,
    )


def _resolve_subsystem_status(
    total: int, healthy: int, warning: int, critical: int, has_capability: bool
) -> str:
    """Resolve subsystem status based on component health counts and capability presence."""
    if critical > 0:
        return "CRITICAL"
    if warning > 0:
        return "WARNING"
    if total > 0 and healthy == total:
        return "OK"
    if total > 0:
        return "UNKNOWN"

    # total == 0
    return "UNKNOWN"


def _compute_aggregate_health(
    subsystems: dict[str, SubsystemHealth], caps: Any
) -> tuple[str, list[str]]:
    """
    Compute aggregate overall server health and summary reasons with precedence:
    CRITICAL > WARNING > UNKNOWN (for unresolved component health) > OK.

    Subsystems marked UNKNOWN solely due to unavailable capabilities or empty collections
    do NOT degrade overall health if all populated components across the server are healthy.
    If zero components exist across the entire server, overall health evaluates to UNKNOWN.
    """
    summary_reasons: list[str] = []
    has_critical = False
    has_warning = False
    has_unresolved_component = False
    total_components_across_server = sum(s.total_components for s in subsystems.values())

    for name, sub in subsystems.items():
        if sub.status == "CRITICAL":
            has_critical = True
            summary_reasons.extend(sub.reasons)
        elif sub.status == "WARNING":
            has_warning = True
            summary_reasons.extend(sub.reasons)
        elif sub.status == "UNKNOWN":
            # Only downgrade overall health if actual components exist whose health could not be determined
            evaluated_components = sub.healthy_components + sub.warning_components + sub.critical_components
            if sub.total_components > 0 and evaluated_components < sub.total_components:
                has_unresolved_component = True
                summary_reasons.append(f"Subsystem '{name}' contains components with UNKNOWN health")

    if has_critical:
        return "CRITICAL", summary_reasons
    if has_warning:
        return "WARNING", summary_reasons
    if has_unresolved_component or total_components_across_server == 0:
        if total_components_across_server == 0:
            summary_reasons.append("No hardware components evaluated")
        return "UNKNOWN", summary_reasons

    return "OK", []
