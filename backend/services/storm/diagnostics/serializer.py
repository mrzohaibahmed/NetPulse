"""Serialize diagnostics / incident payloads for HTTP responses."""

from __future__ import annotations

from typing import Any

from utils.serializers import format_datetime


def serialize_incident(doc: dict) -> dict[str, Any]:
    return {
        "_id": str(doc["_id"]) if doc.get("_id") is not None else None,
        "incidentId": doc.get("incidentId"),
        "deviceId": str(doc["deviceId"]) if doc.get("deviceId") is not None else None,
        "interface": doc.get("interface"),
        "hostname": doc.get("hostname"),
        "ipAddress": doc.get("ipAddress"),
        "status": doc.get("status") or "OPEN",
        "severity": doc.get("severity") or "CRITICAL",
        "trigger": doc.get("trigger") or {},
        "interfaceSnapshot": doc.get("interfaceSnapshot") or {},
        "switchportSnapshot": doc.get("switchportSnapshot") or {},
        "macTable": doc.get("macTable") or {},
        "statistics": doc.get("statistics") or {},
        "neighbor": doc.get("neighbor"),
        "deviceHealth": doc.get("deviceHealth") or {},
        "eligibility": doc.get("eligibility"),
        "risk": doc.get("risk"),
        "confirmation": doc.get("confirmation"),
        "safety": doc.get("safety"),
        "diagnosticsMeta": doc.get("diagnosticsMeta") or {},
        "timeline": [
            {
                "event": item.get("event"),
                "time": format_datetime(item.get("time")),
                "detail": item.get("detail"),
            }
            for item in (doc.get("timeline") or [])
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
        "context": result.get("context") or {},
    }
