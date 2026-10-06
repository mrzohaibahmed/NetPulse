"""MongoDB indexes for the device inventory collection."""

from __future__ import annotations

from pymongo import ASCENDING
from pymongo.errors import OperationFailure

from config.database import db
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("devices")

DUE_CLAIM_INDEX_NAME = "idx_devices_monitor_due_claim"
IP_UNIQUE_INDEX_NAME = "uniq_devices_ipAddress"
IP_UNIQUE_STAGING_NAME = "uniq_devices_ipAddress_partial_mig"
ILO_UNIQUE_INDEX_NAME = "uniq_devices_iloAddress"

# Index only populated, non-empty string addresses (Option A / Step 2).
_POPULATED_STRING_IP = {"ipAddress": {"$type": "string", "$gt": ""}}
_POPULATED_STRING_ILO = {"iloAddress": {"$type": "string", "$gt": ""}}


def _index_map() -> dict:
    return {idx["name"]: idx for idx in db.devices.list_indexes()}


def _partial_filter_matches(index_doc: dict, expected: dict) -> bool:
    partial = index_doc.get("partialFilterExpression")
    if partial is None:
        return False
    return dict(partial) == expected


def _is_desired_address_unique_index(index_doc: dict, *, field: str, partial: dict) -> bool:
    key = dict(index_doc.get("key") or {})
    return (
        bool(index_doc.get("unique"))
        and key == {field: 1}
        and _partial_filter_matches(index_doc, partial)
    )


def _is_legacy_non_partial_unique(index_doc: dict, *, field: str) -> bool:
    key = dict(index_doc.get("key") or {})
    return (
        bool(index_doc.get("unique"))
        and key == {field: 1}
        and index_doc.get("partialFilterExpression") is None
    )


def _raise_unexpected_index(name: str, index_doc: dict, expected: str) -> None:
    raise RuntimeError(
        f"Unexpected devices index '{name}': {dict(index_doc)}. Expected {expected}. "
        "Refusing to weaken uniqueness or silently rewrite indexes."
    )


def _create_partial_unique(*, field: str, name: str, partial: dict) -> None:
    db.devices.create_index(
        [(field, ASCENDING)],
        unique=True,
        name=name,
        partialFilterExpression=partial,
    )
    logger.info(
        "[DEVICES] Created partial unique index | name=%s | field=%s",
        name,
        field,
    )


def _ensure_partial_unique_address_index(
    *,
    field: str,
    final_name: str,
    staging_name: str | None,
    partial: dict,
) -> None:
    """
    Ensure a unique partial index on ``field``.

    MongoDB cannot keep two indexes with the same key pattern and identical
    options under different names. Migration for a legacy non-partial unique:

      1. Create staging partial unique (alongside legacy unique — different options)
      2. Drop legacy unique
      3. Drop staging
      4. Create final-named partial unique

    Interrupted migrations (staging present, final absent) are recovered by
    completing steps 3–4. Unexpected index definitions fail closed.
    """
    indexes = _index_map()
    final = indexes.get(final_name)
    staging = indexes.get(staging_name) if staging_name else None

    if final is not None and _is_desired_address_unique_index(
        final, field=field, partial=partial
    ):
        if staging_name and staging is not None:
            db.devices.drop_index(staging_name)
            logger.info(
                "[DEVICES] Dropped leftover staging index | name=%s",
                staging_name,
            )
        logger.info(
            "[DEVICES] Partial unique %s index already correct | name=%s",
            field,
            final_name,
        )
        return

    # Recover interrupted migration: staging OK, final missing.
    if (
        staging_name
        and staging is not None
        and final is None
        and _is_desired_address_unique_index(staging, field=field, partial=partial)
    ):
        db.devices.drop_index(staging_name)
        logger.info(
            "[DEVICES] Recovering interrupted migration; dropped staging | name=%s",
            staging_name,
        )
        _create_partial_unique(field=field, name=final_name, partial=partial)
        return

    if final is not None and _is_legacy_non_partial_unique(final, field=field):
        if not staging_name:
            _raise_unexpected_index(
                final_name,
                final,
                f"unique partial on {field} with filter {partial}",
            )

        if staging is None:
            _create_partial_unique(field=field, name=staging_name, partial=partial)
        elif not _is_desired_address_unique_index(
            staging, field=field, partial=partial
        ):
            _raise_unexpected_index(
                staging_name,
                staging,
                f"unique partial on {field} with filter {partial}",
            )

        db.devices.drop_index(final_name)
        logger.info("[DEVICES] Dropped legacy unique index | name=%s", final_name)

        # Same key+options cannot coexist under two names — drop staging, then
        # recreate under the canonical final name.
        db.devices.drop_index(staging_name)
        logger.info("[DEVICES] Dropped staging index | name=%s", staging_name)
        _create_partial_unique(field=field, name=final_name, partial=partial)
        return

    if final is not None:
        _raise_unexpected_index(
            final_name,
            final,
            f"unique partial on {field} with filter {partial}",
        )

    if staging_name and staging is not None:
        _raise_unexpected_index(
            staging_name,
            staging,
            f"no leftover staging without a recoverable final index for {field}",
        )

    _create_partial_unique(field=field, name=final_name, partial=partial)


def ensure_device_indexes() -> None:
    """Create indexes on ``devices`` (safe to call repeatedly)."""
    try:
        _ensure_partial_unique_address_index(
            field="ipAddress",
            final_name=IP_UNIQUE_INDEX_NAME,
            staging_name=IP_UNIQUE_STAGING_NAME,
            partial=_POPULATED_STRING_IP,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[DEVICES] Failed to ensure ipAddress unique index: %s", exc)
        raise

    try:
        _ensure_partial_unique_address_index(
            field="iloAddress",
            final_name=ILO_UNIQUE_INDEX_NAME,
            staging_name=None,
            partial=_POPULATED_STRING_ILO,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[DEVICES] Failed to ensure iloAddress unique index: %s", exc)
        raise

    # Due/claim lookup for dispatch monitoring (Phase 2).
    # Partial on monitor=true keeps the index small; no TTL / no unique claim id.
    try:
        db.devices.create_index(
            [("nextCheckAt", ASCENDING), ("scanClaimExpiresAt", ASCENDING)],
            name=DUE_CLAIM_INDEX_NAME,
            partialFilterExpression={"monitor": True},
        )
        logger.info("[DEVICES] due/claim index ensured | name=%s", DUE_CLAIM_INDEX_NAME)
    except OperationFailure as exc:
        # Older servers / incompatible partial options — fall back to full compound.
        logger.warning(
            "[DEVICES] Partial due/claim index failed (%s); "
            "falling back to compound {monitor, nextCheckAt, scanClaimExpiresAt}",
            exc,
        )
        try:
            db.devices.create_index(
                [
                    ("monitor", ASCENDING),
                    ("nextCheckAt", ASCENDING),
                    ("scanClaimExpiresAt", ASCENDING),
                ],
                name=DUE_CLAIM_INDEX_NAME,
            )
            logger.info(
                "[DEVICES] due/claim compound fallback index ensured | name=%s",
                DUE_CLAIM_INDEX_NAME,
            )
        except Exception as fallback_exc:  # noqa: BLE001
            logger.warning(
                "[DEVICES] Failed to ensure due/claim fallback index: %s",
                fallback_exc,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[DEVICES] Failed to ensure due/claim index: %s", exc)
