"""
Storm source confidence for risky interfaces.

Classifies whether elevated storm metrics likely originate on the port,
are being forwarded through it, or are received as flood victims.

Cisco / switch counter convention (In = RX, Out = TX):
- RX (InBroadcast)  = traffic entering the switch from the attached device
                      → most likely originating host on an access port
- TX (OutBroadcast) = traffic leaving the switch toward the attached device
                      → flooded receiver / victim of a broadcast storm
"""

from __future__ import annotations

from typing import Any, Optional

from services.storm.history import rate_per_second

LIKELY_SOURCE = "LIKELY_SOURCE"
POSSIBLE_SOURCE = "POSSIBLE_SOURCE"
LIKELY_FORWARDER = "LIKELY_FORWARDER"
LIKELY_RECEIVER = "LIKELY_RECEIVER"
NORMAL = "NORMAL"
UNKNOWN = "UNKNOWN"


def _rate(
    current: Optional[dict[str, Any]],
    previous: Optional[dict[str, Any]],
    logical: str,
) -> Optional[float]:
    if not current:
        return None
    value, supported = rate_per_second(current, previous, logical)
    if not supported:
        return None
    return value


def _topology_flags(interface_context: Optional[dict[str, Any]]) -> dict[str, Any]:
    ctx = interface_context or {}
    port_mode = str(
        ctx.get("port_mode") or ctx.get("portMode") or ctx.get("mode") or ""
    ).lower()
    is_access = bool(ctx.get("is_access") or ctx.get("isAccess"))
    is_trunk = bool(ctx.get("is_trunk") or ctx.get("isTrunk"))
    is_uplink = bool(ctx.get("is_uplink") or ctx.get("isUplink"))
    is_infrastructure = bool(
        ctx.get("is_infrastructure") or ctx.get("isInfrastructure")
    )
    if not is_access and port_mode == "access":
        is_access = True
    if not is_trunk and port_mode == "trunk":
        is_trunk = True
    neighbor = ctx.get("neighbor") or {}
    return {
        "port_mode": port_mode or "unknown",
        "is_access": is_access,
        "is_trunk": is_trunk,
        "is_uplink": is_uplink,
        "is_infrastructure": is_infrastructure,
        "neighbor_type": str(neighbor.get("deviceType") or "").lower(),
        "has_neighbor": bool(neighbor.get("hostname") or neighbor.get("ip")),
    }


