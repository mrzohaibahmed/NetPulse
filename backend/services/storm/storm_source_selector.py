"""
Storm source arbitration.

Selects the single most probable originating interface per
(device, broadcast-domain/VLAN) among elevated-risk candidates.

This is NOT a replacement for the Risk Engine — it sits between
Risk and Confirmation / Prepare.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId

from services.storm.history import rate_per_second
from services.storm.source_arbitration_config import (
    SourceArbitrationConfig,
    get_source_arbitration_config,
)
from services.storm.source_classification import (
    LIKELY_FORWARDER,
    LIKELY_RECEIVER,
    LIKELY_SOURCE,
    NORMAL,
    POSSIBLE_SOURCE,
    UNKNOWN,
)
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.source_selector")


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _oid(device_id):
    if isinstance(device_id, ObjectId):
        return device_id
    if isinstance(device_id, str) and ObjectId.is_valid(device_id):
        return ObjectId(device_id)
    return device_id


@dataclass
class SourceCandidate:
    device_id: Any
    interface: str
    broadcast_domain: str
    risk_score: float = 0.0
    source_classification: str = UNKNOWN
    source_confidence: float = 0.0
    rx_broadcast_rate: float = 0.0
    tx_broadcast_rate: float = 0.0
    rx_multicast_rate: float = 0.0
    unknown_unicast_rate: float = 0.0
    rx_tx_ratio: float = 0.0
    growth_rate: float = 0.0
    selection_score: float = 0.0
    rank: int = 0
    reason_selected: Optional[str] = None
    reason_rejected: Optional[str] = None
    hostname: Optional[str] = None
    eligible: bool = True
    is_trunk: bool = False
    is_infrastructure: bool = False
    is_management: bool = False
    is_protected: bool = False
    is_uplink: bool = False
    mitigated: bool = False
    in_cooldown: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "deviceId": str(self.device_id) if self.device_id is not None else None,
            "interface": self.interface,
            "broadcastDomain": self.broadcast_domain,
            "riskScore": round(float(self.risk_score), 2),
            "sourceClassification": self.source_classification,
            "sourceConfidence": round(float(self.source_confidence), 2),
            "rxBroadcastRate": round(float(self.rx_broadcast_rate), 4),
            "txBroadcastRate": round(float(self.tx_broadcast_rate), 4),
            "rxMulticastRate": round(float(self.rx_multicast_rate), 4),
            "unknownUnicastRate": round(float(self.unknown_unicast_rate), 4),
            "rxTxRatio": round(float(self.rx_tx_ratio), 4),
            "growthRate": round(float(self.growth_rate), 4),
            "selectionScore": round(float(self.selection_score), 2),
            "sourceRank": self.rank,
            "reasonSelected": self.reason_selected,
            "reasonRejected": self.reason_rejected,
            "hostname": self.hostname,
        }


@dataclass
class SourceSelectionResult:
    best: Optional[SourceCandidate]
    runners_up: list[SourceCandidate]
    receivers: list[SourceCandidate]
    candidate_count: int
    receiver_count: int
    broadcast_domain: str
    device_id: Any
    reason: str
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "deviceId": str(self.device_id) if self.device_id is not None else None,
            "broadcastDomain": self.broadcast_domain,
            "candidateCount": self.candidate_count,
            "receiverCount": self.receiver_count,
            "reason": self.reason,
            "bestCandidate": self.best.to_dict() if self.best else None,
            "runnersUp": [c.to_dict() for c in self.runners_up],
            "receivers": [c.to_dict() for c in self.receivers],
            "selectedInterface": self.best.interface if self.best else None,
            "sourceConfidence": self.best.source_confidence if self.best else 0.0,
            "sourceRank": self.best.rank if self.best else None,
            "reasonSelected": self.best.reason_selected if self.best else None,
        }


def _broadcast_domain(iface_doc: Optional[dict]) -> str:
    if not iface_doc:
        return "unknown"
    vlan = (
        iface_doc.get("accessVlan")
        or iface_doc.get("access_vlan")
        or iface_doc.get("vlan")
        or iface_doc.get("nativeVlan")
    )
    if vlan is None or vlan == "":
        return "unknown"
    return f"vlan:{vlan}"


def _rate_from_pair(current, previous, logical: str) -> float:
    value, supported = rate_per_second(current or {}, previous, logical)
    if not supported or value is None:
        return 0.0
    return float(value)


def _metric_from_risk(risk: dict, metric: str, detail_key: str) -> float:
    raw = (risk.get("rawMetrics") or {}).get(metric) or {}
    detail = raw.get("detail") or {}
    if detail.get(detail_key) is not None:
        try:
            return float(detail[detail_key])
        except (TypeError, ValueError):
            pass
    if raw.get("value") is not None and detail_key.startswith("rx"):
        try:
            return float(raw["value"])
        except (TypeError, ValueError):
            pass
    return 0.0


def _load_latest_risk_rows(
    device_id,
    *,
    risk_threshold: float,
    limit: int,
) -> list[dict]:
    oid = _oid(device_id)
    pipeline = [
        {"$match": {"deviceId": oid}},
        {"$sort": {"timestamp": -1}},
        {
            "$group": {
                "_id": "$interface",
                "doc": {"$first": "$$ROOT"},
            }
        },
        {"$replaceRoot": {"newRoot": "$doc"}},
        {"$match": {"riskScore": {"$gte": float(risk_threshold)}}},
        {"$sort": {"riskScore": -1}},
        {"$limit": max(int(limit), 1)},
    ]
    try:
        return list(_db().storm_risk_history.aggregate(pipeline))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SOURCE] Failed loading risk candidates: %s", exc)
        return []


def _interface_doc(device_id, interface: str) -> Optional[dict]:
    try:
        return _db().interfaces.find_one(
            {"deviceId": _oid(device_id), "name": interface}
        )
    except Exception:  # noqa: BLE001
        return None


def _stats_pair(device_id, interface: str) -> tuple[Optional[dict], Optional[dict]]:
    try:
        from services.storm.history import load_stats_pair  # noqa: PLC0415

        return load_stats_pair(device_id, interface)
    except Exception:  # noqa: BLE001
        return None, None


def _is_mitigated(device_id, interface: str) -> bool:
    try:
        row = _db().storm_incidents.find_one(
            {
                "deviceId": _oid(device_id),
                "interface": interface,
                "status": {"$in": ["MITIGATED", "MONITORING", "READY_FOR_MITIGATION", "PREPARED"]},
            },
            sort=[("updatedAt", -1)],
        )
        return bool(row)
    except Exception:  # noqa: BLE001
        return False


def _in_cooldown(device_id, interface: str, cooldown_minutes: int | None = None) -> bool:
    """True when a recent successful SHUTDOWN is still within recovery cooldown."""
    if cooldown_minutes is None:
        try:
            from services.settings_service import get_settings  # noqa: PLC0415

            cooldown_minutes = int(get_settings().get("cooldownMinutes", 5))
        except Exception:  # noqa: BLE001
            cooldown_minutes = 5
    if cooldown_minutes <= 0:
        return False
    try:
        from datetime import timedelta  # noqa: PLC0415

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=int(cooldown_minutes))
        row = _db().storm_mitigation_history.find_one(
            {
                "deviceId": _oid(device_id),
                "interface": interface,
                "strategy": "SHUTDOWN",
                "status": "SUCCESS",
                "timestamp": {"$gte": cutoff},
            },
            sort=[("timestamp", -1)],
        )
        return bool(row)
    except Exception:  # noqa: BLE001
        return False


def _build_candidate(
    risk: dict,
    *,
    cfg: SourceArbitrationConfig,
    broadcast_domain_filter: Optional[str] = None,
) -> Optional[SourceCandidate]:
    device_id = risk.get("deviceId")
    interface = risk.get("interface")
    if device_id is None or not interface:
        return None

    iface = _interface_doc(device_id, interface) or {}
    domain = _broadcast_domain(iface)
    if broadcast_domain_filter and domain != broadcast_domain_filter:
        return None

    current, previous = _stats_pair(device_id, interface)
    rx_b = _metric_from_risk(risk, "broadcast", "rxRate")
    tx_b = _metric_from_risk(risk, "broadcast", "txRate")
    if current:
        if rx_b <= 0:
            rx_b = _rate_from_pair(current, previous, "rx_broadcast_packets")
        if tx_b <= 0:
            tx_b = _rate_from_pair(current, previous, "tx_broadcast_packets")
    rx_m = _metric_from_risk(risk, "multicast", "rxRate")
    if current and rx_m <= 0:
        rx_m = _rate_from_pair(current, previous, "rx_multicast_packets")
    uu = _metric_from_risk(risk, "unknown_unicast", "rxRate")
    if uu <= 0 and current:
        uu = _rate_from_pair(current, previous, "unknown_unicast_packets")
        if uu <= 0:
            try:
                raw = (risk.get("rawMetrics") or {}).get("unknown_unicast") or {}
                uu = float(raw.get("value") or 0)
            except (TypeError, ValueError):
                uu = 0.0

    # Growth: compare current RX broadcast to previous sample rate if possible.
    growth = 0.0
    if current and previous:
        # Approximate using absolute RX counter delta intensity
        growth = max(0.0, rx_b)  # already a rate; higher is more aggressive

    total = rx_b + tx_b
    rx_tx_ratio = (rx_b / tx_b) if tx_b > 0 else (rx_b if rx_b > 0 else 0.0)

    classification = str(risk.get("sourceClassification") or UNKNOWN)
    confidence = float(risk.get("sourceConfidence") or 0.0)
    risk_score = float(risk.get("riskScore") or 0.0)

    cand = SourceCandidate(
        device_id=device_id,
        interface=str(interface),
        broadcast_domain=domain,
        risk_score=risk_score,
        source_classification=classification,
        source_confidence=confidence,
        rx_broadcast_rate=rx_b,
        tx_broadcast_rate=tx_b,
        rx_multicast_rate=rx_m,
        unknown_unicast_rate=uu,
        rx_tx_ratio=rx_tx_ratio,
        growth_rate=growth,
        hostname=risk.get("hostname") or iface.get("hostname"),
        eligible=bool(risk.get("eligible", True)),
        is_trunk=bool(iface.get("isTrunk")),
        is_infrastructure=bool(iface.get("isInfrastructure")),
        is_management=bool(iface.get("isManagement")),
        is_protected=bool(iface.get("isProtected")),
        is_uplink=bool(iface.get("isUplink")),
        mitigated=_is_mitigated(device_id, interface),
        in_cooldown=_in_cooldown(device_id, interface),
        raw={"risk": risk, "totalDirectional": total},
    )
    cand.selection_score = _score_candidate(cand, cfg)
    return cand


def _score_candidate(cand: SourceCandidate, cfg: SourceArbitrationConfig) -> float:
    """Higher is better. Penalties reduce score for receivers / protected ports."""
    score = 0.0
    score += cand.rx_broadcast_rate * cfg.rx_weight
    score += cand.rx_multicast_rate * (cfg.rx_weight * 0.5)
    score += cand.unknown_unicast_rate * cfg.unknown_unicast_weight
    score += cand.risk_score * cfg.risk_weight
    score += min(cand.rx_tx_ratio, 100.0) * 0.5
    score += cand.growth_rate * cfg.growth_weight
    score += cand.source_confidence * 0.2

    # Penalize TX-dominant flood victims
    score -= cand.tx_broadcast_rate * cfg.tx_penalty

    if cand.source_classification == LIKELY_RECEIVER:
        score -= cfg.receiver_penalty
    elif cand.source_classification == LIKELY_FORWARDER:
        score -= cfg.forwarder_penalty
    elif cand.source_classification == LIKELY_SOURCE:
        score += 25.0
    elif cand.source_classification == POSSIBLE_SOURCE:
        score += 10.0

    if cand.is_trunk or cand.is_uplink:
        score -= 50.0
    if cand.is_infrastructure:
        score -= 40.0
    if cand.is_management:
        score -= 40.0
    if cand.is_protected:
        score -= 30.0
    if cand.mitigated:
        score -= 20.0
    if cand.in_cooldown:
        score -= 25.0
    if not cand.eligible:
        score -= 100.0

    return round(score, 4)


def select_storm_source(
    device_id,
    *,
    broadcast_domain: Optional[str] = None,
    interface: Optional[str] = None,
    risk_threshold: Optional[float] = None,
    config: Optional[SourceArbitrationConfig] = None,
    risk_rows: Optional[list[dict]] = None,
) -> SourceSelectionResult:
    """
    Rank elevated-risk interfaces on a device (optionally one VLAN) and
    return the single best originating candidate.
    """
    cfg = config or get_source_arbitration_config()
    oid = _oid(device_id)

    if not cfg.enable_source_arbitration:
        return SourceSelectionResult(
            best=None,
            runners_up=[],
            receivers=[],
            candidate_count=0,
            receiver_count=0,
            broadcast_domain=broadcast_domain or "all",
            device_id=oid,
            reason="Source arbitration disabled",
            enabled=False,
        )

    if risk_threshold is None:
        from services.storm.confirmation_rules import get_confirmation_config  # noqa: PLC0415

        risk_threshold = float(get_confirmation_config().risk_threshold)

    domain_filter = broadcast_domain
    if domain_filter is None and interface:
        iface = _interface_doc(device_id, interface) or {}
        domain_filter = _broadcast_domain(iface)

    rows = risk_rows
    if rows is None:
        rows = _load_latest_risk_rows(
            device_id,
            risk_threshold=float(risk_threshold),
            limit=cfg.maximum_candidates * 3,
        )

    candidates: list[SourceCandidate] = []
    receivers: list[SourceCandidate] = []
    for risk in rows:
        cand = _build_candidate(
            risk,
            cfg=cfg,
            broadcast_domain_filter=domain_filter,
        )
        if not cand:
            continue
        if cand.source_classification == LIKELY_RECEIVER:
            cand.reason_rejected = "Classified as LIKELY_RECEIVER"
            receivers.append(cand)
            if cfg.enable_receiver_filtering:
                continue
        if cand.source_classification == LIKELY_FORWARDER and cfg.filter_forwarders:
            cand.reason_rejected = "Classified as LIKELY_FORWARDER"
            receivers.append(cand)
            continue
        if cand.source_classification in (NORMAL,):
            cand.reason_rejected = "Normal / below storm signal"
            continue
        if cand.is_trunk or cand.is_uplink or cand.is_infrastructure or cand.is_management:
            cand.reason_rejected = "Protected topology role"
            continue
        if not cand.eligible:
            cand.reason_rejected = "Not eligible"
            continue
        candidates.append(cand)

    candidates.sort(
        key=lambda c: (
            c.selection_score,
            c.rx_broadcast_rate,
            c.risk_score,
            c.source_confidence,
        ),
        reverse=True,
    )
    for idx, cand in enumerate(candidates, start=1):
        cand.rank = idx

    domain_label = domain_filter or "all"
    if not candidates:
        return SourceSelectionResult(
            best=None,
            runners_up=[],
            receivers=receivers,
            candidate_count=0,
            receiver_count=len(receivers),
            broadcast_domain=domain_label,
            device_id=oid,
            reason="No eligible storm-source candidates",
            enabled=True,
        )

    best = candidates[0]
    if best.source_confidence < cfg.minimum_source_confidence and best.source_classification != LIKELY_SOURCE:
        # Still allow LIKELY_SOURCE with lower classifier confidence if RX dominates strongly
        if best.rx_broadcast_rate <= best.tx_broadcast_rate:
            for cand in candidates:
                cand.reason_rejected = (
                    f"Source confidence {best.source_confidence:.1f} "
                    f"below minimum {cfg.minimum_source_confidence:.1f}"
                )
            return SourceSelectionResult(
                best=None,
                runners_up=candidates[:5],
                receivers=receivers,
                candidate_count=len(candidates),
                receiver_count=len(receivers),
                broadcast_domain=domain_label,
                device_id=oid,
                reason="No candidate met minimum source confidence",
                enabled=True,
            )

    best.reason_selected = (
        f"Highest selection score ({best.selection_score:.1f}) — "
        f"{best.source_classification}, RX={best.rx_broadcast_rate:.1f} "
        f"TX={best.tx_broadcast_rate:.1f}, risk={best.risk_score:.1f}"
    )

    runners: list[SourceCandidate] = []
    for cand in candidates[1:]:
        gap = best.selection_score - cand.selection_score
        if cfg.allow_multiple_sources and gap <= cfg.tie_threshold:
            cand.reason_selected = (
                f"Tie/near-tie with selected source (gap={gap:.1f} <= {cfg.tie_threshold})"
            )
            runners.append(cand)
        else:
            cand.reason_rejected = (
                f"Outranked by {best.interface} "
                f"(score {cand.selection_score:.1f} vs {best.selection_score:.1f})"
            )
            runners.append(cand)

    selected_runners = (
        [c for c in runners if c.reason_selected]
        if cfg.allow_multiple_sources
        else []
    )

    logger.info(
        "Source selected | device=%s domain=%s iface=%s score=%.1f candidates=%s receivers=%s",
        oid,
        domain_label,
        best.interface,
        best.selection_score,
        len(candidates),
        len(receivers),
    )

    return SourceSelectionResult(
        best=best,
        runners_up=runners[:10],
        receivers=receivers[:20],
        candidate_count=len(candidates),
        receiver_count=len(receivers),
        broadcast_domain=domain_label,
        device_id=oid,
        reason=best.reason_selected or "Selected",
        enabled=True,
    )


def is_selected_storm_source(
    device_id,
    interface: str,
    *,
    config: Optional[SourceArbitrationConfig] = None,
    risk_threshold: Optional[float] = None,
) -> tuple[bool, SourceSelectionResult]:
    """
    Return whether ``interface`` is the arbitration winner for its domain.
    """
    cfg = config or get_source_arbitration_config()
    selection = select_storm_source(
        device_id,
        interface=interface,
        risk_threshold=risk_threshold,
        config=cfg,
    )
    if not cfg.enable_source_arbitration:
        return True, selection
    if selection.best and selection.best.interface == interface:
        return True, selection
    if cfg.allow_multiple_sources:
        for runner in selection.runners_up:
            if runner.interface == interface and runner.reason_selected:
                return True, selection
    return False, selection


def confirmation_allowed_for_source(
    device_id,
    interface: str,
    *,
    risk_doc: Optional[dict] = None,
    config: Optional[SourceArbitrationConfig] = None,
) -> tuple[bool, str, Optional[dict]]:
    """
    Gate used by Confirmation Engine.

    Returns (allowed, reason, selection_dict_or_none).
    """
    cfg = config or get_source_arbitration_config()
    classification = None
    if risk_doc:
        classification = risk_doc.get("sourceClassification")
    else:
        try:
            from services.storm.confirmation_history import load_latest_risk  # noqa: PLC0415

            risk_doc = load_latest_risk(device_id, interface)
            classification = (risk_doc or {}).get("sourceClassification")
        except Exception:  # noqa: BLE001
            risk_doc = None

    if cfg.enable_receiver_filtering and not cfg.allow_confirm_receivers:
        if classification == LIKELY_RECEIVER:
            return (
                False,
                "Likely receiver — confirmation blocked by source attribution",
                None,
            )
        if cfg.filter_forwarders and classification == LIKELY_FORWARDER:
            return (
                False,
                "Likely forwarder — confirmation blocked by source attribution",
                None,
            )

    if not cfg.enable_source_arbitration:
        return True, "Source arbitration disabled", None

    allowed, selection = is_selected_storm_source(device_id, interface, config=cfg)
    payload = selection.to_dict()
    if allowed:
        return True, selection.reason or "Selected as storm source", payload

    selected = selection.best.interface if selection.best else None
    reason = (
        f"Not selected as storm source"
        + (f" (selected {selected})" if selected else "")
    )
    return False, reason, payload
