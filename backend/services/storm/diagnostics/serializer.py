"""Serialize diagnostics / incident payloads for HTTP responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId

from utils.serializers import format_datetime


def _jsonable(value: Any) -> Any:
    """Recursively convert BSON/datetime values into JSON-safe primitives."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return format_datetime(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def serialize_incident(doc: dict) -> dict[str, Any]:
    # Lazy imports avoid circular import with utils.serializers.serialize_incident.
    from utils.serializers import (  # noqa: PLC0415
        serialize_confirmation_result,
        serialize_eligibility_result,
        serialize_risk_result,
        serialize_safety_result,
    )

    trigger = doc.get("trigger") or {}
    eligibility = doc.get("eligibility")
    risk = doc.get("risk")
    confirmation = doc.get("confirmation")
    safety = doc.get("safety")

    return {
        "_id": str(doc["_id"]) if doc.get("_id") is not None else None,
        "incidentId": doc.get("incidentId"),
        "deviceId": str(doc["deviceId"]) if doc.get("deviceId") is not None else None,
        "interface": doc.get("interface"),
        "hostname": doc.get("hostname"),
        "ipAddress": doc.get("ipAddress"),
        "incidentType": doc.get("incidentType") or doc.get("type") or "STORM",
        "type": doc.get("type") or doc.get("incidentType") or "STORM",
        "requestedBy": doc.get("requestedBy"),
        "requestedAt": format_datetime(doc.get("requestedAt")),
        "reason": doc.get("reason"),
        "action": doc.get("action"),
        "requiresApproval": doc.get("requiresApproval"),
        "approvedBy": doc.get("approvedBy"),
        "executedImmediately": doc.get("executedImmediately"),
        "status": doc.get("status") or "OPEN",
        "severity": doc.get("severity") or "CRITICAL",
        "recoveryRetryCount": int(doc.get("recoveryRetryCount") or 0),
        "stabilizationEnd": format_datetime(doc.get("stabilizationEnd")),
        "recoveredAt": format_datetime(doc.get("recoveredAt")),
        "trigger": _jsonable(trigger),
        "interfaceSnapshot": _jsonable(doc.get("interfaceSnapshot") or {}),
        "switchportSnapshot": _jsonable(doc.get("switchportSnapshot") or {}),
        "macTable": _jsonable(doc.get("macTable") or {}),
        "statistics": _jsonable(doc.get("statistics") or {}),
        "neighbor": _jsonable(doc.get("neighbor")),
        "deviceHealth": _jsonable(doc.get("deviceHealth") or {}),
        "eligibility": (
            serialize_eligibility_result(eligibility)
            if isinstance(eligibility, dict)
            else None
        ),
        "risk": serialize_risk_result(risk) if isinstance(risk, dict) else None,
        "confirmation": (
            serialize_confirmation_result(confirmation)
            if isinstance(confirmation, dict)
            else None
        ),
        "safety": serialize_safety_result(safety) if isinstance(safety, dict) else None,
        "diagnosticsMeta": _jsonable(doc.get("diagnosticsMeta") or {}),
        "sourceAttribution": _jsonable(doc.get("sourceAttribution")),
        "sourceClassification": doc.get("sourceClassification")
        or (risk or {}).get("sourceClassification"),
        "sourceConfidence": doc.get("sourceConfidence")
        if doc.get("sourceConfidence") is not None
        else (risk or {}).get("sourceConfidence"),
        "affectedInterfaces": list(doc.get("affectedInterfaces") or []),
        "relatedInterfaces": list(doc.get("relatedInterfaces") or []),
        "timeline": [
            {
                "event": item.get("event"),
                "time": format_datetime(item.get("time")),
                "detail": item.get("detail"),
            }
            for item in (doc.get("timeline") or [])
            if isinstance(item, dict)
        ],
        "createdAt": format_datetime(doc.get("createdAt")),
        "updatedAt": format_datetime(doc.get("updatedAt")),
    }


def serialize_prepare_result(result: dict) -> dict[str, Any]:
    return {
        "ready": bool(result.get("ready")),
        "status": result.get("status"),
        "incidentId": result.get("incidentId"),
        "deviceId": result.get("deviceId"),
        "interface": result.get("interface"),
        "reason": result.get("reason"),
        "context": _jsonable(result.get("context") or {}),
    }
