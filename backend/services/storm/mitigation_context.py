"""
Mitigation context builder.

Assembles the immutable context the future Mitigation Engine will consume.
Contains no SSH configuration logic.
"""

from __future__ import annotations

from typing import Any, Optional


def build_mitigation_context(
    *,
    device_id,
    interface: str,
    incident: dict,
    diagnostics: Optional[dict] = None,
    safety: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Build a mitigation context document.

    The context is intentionally read-only evidence + identifiers —
    it does not include any executable config commands.
    """
    safety = safety or incident.get("safety") or {}
    diagnostics = diagnostics or {}

    return {
        "deviceId": str(device_id),
        "interface": interface,
        "incidentId": incident.get("incidentId"),
        "severity": incident.get("severity"),
        "hostname": incident.get("hostname"),
        "ipAddress": incident.get("ipAddress"),
        "safety": {
            "safe": bool(safety.get("safe")),
            "reason": safety.get("reason"),
            "failedRule": safety.get("failedRule"),
            "checks": safety.get("checks") or {},
            "confidence": safety.get("confidence"),
        },
        "trigger": incident.get("trigger") or {},
        "evidence": {
            "interfaceSnapshot": incident.get("interfaceSnapshot") or {},
            "switchportSnapshot": incident.get("switchportSnapshot") or {},
            "macTable": incident.get("macTable") or {},
            "statistics": incident.get("statistics") or {},
            "neighbor": incident.get("neighbor"),
            "deviceHealth": incident.get("deviceHealth") or {},
        },
        "diagnosticsMeta": incident.get("diagnosticsMeta")
        or diagnostics.get("diagnosticsMeta")
        or {},
        "mitigationAllowed": bool(safety.get("safe")),
        "actionsPending": [],  # Future Mitigation Engine fills this
        "notes": (
            "READY_FOR_MITIGATION — no configuration commands have been executed."
        ),
    }
