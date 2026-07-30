"""
Strongly typed models for the Port Eligibility Engine.

The public ``evaluate()`` API returns ``EligibilityResult``. Future Storm
modules consume this object without depending on MongoDB documents.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class EligibilityChecks:
    """Per-rule check outcomes. ``None`` means the rule was not evaluated."""

    monitoring: Optional[bool] = None
    admin: Optional[bool] = None
    oper: Optional[bool] = None
    access: Optional[bool] = None
    trunk: Optional[bool] = None
    uplink: Optional[bool] = None
    infrastructure: Optional[bool] = None
    management: Optional[bool] = None
    protected: Optional[bool] = None

    def to_dict(self) -> dict[str, Optional[bool]]:
        return asdict(self)


@dataclass
class EligibilityResult:
    """
    Deterministic eligibility decision.

    Attribute names match the engine contract (snake_case). Use
    ``to_dict()`` / ``to_api_dict()`` for persistence and HTTP responses.
    """

    eligible: bool
    reason: str
    confidence: int = 100
    failed_rule: Optional[str] = None
    checks: EligibilityChecks = field(default_factory=EligibilityChecks)
    device_id: Optional[str] = None
    interface: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Engine / future-module contract (snake_case)."""
        return {
            "eligible": self.eligible,
            "reason": self.reason,
            "confidence": self.confidence,
            "failed_rule": self.failed_rule,
            "checks": self.checks.to_dict(),
            "device_id": self.device_id,
            "interface": self.interface,
        }

    def to_api_dict(self) -> dict[str, Any]:
        """HTTP / Mongo-friendly camelCase payload (without ids)."""
        payload: dict[str, Any] = {
            "eligible": self.eligible,
            "reason": self.reason,
            "confidence": self.confidence,
            "failedRule": self.failed_rule,
            "checks": self.checks.to_dict(),
        }
        if self.device_id is not None:
            payload["deviceId"] = self.device_id
        if self.interface is not None:
            payload["interface"] = self.interface
        return payload


@dataclass
class NormalizedInterface:
    """
    Normalised view of an interface document for rule evaluation.

    Accepts both camelCase Mongo documents and snake_case payloads.
    """

    device_id: str
    interface: str
    admin_status: str
    oper_status: str
    is_access: bool
    is_trunk: bool
    is_uplink: bool
    is_infrastructure: bool
    is_management: bool
    is_protected: bool
    monitoring_enabled: bool
    port_mode: str
    neighbor: Any = None
    hostname: Optional[str] = None
    ip_address: Optional[str] = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "NormalizedInterface":
        if not isinstance(raw, dict) or not raw:
            raise ValueError("Interface payload must be a non-empty dict")

        name = (
            raw.get("interface")
            or raw.get("name")
            or raw.get("interfaceName")
            or ""
        )
        name = str(name).strip()
        if not name:
            raise ValueError("Interface name is required")

        device_id = raw.get("device_id") or raw.get("deviceId") or ""
        device_id = str(device_id).strip()

        port_mode = str(
            raw.get("port_mode")
            or raw.get("portMode")
            or raw.get("mode")
            or "unknown"
        ).strip().lower()

        is_access = _coerce_bool(
            raw.get("is_access", raw.get("isAccess")),
            default=(port_mode == "access"),
        )
        is_trunk = _coerce_bool(
            raw.get("is_trunk", raw.get("isTrunk")),
            default=(port_mode == "trunk"),
        )

        return cls(
            device_id=device_id,
            interface=name,
            admin_status=_status(raw.get("admin_status", raw.get("adminStatus"))),
            oper_status=_status(raw.get("oper_status", raw.get("operStatus"))),
            is_access=is_access,
            is_trunk=is_trunk,
            is_uplink=_coerce_bool(raw.get("is_uplink", raw.get("isUplink")), False),
            is_infrastructure=_coerce_bool(
                raw.get("is_infrastructure", raw.get("isInfrastructure")), False
            ),
            is_management=_coerce_bool(
                raw.get("is_management", raw.get("isManagement")), False
            ),
            is_protected=_coerce_bool(
                raw.get("is_protected", raw.get("isProtected")), False
            ),
            monitoring_enabled=_coerce_bool(
                raw.get("monitoring_enabled", raw.get("monitoringEnabled")),
                True,
            ),
            port_mode=port_mode,
            neighbor=raw.get("neighbor") or {},
            hostname=raw.get("hostname"),
            ip_address=raw.get("ip_address") or raw.get("ipAddress"),
        )


