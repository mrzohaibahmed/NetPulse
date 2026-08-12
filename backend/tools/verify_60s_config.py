"""Verify 60s cadence config chain + migration idempotency (no architecture changes)."""
from __future__ import annotations

import logging
import sys
from datetime import timedelta
from pathlib import Path

logging.disable(logging.CRITICAL)

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from services.monitor_claim import compute_next_check_at  # noqa: E402
from services.monitor_schedule_migration import (  # noqa: E402
    ensure_monitor_cadence_settings,
    ensure_monitor_schedule_migration,
)
from services.settings_service import (  # noqa: E402
    DEFAULT_SETTINGS,
    get_monitor_dispatcher_interval_seconds,
    get_monitor_ping_concurrency,
    get_monitor_runtime_mode,
    get_ping_config,
    get_settings,
)
from utils.utc import utc_now  # noqa: E402


def main() -> int:
    s0 = get_settings()
    print(
        "before:",
        {
            "pingInterval": s0.get("pingInterval"),
            "pingConcurrency": s0.get("pingConcurrency"),
            "V2": s0.get("monitorDispatchCadenceV2"),
        },
    )
    r1 = ensure_monitor_schedule_migration()
    print("migration1:", r1)
    s1 = get_settings()
    print(
        "after1:",
        {
            "pingInterval": s1.get("pingInterval"),
            "pingConcurrency": s1.get("pingConcurrency"),
            "V2": s1.get("monitorDispatchCadenceV2"),
        },
    )
    r2 = ensure_monitor_cadence_settings()
    print("migration2_idempotent:", r2)
    s2 = get_settings()
    print(
        "after2:",
        {
            "pingInterval": s2.get("pingInterval"),
            "pingConcurrency": s2.get("pingConcurrency"),
        },
    )

    cfg = get_ping_config()
    now = utc_now()
    prev = now - timedelta(seconds=1)
    nxt = compute_next_check_at(
        claim_now=now,
        previous_next_check_at=prev,
        interval_seconds=int(cfg["interval"]),
    )
    print(
        "runtime:",
        {
            "DEFAULT_SETTINGS.pingInterval": DEFAULT_SETTINGS["pingInterval"],
            "mode": get_monitor_runtime_mode(),
            "dispatcher": get_monitor_dispatcher_interval_seconds(),
            "get_ping_config.interval": cfg["interval"],
            "concurrency": get_monitor_ping_concurrency(),
            "nextCheckAt_delta_s": (nxt - prev).total_seconds(),
        },
    )

    from config.database import db  # noqa: PLC0415

    print(
        "devices:",
        {
            "monitored": db.devices.count_documents({"monitor": True}),
            "missing_nextCheckAt": db.devices.count_documents(
                {
                    "monitor": True,
                    "$or": [
                        {"nextCheckAt": {"$exists": False}},
                        {"nextCheckAt": None},
                    ],
                }
            ),
            "per_device_pingInterval_set": db.devices.count_documents(
                {"pingInterval": {"$ne": None}}
            ),
        },
    )

    ok = (
        int(s2.get("pingInterval") or 0) == 60
        and int(cfg["interval"]) == 60
        and int(DEFAULT_SETTINGS["pingInterval"]) == 60
        and get_monitor_runtime_mode() == "dispatch"
        and get_monitor_ping_concurrency() == 40
        and 1 <= get_monitor_dispatcher_interval_seconds() <= 15
        and (nxt - prev).total_seconds() == 60
        and r2.get("skipped") is True
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
