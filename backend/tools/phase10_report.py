"""Merge Phase 10 capacity CSVs and print summary tables."""
from __future__ import annotations

import csv
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
LOGS = BACKEND / "logs"

SOURCES = [
    LOGS / "phase10_capacity" / "capacity_table_live.csv",
    LOGS / "phase10_capacity_1000" / "capacity_table.csv",
    LOGS / "phase10_capacity_rerun" / "capacity_table.csv",
]

FLEETS = [250, 500, 750, 1000]
MIXES = ["all_up", "half", "all_down", "intermittent", "slow"]
CONC = [20, 30, 40, 50, 60, 64]


def load_rows() -> list[dict]:
    by_key: dict[tuple, dict] = {}
    for path in SOURCES:
        if not path.is_file():
            continue
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                key = (
                    int(row["Devices"]),
                    row["Mix"],
                    int(row["Concurrency"]),
                )
                # Prefer measured runs over inherited duplicates; later files win if same key.
                by_key[key] = row
    return list(by_key.values())


def min_pass(rows: list[dict], devices: int, mix: str | None = None) -> int | None:
    subset = [
        r
        for r in rows
        if int(r["Devices"]) == devices
        and (mix is None or r["Mix"] == mix)
        and r["Result"] == "PASS"
    ]
    if not subset:
        return None
    return min(int(r["Concurrency"]) for r in subset)


def worst_mix_pass(rows: list[dict], devices: int) -> dict:
    out = {}
    for mix in MIXES:
        out[mix] = min_pass(rows, devices, mix)
    return out


def compact_table(rows: list[dict]) -> None:
    """Representative row per fleet/mix at minimum PASS concurrency (or best attempt)."""
    print("\n## Capacity table (minimum PASS concurrency per mix)\n")
    hdr = (
        "Devices | Mix | Concurrency | WaveS | QueueMax | MongoOps/s | MongoP95ms | "
        "Overlap | p50_sts | p95_sts | Result | Bottleneck"
    )
    print(hdr)
    print("-" * len(hdr))
    for devices in FLEETS:
        for mix in MIXES:
            c = min_pass(rows, devices, mix)
            candidates = [
                r
                for r in rows
                if int(r["Devices"]) == devices and r["Mix"] == mix
            ]
            if c is None:
                # show best (lowest wave among limited headroom / fail)
                candidates.sort(
                    key=lambda r: float(r["WaveS"] or 999),
                )
                if not candidates:
                    continue
                r = candidates[0]
                note = " (no PASS; best wave)"
            else:
                r = next(x for x in candidates if int(x["Concurrency"]) == c)
                note = ""
            print(
                f"{devices} | {mix} | {r['Concurrency']} | {float(r['WaveS']):.1f}s | "
                f"{r['QueueMax']} | {float(r['MongoOpsPerS']):.0f} | "
                f"{float(r['MongoP95Ms']):.1f} | {r['Overlap']} | "
                f"{r.get('p50_sts') or '-'} | {r.get('p95_sts') or '-'} | "
                f"{r['Result']}{note} | {r['Bottleneck']}"
            )


def main() -> None:
    rows = load_rows()
    out_json = LOGS / "phase10_capacity" / "capacity_merged.json"
    out_json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"Merged {len(rows)} unique configurations -> {out_json}")

    print("\n=== Minimum safe concurrency (PASS only) ===")
    for devices in FLEETS:
        per = worst_mix_pass(rows, devices)
        rec = max(v for v in per.values() if v is not None) if any(per.values()) else None
        print(f"\n{devices} devices:")
        for mix, c in per.items():
            status = str(c) if c is not None else "NO PASS"
            print(f"  {mix:12} -> {status}")
        print(f"  recommended (max across mixes that PASS): {rec or 'N/A'}")

    compact_table(rows)

    # Global invariants
    overlaps = sum(int(r["Overlap"]) for r in rows)
    mongo_err = sum(int(r.get("mongo_errors") or r.get("MongoErrors") or 0) for r in rows)
    print(f"\n=== Invariants ===")
    print(f"Duplicate overlapping claims (total): {overlaps}")
    print(f"Mongo errors: {mongo_err}")


if __name__ == "__main__":
    main()
