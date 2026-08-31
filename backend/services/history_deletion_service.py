"""
Admin-only manual deletion of retention-managed history collections.

Uses delete_many({}) only — never drops indexes, modifies TTL, or changes settings.
"""

from __future__ import annotations

from typing import Any

from config.database import db

VALID_HISTORY_DELETION_SCOPES: frozenset[str] = frozenset(
    {"ping", "telemetry", "incidents", "all"}
)

PING_HISTORY_COLLECTIONS: tuple[str, ...] = ("pingHistory",)

TELEMETRY_HISTORY_COLLECTIONS: tuple[str, ...] = (
    "interface_stats",
    "eligibility_results",
    "storm_risk_history",
    "storm_confirmation_history",
    "storm_safety_history",
)

INCIDENT_HISTORY_COLLECTIONS: tuple[str, ...] = (
    "storm_mitigation_history",
    "storm_recovery_history",
)

ALL_HISTORY_COLLECTIONS: tuple[str, ...] = (
    PING_HISTORY_COLLECTIONS
    + TELEMETRY_HISTORY_COLLECTIONS
    + INCIDENT_HISTORY_COLLECTIONS
)

_SCOPE_COLLECTIONS: dict[str, tuple[str, ...]] = {
    "ping": PING_HISTORY_COLLECTIONS,
    "telemetry": TELEMETRY_HISTORY_COLLECTIONS,
    "incidents": INCIDENT_HISTORY_COLLECTIONS,
    "all": ALL_HISTORY_COLLECTIONS,
}


class HistoryDeletionError(RuntimeError):
    """One or more collection deletions failed."""

    def __init__(self, results: dict[str, Any]):
        self.results = results
        failed = list((results.get("failed") or {}).keys())
        detail = f" for: {', '.join(failed)}" if failed else ""
        if results.get("result") == "partial_failure":
            message = f"History deletion partially failed{detail}"
        else:
            message = f"History deletion failed{detail}"
        super().__init__(message)


def resolve_history_deletion_collections(scope: str) -> tuple[str, ...]:
    normalized = str(scope or "").strip().lower()
    if normalized not in VALID_HISTORY_DELETION_SCOPES:
        raise ValueError(
            "Invalid history deletion scope. Allowed values: ping, telemetry, incidents, all"
        )
    return _SCOPE_COLLECTIONS[normalized]


def delete_history(scope: str) -> dict[str, Any]:
    """
    Delete all documents from the collections mapped to ``scope``.

    Returns structured counts on full success. Raises ``HistoryDeletionError`` when
    any collection deletion fails (partial or total failure).
    """
    collections = resolve_history_deletion_collections(scope)
    deleted: dict[str, int] = {}
    failed: dict[str, str] = {}

    for collection_name in collections:
        try:
            result = db[collection_name].delete_many({})
            deleted[collection_name] = int(result.deleted_count)
        except Exception as exc:  # noqa: BLE001
            failed[collection_name] = str(exc)

    total_deleted = sum(deleted.values())
    outcome: dict[str, Any] = {
        "success": not failed,
        "scope": str(scope).strip().lower(),
        "deleted": deleted,
        "totalDeleted": total_deleted,
    }

    if failed:
        outcome["failed"] = failed
        if deleted:
            outcome["result"] = "partial_failure"
        else:
            outcome["result"] = "failure"
        raise HistoryDeletionError(outcome)

    outcome["result"] = "success"
    return outcome
