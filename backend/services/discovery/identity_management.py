"""
Identity field ownership for device hostname and deviceType.

Automatic classification enriches inventory but must not overwrite fields
an operator has intentionally set. Missing ``identityManagement`` means auto.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from config.database import db
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("discovery")

OWNERSHIP_AUTO = "auto"
OWNERSHIP_MANUAL = "manual"

_IDENTITY_FIELDS = frozenset({"hostname", "deviceType"})


def get_identity_management(device: dict | None) -> dict[str, str]:
    """Return ownership map; missing keys default to ``auto``."""
    raw = (device or {}).get("identityManagement") or {}
    return {
        "hostname": _normalize_ownership(raw.get("hostname")),
        "deviceType": _normalize_ownership(raw.get("deviceType")),
    }


def _normalize_ownership(value: Any) -> str:
    if str(value or "").strip().lower() == OWNERSHIP_MANUAL:
        return OWNERSHIP_MANUAL
    return OWNERSHIP_AUTO


def is_identity_field_manual(device: dict | None, field: str) -> bool:
    """True when ``field`` is operator-managed (``manual``)."""
    if field not in _IDENTITY_FIELDS:
        return False
    return get_identity_management(device).get(field) == OWNERSHIP_MANUAL


def ownership_for_device_edit(
    device: dict,
    update_data: dict[str, Any],
) -> dict[str, str] | None:
    """
    Build ``identityManagement`` updates when PUT changes hostname or deviceType.

    Only fields that actually changed are marked ``manual``; others keep
    their existing ownership (or default to auto).
    """
    if "hostname" not in update_data and "deviceType" not in update_data:
        return None

    current = get_identity_management(device)
    changed = False

    if "hostname" in update_data:
        new_hostname = str(update_data["hostname"]).strip()
        old_hostname = str(device.get("hostname") or "").strip()
        if new_hostname != old_hostname:
            current["hostname"] = OWNERSHIP_MANUAL
            changed = True

    if "deviceType" in update_data:
        new_type = str(update_data["deviceType"]).strip()
        old_type = str(device.get("deviceType") or device.get("type") or "").strip()
        if new_type != old_type:
            current["deviceType"] = OWNERSHIP_MANUAL
            changed = True

    return current if changed else None


def apply_identity_fields_to_classification_update(
    existing: dict | None,
    update_fields: dict[str, Any],
    *,
    detected_hostname: str,
    detected_device_type: str,
    device_label: str,
) -> dict[str, Any]:
    """
    Remove hostname/deviceType from ``update_fields`` when ownership is manual.

    Caller must still apply merge_hostname before passing ``detected_hostname``.
    """
    mgmt = get_identity_management(existing)

    if mgmt["hostname"] == OWNERSHIP_MANUAL:
        update_fields.pop("hostname", None)
        logger.info("[IDENTITY LOCK] hostname preserved | device=%s", device_label)
    else:
        update_fields["hostname"] = detected_hostname

    if mgmt["deviceType"] == OWNERSHIP_MANUAL:
        update_fields.pop("deviceType", None)
        logger.info("[IDENTITY LOCK] deviceType preserved | device=%s", device_label)
    else:
        update_fields["deviceType"] = detected_device_type

    return update_fields


def reset_identity_management(device_id: ObjectId) -> bool:
    """
    Switch hostname and deviceType ownership back to automatic.

    Not exposed via API yet; intended for future operator tooling.
    """
    result = db.devices.update_one(
        {"_id": device_id},
        {
            "$set": {
                "identityManagement": {
                    "hostname": OWNERSHIP_AUTO,
                    "deviceType": OWNERSHIP_AUTO,
                },
                "updatedAt": datetime.now(timezone.utc),
            }
        },
    )
    return result.matched_count > 0