def classify_storm_source(
    *,
    current: Optional[dict[str, Any]],
    previous: Optional[dict[str, Any]],
    interface_context: Optional[dict[str, Any]] = None,
    risk_score: float = 0.0,
    min_risk_for_analysis: float = 25.0,
    dominance_ratio: float = 1.25,
    dominant_share: float = 0.6,
) -> dict[str, Any]:
    """
    Return ``sourceClassification``, ``sourceConfidence`` (0–100), and rationale.

    ``dominance_ratio`` / ``dominant_share`` remain configurable so Cisco
    In/Out assumptions stay inside this classifier only.
    """
    if not current:
        return {
            "sourceClassification": UNKNOWN,
            "sourceConfidence": 0.0,
            "sourceRationale": "Missing statistics.",
        }

    if risk_score < float(min_risk_for_analysis):
        return {
            "sourceClassification": NORMAL,
            "sourceConfidence": 0.0,
            "sourceRationale": "Risk below analysis threshold — treated as normal.",
        }

    topo = _topology_flags(interface_context)
    rx_bcast = _rate(current, previous, "rx_broadcast_packets")
    tx_bcast = _rate(current, previous, "tx_broadcast_packets")
    rx_mcast = _rate(current, previous, "rx_multicast_packets")
    tx_mcast = _rate(current, previous, "tx_multicast_packets")

    # Legacy fallback when directional counters absent.
    if rx_bcast is None and tx_bcast is None:
        combined = _rate(current, previous, "broadcast_packets")
        if combined is not None:
            rx_bcast = combined * 0.5
            tx_bcast = combined * 0.5
    if rx_mcast is None and tx_mcast is None:
        combined = _rate(current, previous, "multicast_packets")
        if combined is not None:
            rx_mcast = combined * 0.5
            tx_mcast = combined * 0.5

    rx_storm = max(rx_bcast or 0.0, rx_mcast or 0.0)
    tx_storm = max(tx_bcast or 0.0, tx_mcast or 0.0)
    total = rx_storm + tx_storm

    if total <= 0:
        return {
            "sourceClassification": NORMAL,
            "sourceConfidence": 0.0,
            "sourceRationale": "No directional storm traffic rates available.",
            "sourceMetrics": {
                "rxBroadcastRate": rx_bcast,
                "txBroadcastRate": tx_bcast,
                "rxMulticastRate": rx_mcast,
                "txMulticastRate": tx_mcast,
                "rxRatio": 0.0,
                "txRatio": 0.0,
            },
        }

    rx_ratio = rx_storm / total
    tx_ratio = tx_storm / total
    trunk_like = topo["is_trunk"] or topo["is_uplink"] or topo["is_infrastructure"]
    both_elevated = (
        rx_storm > 0
        and tx_storm > 0
        and min(rx_ratio, tx_ratio) >= 0.35
    )

    classification = UNKNOWN
    rationale_parts: list[str] = []

    if topo["is_access"]:
        # Access: RX-dominant ingress = originating host; TX-dominant egress = victim.
        if rx_ratio >= dominant_share and rx_storm >= tx_storm * dominance_ratio:
            classification = LIKELY_SOURCE
            rationale_parts.append(
                "Access port with dominant RX broadcast/multicast — likely originating storm."
            )
        elif tx_ratio >= dominant_share and tx_storm >= rx_storm * dominance_ratio:
            classification = LIKELY_RECEIVER
            rationale_parts.append(
                "Access port with dominant TX broadcast/multicast — likely receiving forwarded storm."
            )
        elif both_elevated:
            classification = POSSIBLE_SOURCE
            rationale_parts.append(
                "Access port with elevated RX and TX storm traffic — possible source."
            )
        else:
            classification = UNKNOWN
            rationale_parts.append(
                "Access port with mixed RX/TX storm traffic — direction inconclusive."
            )
    elif trunk_like:
        if both_elevated:
            classification = LIKELY_FORWARDER
            rationale_parts.append(
                "Trunk/uplink with significant RX and TX storm traffic — likely forwarding."
            )
        elif rx_ratio >= 0.65:
            classification = LIKELY_RECEIVER
            rationale_parts.append(
                "Trunk/uplink with dominant RX storm traffic — storm arriving from upstream."
            )
        elif tx_ratio >= 0.65:
            classification = LIKELY_FORWARDER
            rationale_parts.append(
                "Trunk/uplink with dominant TX storm traffic — flooding downstream (forwarder)."
            )
        else:
            classification = LIKELY_FORWARDER
            rationale_parts.append(
                "Trunk/uplink role with bidirectional storm metrics — treated as forwarder."
            )
    else:
        if rx_ratio >= 0.55 and rx_storm >= tx_storm * dominance_ratio:
            classification = LIKELY_SOURCE
            rationale_parts.append("Dominant RX storm traffic on unknown port role.")
        elif tx_ratio >= 0.55 and tx_storm >= rx_storm * dominance_ratio:
            classification = LIKELY_RECEIVER
            rationale_parts.append("Dominant TX storm traffic on unknown port role.")
        elif both_elevated:
            classification = POSSIBLE_SOURCE
            rationale_parts.append("Elevated RX and TX on unknown port role — possible source.")
        else:
            classification = UNKNOWN
            rationale_parts.append("Mixed directional storm traffic.")

    if topo["has_neighbor"] and topo["neighbor_type"]:
        rationale_parts.append(f"Neighbor type: {topo['neighbor_type']}.")

    # Confidence from ratio separation, signal strength, and topology clarity.
    separation = abs(rx_ratio - tx_ratio)
    signal = min(100.0, (total / 100.0) * 10.0)
    base = 35.0 + separation * 55.0
    if classification == LIKELY_FORWARDER and trunk_like:
        base += 10.0
    if classification == POSSIBLE_SOURCE:
        base *= 0.75
    if classification in (UNKNOWN, NORMAL):
        base *= 0.45
    confidence = round(min(100.0, max(0.0, base + signal * 0.15)), 2)

    return {
        "sourceClassification": classification,
        "sourceConfidence": confidence,
        "sourceRationale": " ".join(rationale_parts),
        "sourceMetrics": {
            "rxBroadcastRate": rx_bcast,
            "txBroadcastRate": tx_bcast,
            "rxMulticastRate": rx_mcast,
            "txMulticastRate": tx_mcast,
            "rxRatio": round(rx_ratio, 4),
            "txRatio": round(tx_ratio, 4),
            "broadcastDominance": "RX" if rx_ratio >= tx_ratio else "TX",
        },
    }
