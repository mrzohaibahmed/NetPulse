#!/usr/bin/env python
"""Focused 500-device cadence run for the final report."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logging.disable(logging.CRITICAL)

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "tools"))

from capacity_validate_60s import run_capacity  # noqa: E402

OUT = _BACKEND / "logs" / "capacity_cadence_500.json"
OUT.parent.mkdir(parents=True, exist_ok=True)


def main() -> int:
    results = []
    for mix in ("all_up", "all_down"):
        print(f"running 500 {mix}...", flush=True)
        r = run_capacity(
            devices=500,
            mix_name=mix,
            concurrency=40,
            interval_s=60.0,
            cycles=2,
            dispatcher_s=2.0,
            timeout_ms=1000,
            retries=3,
            late_tolerance_s=5.0,
        )
        print(
            f"{mix}: {r.verdict} completed={r.attempts_completed} "
            f"avg={r.avg_interval_s} med={r.median_interval_s} "
            f"p95={r.p95_interval_s} max={r.max_interval_s} "
            f"late={r.missed_deadlines} sig_late={r.significantly_late} "
            f"stale={r.stale_overwrites} dup={r.duplicate_claims} "
            f"wall={r.wall_s:.1f}s notes={r.notes}",
            flush=True,
        )
        results.append({k: v for k, v in r.__dict__.items() if k != "attempt_times"})

    OUT.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    return 0 if all(r["verdict"] == "PASS" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
