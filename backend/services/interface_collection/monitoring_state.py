"""
monitoring_state.py
===================
Enterprise monitoring preference model for Storm Protection.

Separates **administrator intent** from **transient operational state**.

Canonical field
---------------
``monitoringMode``:

- ``AUTO`` — system may evaluate the port for storm analysis when operational
- ``DISABLED_BY_USER`` — administrator explicitly opted the port out

``monitoringEnabled`` is retained as a preference mirror
(``True`` iff mode is ``AUTO``). It is **never** forced off solely because
``adminStatus`` is temporarily ``down``.

Operational exclusion (admin/oper down) remains the responsibility of the
Eligibility Engine (RULE_2 / RULE_3), not the inventory classifier.
"""

from __future__ import annotations

from typing import Any, Optional

from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("interface.monitoring")

MONITORING_MODE_AUTO = "AUTO"
MONITORING_MODE_DISABLED_BY_USER = "DISABLED_BY_USER"
VALID_MONITORING_MODES = frozenset(
    {MONITORING_MODE_AUTO, MONITORING_MODE_DISABLED_BY_USER}
)

REASON_ADMINISTRATOR_DISABLED = "administrator_disabled"
REASON_ADMINISTRATIVE_DOWN = "administrative_down"
REASON_OPERATIONAL_DOWN = "operational_down"
REASON_ACTIVE = "active"


