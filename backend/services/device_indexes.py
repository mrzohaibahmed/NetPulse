"""MongoDB indexes for the device inventory collection."""

from __future__ import annotations

from pymongo import ASCENDING
from pymongo.errors import OperationFailure

from config.database import db
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("devices")

DUE_CLAIM_INDEX_NAME = "idx_devices_monitor_due_claim"


def ensure_device_indexes() -> None:
    """Create indexes on ``devices`` (safe to call repeatedly)."""
    try:
        db.devices.create_index(
            [("ipAddress", ASCENDING)],
            unique=True,
            name="uniq_devices_ipAddress",
        )
        logger.info("[DEVICES] unique ipAddress index ensured")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[DEVICES] Failed to ensure ipAddress index: %s", exc)

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
