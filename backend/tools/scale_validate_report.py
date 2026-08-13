"""
Scale validation harness wrapper (SIMULATION only).

This does NOT probe the live network. It exercises the dispatch claim /
runtime path with in-memory devices and mocked ICMP latency.

  python tools/scale_validate_report.py
  python tools/scale_validate_report.py --fleets 500,750,1000 --mixes all_up,mixed,all_down

LIVE NETWORK TEST is out of scope for this tool — document separately.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from tools.capacity_validate_60s import main as capacity_main  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    if "--fleets" not in argv and "--devices" not in argv:
        argv = [
            "--fleets",
            "500,750,1000",
            "--mixes",
            "all_up,mixed,all_down",
            "--concurrency",
            "40",
            "--interval",
            "60",
            "--cycles",
            "2",
            "--dispatcher",
            "2",
            "--out",
            "logs/scale_validate_simulation",
        ] + argv

    print("=" * 72)
    print("KIND: SIMULATION (in-memory devices, mocked ping — NOT live network)")
    print("Target cadence: 60s | concurrency: 40 (unless overridden)")
    print("=" * 72)
    return capacity_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