def normalize_monitoring_mode(value: Any) -> Optional[str]:
    """Return a canonical mode string, or None if value is absent/unknown."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    if text in VALID_MONITORING_MODES:
        return text
    # Tolerant aliases
    if text in {"DISABLED", "OFF", "USER", "MANUAL_OFF", "DISABLED_BY_ADMIN"}:
        return MONITORING_MODE_DISABLED_BY_USER
    if text in {"ENABLED", "ON", "DEFAULT", "SYSTEM"}:
        return MONITORING_MODE_AUTO
    return None


def preference_enabled_for_mode(mode: str) -> bool:
    """Preference mirror stored as ``monitoringEnabled``."""
    return mode != MONITORING_MODE_DISABLED_BY_USER


def resolve_monitoring_mode(
    raw: Optional[dict[str, Any]] = None,
    *,
    existing: Optional[dict[str, Any]] = None,
    default: str = MONITORING_MODE_AUTO,
) -> str:
    """
    Resolve administrator monitoring intent.

    Precedence
    ----------
    1. Explicit ``monitoringMode`` / ``monitoring_mode`` on ``raw``
    2. Explicit mode on ``existing`` (rediscovery preserve)
    3. Legacy ``monitoringEnabled=false`` **only** when accompanied by
       ``DISABLED_BY_USER`` intent is already covered by (1)/(2).
       Bare legacy ``false`` without a mode is treated as **AUTO** so
       rediscovery cannot re-latch system-forced disables.
    4. ``default`` (AUTO)
    """
    for source in (raw, existing):
        if not isinstance(source, dict):
            continue
        explicit = normalize_monitoring_mode(
            source.get("monitoringMode", source.get("monitoring_mode"))
        )
        if explicit is not None:
            return explicit

    return default


def apply_monitoring_preference(iface: dict[str, Any], mode: str) -> dict[str, Any]:
    """Write canonical monitoring preference fields onto an interface dict."""
    resolved = normalize_monitoring_mode(mode) or MONITORING_MODE_AUTO
    iface["monitoringMode"] = resolved
    iface["monitoringEnabled"] = preference_enabled_for_mode(resolved)
    return iface


def _status_is_up(status: Any) -> bool:
    return str(status or "").strip().lower() in ("up", "connected")


def compute_monitoring_view(
    *,
    monitoring_mode: Any = None,
    monitoring_enabled: Any = None,
    admin_status: Any = None,
    oper_status: Any = None,
) -> dict[str, Any]:
    """
    Build API/UI monitoring view fields.

    ``monitoringEnabled`` in the response remains the **preference** mirror
    (backward compatible with clients that treat it as admin intent).
    """
    mode = normalize_monitoring_mode(monitoring_mode)
    if mode is None:
        # Bare legacy ``monitoringEnabled=false`` was often a sticky system latch,
        # not administrator intent. Only an explicit DISABLED_BY_USER mode counts
        # as admin opt-out. Missing mode ⇒ AUTO.
        mode = MONITORING_MODE_AUTO

    administrator_disabled = mode == MONITORING_MODE_DISABLED_BY_USER
    preference = preference_enabled_for_mode(mode)
    admin_up = _status_is_up(admin_status)
    oper_up = _status_is_up(oper_status)

    if administrator_disabled:
        reason = REASON_ADMINISTRATOR_DISABLED
        effective = False
    elif not admin_up:
        reason = REASON_ADMINISTRATIVE_DOWN
        effective = False
    elif not oper_up:
        reason = REASON_OPERATIONAL_DOWN
        effective = False
    else:
        reason = REASON_ACTIVE
        effective = True

    return {
        "monitoringMode": mode,
        "monitoringEnabled": preference,
        "administratorDisabled": administrator_disabled,
        "effectiveMonitoring": effective,
        "monitoringReason": reason,
    }


def set_interface_monitoring_mode(
    device_id,
    interface_name: str,
    mode: str,
) -> Optional[dict[str, Any]]:
    """
    Persist administrator monitoring intent for one interface.

    Returns the updated interface document, or None if not found.
    """
    from bson import ObjectId
    from config.database import db  # noqa: PLC0415
    from datetime import datetime, timezone

    resolved = normalize_monitoring_mode(mode)
    if resolved is None:
        raise ValueError(
            f"Invalid monitoringMode; expected one of {sorted(VALID_MONITORING_MODES)}"
        )

    oid = device_id
    if isinstance(oid, str) and ObjectId.is_valid(oid):
        oid = ObjectId(oid)

    name = str(interface_name or "").strip()
    if not name:
        raise ValueError("Interface name is required")

    now = datetime.now(timezone.utc)
    preference = preference_enabled_for_mode(resolved)
    from pymongo import ReturnDocument  # noqa: PLC0415

    result = db.interfaces.find_one_and_update(
        {"deviceId": oid, "name": name},
        {
            "$set": {
                "monitoringMode": resolved,
                "monitoringEnabled": preference,
                "updatedAt": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    return result


def migrate_interface_monitoring_state(*, apply: bool = True) -> dict[str, Any]:
    """
    Idempotent migration away from the sticky ``monitoringEnabled=false`` latch.

    Rules
    -----
    - Documents already marked ``DISABLED_BY_USER`` are left alone (intent preserved).
    - Documents with ``AUTO`` get ``monitoringEnabled`` synced to ``True``.
    - Documents **without** ``monitoringMode``:
      - Sticky case (preference false but admin+oper up, no user-disable flag)
        → ``AUTO`` + ``monitoringEnabled=True``
      - All other legacy system-forced falses (including admin-down)
        → ``AUTO`` + ``monitoringEnabled=True``
        because the old classifier forced false on admin-down and no
        administrator-disable API existed.
      - Preference true / missing → ``AUTO`` + ``monitoringEnabled=True``

    Safe to run on every bootstrap.
    """
    from config.database import db  # noqa: PLC0415
    from datetime import datetime, timezone

    scanned = 0
    restored_sticky = 0
    upgraded_legacy = 0
    synced_auto = 0
    preserved_user = 0
    unchanged = 0
    errors = 0
    now = datetime.now(timezone.utc)

    try:
        cursor = db.interfaces.find({})
    except Exception as exc:  # noqa: BLE001
        logger.error("[MONITORING-MIGRATE] Failed to query interfaces: %s", exc)
        return {
            "scanned": 0,
            "restoredSticky": 0,
            "upgradedLegacy": 0,
            "syncedAuto": 0,
            "preservedUserDisabled": 0,
            "unchanged": 0,
            "errors": 1,
            "applied": apply,
        }

    for doc in cursor:
        scanned += 1
        try:
            doc_id = doc.get("_id")
            mode = normalize_monitoring_mode(doc.get("monitoringMode"))
            preference = doc.get("monitoringEnabled")
            admin_status = doc.get("adminStatus")
            oper_status = doc.get("operStatus")

            if mode == MONITORING_MODE_DISABLED_BY_USER:
                desired_mode = MONITORING_MODE_DISABLED_BY_USER
                desired_pref = False
                needs_write = (
                    doc.get("monitoringMode") != desired_mode
                    or doc.get("monitoringEnabled") is not desired_pref
                )
                if needs_write and apply:
                    db.interfaces.update_one(
                        {"_id": doc_id},
                        {
                            "$set": {
                                "monitoringMode": desired_mode,
                                "monitoringEnabled": desired_pref,
                                "updatedAt": now,
                            }
                        },
                    )
                preserved_user += 1
                continue

            if mode == MONITORING_MODE_AUTO:
                desired_mode = MONITORING_MODE_AUTO
                desired_pref = True
                needs_write = (
                    doc.get("monitoringMode") != desired_mode
                    or doc.get("monitoringEnabled") is not desired_pref
                )
                if not needs_write:
                    unchanged += 1
                    continue
                if apply:
                    db.interfaces.update_one(
                        {"_id": doc_id},
                        {
                            "$set": {
                                "monitoringMode": desired_mode,
                                "monitoringEnabled": desired_pref,
                                "updatedAt": now,
                            }
                        },
                    )
                synced_auto += 1
                continue

            # No canonical mode yet — legacy document.
            desired_mode = MONITORING_MODE_AUTO
            desired_pref = True
            sticky = (
                preference is False
                and _status_is_up(admin_status)
                and _status_is_up(oper_status)
            )
            if apply:
                db.interfaces.update_one(
                    {"_id": doc_id},
                    {
                        "$set": {
                            "monitoringMode": desired_mode,
                            "monitoringEnabled": desired_pref,
                            "updatedAt": now,
                        }
                    },
                )
            if sticky:
                restored_sticky += 1
            else:
                upgraded_legacy += 1
            continue
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning(
                "[MONITORING-MIGRATE] Failed on %s: %s",
                doc.get("name"),
                exc,
            )

    summary = {
        "scanned": scanned,
        "restoredSticky": restored_sticky,
        "upgradedLegacy": upgraded_legacy,
        "syncedAuto": synced_auto,
        "preservedUserDisabled": preserved_user,
        "unchanged": unchanged,
        "errors": errors,
        "applied": apply,
    }
    logger.info(
        "[MONITORING-MIGRATE] complete | scanned=%s sticky=%s legacy=%s "
        "synced=%s preservedUser=%s unchanged=%s errors=%s applied=%s",
        scanned,
        restored_sticky,
        upgraded_legacy,
        synced_auto,
        preserved_user,
        unchanged,
        errors,
        apply,
    )
    return summary