def create_eligibility_document(
    *,
    device_id,
    interface: str,
    result: EligibilityResult,
    timestamp: Optional[datetime] = None,
    hostname: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> dict[str, Any]:
    """Factory for append-only ``eligibility_results`` documents."""
    from datetime import timezone

    now = timestamp or datetime.now(timezone.utc)
    return {
        "deviceId": device_id,
        "interface": interface,
        "hostname": hostname,
        "ipAddress": ip_address,
        "eligible": bool(result.eligible),
        "reason": result.reason,
        "failedRule": result.failed_rule,
        "confidence": int(result.confidence),
        "checks": result.checks.to_dict(),
        "timestamp": now,
    }


def _status(value: Any) -> str:
    if value is None:
        return "unknown"
    return str(value).strip().lower()


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off", ""):
            return False
    return bool(value)


# ---------------------------------------------------------------------------
# Risk Score Engine models
# ---------------------------------------------------------------------------


@dataclass
class AnalyzerResult:
    """Output of a single independent metric analyzer."""

    metric: str
    value: Optional[float]
    score: float
    supported: bool = True
    weight: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "metric": self.metric,
            "value": self.value,
            "score": round(float(self.score), 2),
            "supported": self.supported,
            "weight": self.weight,
        }
        if self.detail:
            payload["detail"] = dict(self.detail)
        return payload

    def to_contributor(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": self.value,
            "score": round(float(self.score), 2),
            "weight": self.weight,
        }


@dataclass
class RiskScoreResult:
    """
    Aggregated Layer-2 storm risk estimate for one interface.

    Attribute names support both engine (camelCase via to_api_dict) and
    future Storm modules.
    """

    risk_score: float
    severity: str
    confidence: float
    contributors: list[dict[str, Any]] = field(default_factory=list)
    eligible: bool = True
    timestamp: Optional[datetime] = None
    device_id: Optional[str] = None
    interface: Optional[str] = None
    raw_metrics: dict[str, Any] = field(default_factory=dict)
    skipped_reason: Optional[str] = None
    source_classification: Optional[str] = None
    source_confidence: float = 0.0
    source_rationale: Optional[str] = None

    def to_api_dict(self) -> dict[str, Any]:
        from datetime import timezone

        from utils.serializers import format_datetime

        ts = self.timestamp or datetime.now(timezone.utc)
        return {
            "riskScore": round(float(self.risk_score), 2),
            "severity": self.severity,
            "confidence": round(float(self.confidence), 2),
            "contributors": list(self.contributors),
            "eligible": bool(self.eligible),
            "timestamp": format_datetime(ts),
            "deviceId": self.device_id,
            "interface": self.interface,
            "rawMetrics": dict(self.raw_metrics),
            "skippedReason": self.skipped_reason,
            "sourceClassification": self.source_classification,
            "sourceConfidence": round(float(self.source_confidence), 2),
            "sourceRationale": self.source_rationale,
        }


