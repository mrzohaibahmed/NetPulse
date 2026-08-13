"""
Mitigation execution authorization (defense in depth).

Does NOT change Storm decision engines. Governs who may invoke
``execute_mitigation`` and under what incident state.

Modes
-----
STANDARD — admin/API path; requires READY_FOR_MITIGATION and live gates.
EMERGENCY — explicit operator break-glass; bypasses confirmation/safety
            but still uses mitigation locks and audit (set at route layer).
SYSTEM — automatic scheduler path; unchanged confirmation/safety checks
         remain in the engine.
"""

from __future__ import annotations

from typing import Any

from bson import ObjectId

EXECUTION_MODE_STANDARD = "STANDARD"
EXECUTION_MODE_EMERGENCY = "EMERGENCY"

# SYSTEM automatic path — preserved from pre-hardening behavior.
_SYSTEM_SHUTDOWN_STATUSES = frozenset(
    {"READY_FOR_MITIGATION", "PREPARED", "OPEN", "MITIGATION_FAILED"}
)
_SYSTEM_NO_SHUTDOWN_STATUSES = frozenset(
    {"MITIGATED", "MITIGATION_FAILED", "READY_FOR_MITIGATION", "PREPARED", "OPEN"}
)

# Human/admin STANDARD path — strict READY gate (+ retry after failure).
_STANDARD_SHUTDOWN_STATUSES = frozenset({"READY_FOR_MITIGATION", "MITIGATION_FAILED"})
_STANDARD_NO_SHUTDOWN_STATUSES = frozenset(
    {"MITIGATED", "MITIGATION_FAILED", "READY_FOR_MITIGATION"}
)

# Emergency break-glass — manual incidents and post-failure retry only.
_EMERGENCY_SHUTDOWN_STATUSES = frozenset(
    {"OPEN", "PREPARED", "READY_FOR_MITIGATION", "MITIGATION_FAILED"}
)


def _oid(val: Any) -> ObjectId:
    if isinstance(val, ObjectId):
        return val
    return ObjectId(str(val))


def _incident_type(incident: dict) -> str:
    return str(incident.get("incidentType") or incident.get("type") or "STORM").upper()


def _is_confirmed(doc: dict | None) -> bool:
    if not doc:
        return False
    return bool(doc.get("confirmed")) or str(doc.get("state") or "").upper() == "CONFIRMED"


def _require_storm_live_gates(db, device_id, interface: str, *, label: str) -> None:
    """Shared CONFIRMED + SAFE checks for STORM incidents (non-emergency)."""
    latest_confirm = db.storm_confirmation_history.find_one(
        {"deviceId": _oid(device_id), "interface": interface},
        sort=[("timestamp", -1)],
    )
    if not _is_confirmed(latest_confirm):
        raise ValueError(
            f"Mitigation rejected ({label}): storm is not currently confirmed. "
            "Fresh confirmation is required."
        )

    latest_safety = db.storm_safety_history.find_one(
        {"deviceId": _oid(device_id), "interface": interface},
        sort=[("timestamp", -1)],
    )
    if not latest_safety or not latest_safety.get("safe"):
        raise ValueError(
            f"Mitigation rejected ({label}): latest safety result is not SAFE."
        )


def normalize_execution_mode(mode: str | None) -> str:
    raw = (mode or EXECUTION_MODE_STANDARD).strip().upper()
    if raw not in (EXECUTION_MODE_STANDARD, EXECUTION_MODE_EMERGENCY):
        raise ValueError(f"Unsupported execution mode: {raw}")
    return raw


def allowed_statuses_for(
    strategy_name: str,
    *,
    operator: str,
    execution_mode: str | None,
) -> frozenset[str]:
    mode = normalize_execution_mode(execution_mode)
    is_system = str(operator).upper() == "SYSTEM"
    strategy = str(strategy_name).strip().upper()

    if mode == EXECUTION_MODE_EMERGENCY:
        if strategy == "SHUTDOWN":
            return _EMERGENCY_SHUTDOWN_STATUSES
        return _SYSTEM_NO_SHUTDOWN_STATUSES

    if is_system:
        if strategy == "SHUTDOWN":
            return _SYSTEM_SHUTDOWN_STATUSES
        return _SYSTEM_NO_SHUTDOWN_STATUSES

    if strategy == "SHUTDOWN":
        return _STANDARD_SHUTDOWN_STATUSES
    return _STANDARD_NO_SHUTDOWN_STATUSES


def validate_mitigation_authorization(
    *,
    strategy_name: str,
    operator: str,
    incident: dict,
    execution_mode: str | None,
    db,
) -> None:
    """
    Raise ValueError when the caller is not authorized for this execution.

    SYSTEM path: status list preserved; confirmation/safety enforced separately
    in the engine (unchanged ordering).

    STANDARD path: READY_FOR_MITIGATION (+ live STORM gates).

    EMERGENCY path: status list widened; confirmation/safety skipped here
    (route must require confirm + reason + elevated role).
    """
    mode = normalize_execution_mode(execution_mode)
    strategy = str(strategy_name).strip().upper()
    current_status = str(incident.get("status") or "OPEN")
    allowed = allowed_statuses_for(
        strategy, operator=operator, execution_mode=mode
    )

    if current_status not in allowed:
        raise ValueError(
            f"Mitigation rejected: incident status '{current_status}' is not "
            f"allowed for strategy '{strategy}' in mode '{mode}'."
        )

    if mode == EXECUTION_MODE_EMERGENCY:
        return

    is_system = str(operator).upper() == "SYSTEM"
    if is_system:
        # Confirmation + safety checks remain in engine.execute_mitigation.
        return

    device_id = incident.get("deviceId")
    interface = incident.get("interface")
    if not device_id or not interface:
        raise ValueError("Incident is missing deviceId or interface")

    if strategy == "SHUTDOWN" and _incident_type(incident) == "STORM":
        _require_storm_live_gates(
            db,
            device_id,
            interface,
            label="standard",
        )
