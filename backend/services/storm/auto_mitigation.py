"""
Bounded automatic mitigation dispatch for the safety/prepare scheduler job.

Does not change mitigation decision rules — only limits batch size and
processes READY_FOR_MITIGATION incidents in stable order with existing locks.
"""

from __future__ import annotations

import os
from typing import Any

from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.auto_mitigation")

DEFAULT_BATCH_SIZE = 5
MAX_BATCH_SIZE = 50


def get_mitigation_batch_size() -> int:
    raw = (os.getenv("STORM_MITIGATION_BATCH_SIZE") or "").strip()
    if not raw:
        return DEFAULT_BATCH_SIZE
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_BATCH_SIZE
    return max(1, min(value, MAX_BATCH_SIZE))


def fetch_ready_incidents_batch(*, limit: int | None = None) -> list[dict[str, Any]]:
    """Return up to ``limit`` incidents in READY_FOR_MITIGATION (stable order)."""
    from config.database import db  # noqa: PLC0415

    batch = limit if limit is not None else get_mitigation_batch_size()
    cursor = (
        db.storm_incidents.find({"status": "READY_FOR_MITIGATION"})
        .sort([("updatedAt", 1), ("incidentId", 1)])
        .limit(max(1, int(batch)))
    )
    return list(cursor)


def run_automatic_mitigation_batch(*, cycle_id: str | None = None) -> dict[str, Any]:
    """
    Execute SYSTEM SHUTDOWN for a bounded batch of READY incidents.

    Each call to ``execute_mitigation`` acquires mitigation locks — duplicate
    concurrent execution remains prevented even if multiple batches overlap
    across processes (only one wins the lock).
    """
    from services.storm.mitigation.engine import execute_mitigation  # noqa: PLC0415

    batch_size = get_mitigation_batch_size()
    ready = fetch_ready_incidents_batch(limit=batch_size)
    summary: dict[str, Any] = {
        "batchSize": batch_size,
        "readyFetched": len(ready),
        "executed": 0,
        "success": 0,
        "failed": 0,
        "results": [],
    }

    if not ready:
        return summary

    logger.info(
        "[SCHEDULER] Automatic mitigation batch | count=%s | cycleId=%s",
        len(ready),
        cycle_id or "-",
    )

    for inc in ready:
        inc_id = inc.get("incidentId")
        if not inc_id:
            continue
        summary["executed"] += 1
        try:
            res = execute_mitigation(str(inc_id), "SHUTDOWN", operator="SYSTEM")
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Auto-mitigation exception | incident=%s | cycleId=%s | %s",
                inc_id,
                cycle_id,
                exc,
            )
            summary["failed"] += 1
            summary["results"].append(
                {"incidentId": inc_id, "success": False, "error": "execution_failed"}
            )
            continue

        ok = bool(res.get("success"))
        if ok:
            summary["success"] += 1
        else:
            summary["failed"] += 1
        summary["results"].append(
            {
                "incidentId": inc_id,
                "success": ok,
                "status": res.get("status"),
            }
        )
        logger.info(
            "[SCHEDULER] Auto-mitigation status | incident=%s | success=%s | cycleId=%s",
            inc_id,
            ok,
            cycle_id or "-",
        )

    return summary