def create_risk_document(
    *,
    device_id,
    interface: str,
    result: RiskScoreResult,
    timestamp: Optional[datetime] = None,
    hostname: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> dict[str, Any]:
    """Factory for append-only ``storm_risk_history`` documents."""
    from datetime import timezone

    now = timestamp or result.timestamp or datetime.now(timezone.utc)
    doc = {
        "deviceId": device_id,
        "interface": interface,
        "hostname": hostname,
        "ipAddress": ip_address,
        "riskScore": round(float(result.risk_score), 2),
        "severity": result.severity,
        "contributors": list(result.contributors),
        "rawMetrics": dict(result.raw_metrics),
        "confidence": round(float(result.confidence), 2),
        "eligible": bool(result.eligible),
        "skippedReason": result.skipped_reason,
        "timestamp": now,
    }
    if result.source_classification is not None:
        doc["sourceClassification"] = result.source_classification
        doc["sourceConfidence"] = round(float(result.source_confidence), 2)
        if result.source_rationale:
            doc["sourceRationale"] = result.source_rationale
    return doc


# ---------------------------------------------------------------------------
# Confirmation Engine models
# ---------------------------------------------------------------------------


@dataclass
class ConfirmationResult:
    """
    Persistent-storm confirmation decision for one interface.

    Future Safety Engine consumes this via ``confirmation.evaluate(...)``.
    """

    confirmed: bool
    state: str
    current_risk: float
    highest_risk: float
    average_risk: float
    consecutive_high_samples: int
    required_samples: int
    reason: str
    timestamp: Optional[datetime] = None
    device_id: Optional[str] = None
    interface: Optional[str] = None
    reset: bool = False
    reset_reason: Optional[str] = None

    def to_api_dict(self) -> dict[str, Any]:
        from datetime import timezone

        from utils.serializers import format_datetime

        ts = self.timestamp or datetime.now(timezone.utc)
        return {
            "confirmed": bool(self.confirmed),
            "state": self.state,
            "currentRisk": round(float(self.current_risk), 2),
            "highestRisk": round(float(self.highest_risk), 2),
            "averageRisk": round(float(self.average_risk), 2),
            "consecutiveHighSamples": int(self.consecutive_high_samples),
            "requiredSamples": int(self.required_samples),
            "reason": self.reason,
            "timestamp": format_datetime(ts),
            "deviceId": self.device_id,
            "interface": self.interface,
            "reset": bool(self.reset),
            "resetReason": self.reset_reason,
        }


def create_confirmation_document(
    *,
    device_id,
    interface: str,
    result: ConfirmationResult,
    timestamp: Optional[datetime] = None,
    hostname: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> dict[str, Any]:
    """Factory for append-only ``storm_confirmation_history`` documents."""
    from datetime import timezone

    now = timestamp or result.timestamp or datetime.now(timezone.utc)
    return {
        "deviceId": device_id,
        "interface": interface,
        "hostname": hostname,
        "ipAddress": ip_address,
        "confirmed": bool(result.confirmed),
        "state": result.state,
        "currentRisk": round(float(result.current_risk), 2),
        "highestRisk": round(float(result.highest_risk), 2),
        "averageRisk": round(float(result.average_risk), 2),
        "consecutiveHighSamples": int(result.consecutive_high_samples),
        "requiredSamples": int(result.required_samples),
        "reason": result.reason,
        "reset": bool(result.reset),
        "resetReason": result.reset_reason,
        "timestamp": now,
    }


# ---------------------------------------------------------------------------
# Safety Engine models
# ---------------------------------------------------------------------------


@dataclass
class SafetyResult:
    """
    Pre-mitigation safety decision.

    The Mitigation Engine and recovery policy consume this via ``safety.evaluate(...)``.
    """

    safe: bool
    reason: str
    confidence: float = 99.0
    failed_rule: Optional[str] = None
    checks: dict[str, Any] = field(default_factory=dict)
    timestamp: Optional[datetime] = None
    device_id: Optional[str] = None
    interface: Optional[str] = None
    cooldown_remaining_seconds: int = 0
    mitigation_attempts: int = 0
    cpu_percent: Optional[float] = None
    memory_percent: Optional[float] = None
    status: str = "UNSAFE"  # SAFE | UNSAFE | WAITING

    def to_api_dict(self) -> dict[str, Any]:
        from datetime import timezone

        from utils.serializers import format_datetime

        ts = self.timestamp or datetime.now(timezone.utc)
        return {
            "safe": bool(self.safe),
            "reason": self.reason,
            "confidence": round(float(self.confidence), 2),
            "failedRule": self.failed_rule,
            "checks": dict(self.checks),
            "timestamp": format_datetime(ts),
            "deviceId": self.device_id,
            "interface": self.interface,
            "cooldownRemainingSeconds": int(self.cooldown_remaining_seconds),
            "mitigationAttempts": int(self.mitigation_attempts),
            "cpuPercent": self.cpu_percent,
            "memoryPercent": self.memory_percent,
            "status": self.status,
        }


def create_safety_document(
    *,
    device_id,
    interface: str,
    result: SafetyResult,
    timestamp: Optional[datetime] = None,
    hostname: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> dict[str, Any]:
    """Factory for append-only ``storm_safety_history`` documents."""
    from datetime import timezone

    now = timestamp or result.timestamp or datetime.now(timezone.utc)
    return {
        "deviceId": device_id,
        "interface": interface,
        "hostname": hostname,
        "ipAddress": ip_address,
        "safe": bool(result.safe),
        "reason": result.reason,
        "failedRule": result.failed_rule,
        "confidence": round(float(result.confidence), 2),
        "checks": dict(result.checks),
        "cooldownRemainingSeconds": int(result.cooldown_remaining_seconds),
        "mitigationAttempts": int(result.mitigation_attempts),
        "cpuPercent": result.cpu_percent,
        "memoryPercent": result.memory_percent,
        "status": result.status,
        "timestamp": now,
    }


@dataclass
class PrepareResult:
    """Public result of ``orchestrator.prepare`` (no mitigation executed)."""

    ready: bool
    status: str
    incident_id: Optional[str]
    device_id: str
    interface: str
    reason: str = ""
    context: dict[str, Any] = field(default_factory=dict)

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "status": self.status,
            "incidentId": self.incident_id,
            "deviceId": self.device_id,
            "interface": self.interface,
            "reason": self.reason,
            "context": self.context,
        }
