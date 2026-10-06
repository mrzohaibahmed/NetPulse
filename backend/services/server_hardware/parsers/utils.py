"""
Parser utilities, type coercion, and exception classes for Redfish normalization (Phase 3B).
"""

from __future__ import annotations

from typing import Any

from utils.monitor_logger import get_monitor_logger
from services.server_hardware.models import ComponentStatus

logger = get_monitor_logger("redfish_parser")


class RedfishParserError(ValueError):
    """Raised when a Redfish payload is severely malformed or missing required system identity."""


def clean_string(val: Any) -> str | None:
    """
    Sanitize raw Redfish string fields.

    Strips leading/trailing whitespace and converts empty strings, "n/a", or "unknown"
    placeholder strings to None.
    """
    if val is None:
        return None
    if not isinstance(val, (str, int, float)):
        return None
    text = str(val).strip()
    if not text or text.lower() in ("n/a", "unknown", "none", "null"):
        return None
    return text


def clean_int(val: Any) -> int | None:
    """
    Safely convert raw numeric values to integer.

    Preserves 0 as a valid measurement. Returns None on failure or missing input.
    """
    if val is None or val == "":
        return None
    try:
        # Handle float strings like "2400.0"
        return int(float(val))
    except (ValueError, TypeError):
        return None


def clean_float(val: Any) -> float | None:
    """
    Safely convert raw numeric values to float.

    Preserves 0.0 as a valid measurement. Returns None on failure or missing input.
    """
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def parse_status(data: dict[str, Any] | None) -> ComponentStatus | None:
    """
    Extract and normalize Redfish Status structure (State, Health, HealthRollup).

    Returns None if status data is missing or completely unpopulated.
    """
    if not data or not isinstance(data, dict):
        return None
    status_obj = data.get("Status") if "Status" in data and isinstance(data.get("Status"), dict) else data

    state = clean_string(status_obj.get("State"))
    health = clean_string(status_obj.get("Health"))
    health_rollup = clean_string(status_obj.get("HealthRollup"))

    if state is None and health is None and health_rollup is None:
        return None

    return ComponentStatus(
        state=state,
        health=health,
        health_rollup=health_rollup,
    )


def extract_component_id(data: dict[str, Any], fallback_id: str | None = None) -> str | None:
    """
    Extract stable identifier for a component.

    Prefers @odata.id, then Id, then MemberId, then fallback_id.
    Returns None if no identifier can be derived.
    """
    if not isinstance(data, dict):
        return fallback_id

    odata_id = clean_string(data.get("@odata.id"))
    if odata_id:
        return odata_id

    obj_id = clean_string(data.get("Id"))
    if obj_id:
        return obj_id

    member_id = clean_string(data.get("MemberId"))
    if member_id:
        return member_id

    return fallback_id
