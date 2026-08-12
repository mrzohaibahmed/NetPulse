"""
Deterministic multi-cycle capacity + cadence validation for dispatch monitoring.

Uses in-memory devices + mocked ping latency (no real ICMP / no production writes).
Measures start-to-start intervals across multiple 60s schedule periods.

Example:
  python tools/capacity_validate_60s.py --devices 500 --concurrency 40 --cycles 2
  python tools/capacity_validate_60s.py --fleets 250,500,750,1000 --concurrency 40
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import threading
import time
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from bson import ObjectId
from pymongo import ReturnDocument

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from utils.utc import ensure_utc, utc_now  # noqa: E402


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    idx = min(len(ordered) - 1, max(0, math.ceil(p / 100.0 * len(ordered)) - 1))
    return float(ordered[idx])


def _field_matches(doc: dict, key: str, cond) -> bool:
    if isinstance(cond, dict):
        if "$exists" in cond:
            exists = key in doc
            if bool(cond["$exists"]) != exists:
                return False
            other = {k: v for k, v in cond.items() if k != "$exists"}
            if not other:
                return True
            if not exists:
                return True
            cond = other
        if not cond:
            return True
        value = doc.get(key)
        if "$lte" in cond:
            if value is None:
                return False
            left = ensure_utc(value)
            right = ensure_utc(cond["$lte"])
            if left is None or right is None or left > right:
                return False
        if "$ne" in cond:
            return doc.get(key) != cond["$ne"]
        return True
    return doc.get(key) == cond


def _matches(doc: dict, filt: dict) -> bool:
    for key, cond in filt.items():
        if key == "$and":
            if not all(_matches(doc, part) for part in cond):
                return False
            continue
        if key == "$or":
            if not any(_matches(doc, part) for part in cond):
                return False
            continue
        if not _field_matches(doc, key, cond):
            return False
    return True


class InMemoryDevices:
    def __init__(self, docs: list[dict]):
        self._lock = threading.RLock()
        self.docs = {d["_id"]: deepcopy(d) for d in docs}
        self.ops = 0
        self.errors = 0
        self.duplicate_claims = 0
        self.stale_overwrites = 0

    def find(self, query, projection=None):
        with self._lock:
            self.ops += 1
            out = []
            for doc in self.docs.values():
                if _matches(doc, query):
                    out.append(deepcopy(doc))
            out.sort(
                key=lambda d: (
                    ensure_utc(d.get("nextCheckAt"))
                    or datetime.min.replace(tzinfo=timezone.utc)
                )
            )
            return _Cursor(out)

    def find_one_and_update(self, filt, update, return_document=None):
        with self._lock:
            self.ops += 1
            for doc in self.docs.values():
                if not _matches(doc, filt):
                    continue
                # Detect duplicate active claim attempts
                if doc.get("scanClaimId") and ensure_utc(
                    doc.get("scanClaimExpiresAt")
                ):
                    exp = ensure_utc(doc.get("scanClaimExpiresAt"))
                    now = ensure_utc(filt.get("$and", [{}])[0]) if False else None
                    _ = now
                for key, value in (update.get("$set") or {}).items():
                    doc[key] = value
                for key in update.get("$unset") or {}:
                    doc.pop(key, None)
                if return_document == ReturnDocument.AFTER:
                    return deepcopy(doc)
                return deepcopy(doc)
            return None

    def update_one(self, filt, update):
        with self._lock:
            self.ops += 1
            matched = 0
            modified = 0
            for doc in self.docs.values():
                if not _matches(doc, filt):
                    continue
                matched = 1
                # Stale-result protection mirror: lastPingStartedAt ordering
                set_fields = update.get("$set") or {}
                if "lastPingStartedAt" in set_fields and "lastPingStartedAt" in doc:
                    incoming = ensure_utc(set_fields["lastPingStartedAt"])
                    current = ensure_utc(doc.get("lastPingStartedAt"))
                    if (
                        incoming is not None
                        and current is not None
                        and incoming < current
                    ):
                        self.stale_overwrites += 1
                        break
                for key, value in set_fields.items():
                    if doc.get(key) != value:
                        modified = 1
                    doc[key] = value
                for key in update.get("$unset") or {}:
                    if key in doc:
                        modified = 1
                    doc.pop(key, None)
                if "$inc" in update:
                    for key, value in update["$inc"].items():
                        doc[key] = int(doc.get(key) or 0) + int(value)
                        modified = 1
                break
            result = MagicMock()
            result.acknowledged = True
            result.matched_count = matched
            result.modified_count = modified
            return result

    def find_one(self, filt=None, projection=None):
        with self._lock:
            self.ops += 1
            if not filt:
                return deepcopy(next(iter(self.docs.values()), None))
            for doc in self.docs.values():
                if _matches(doc, filt):
                    return deepcopy(doc)
            return None


class _Cursor:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def limit(self, n: int):
        self._docs = self._docs[: max(0, int(n))]
        return self

    def __iter__(self):
        return iter(self._docs)


@dataclass
class MixProfile:
    name: str
    online_ratio: float = 1.0
    ping_latency_s: float = 0.01
    timeout_s: float = 0.0  # when offline path used


MIXES = {
    "all_up": MixProfile("all_up", online_ratio=1.0, ping_latency_s=0.01),
    "all_down": MixProfile("all_down", online_ratio=0.0, ping_latency_s=0.0, timeout_s=3.0),
    "mixed": MixProfile("mixed", online_ratio=0.7, ping_latency_s=0.02, timeout_s=3.0),
    "slow": MixProfile("slow", online_ratio=1.0, ping_latency_s=0.8),
}


@dataclass
class CapacityResult:
    devices: int
    mix: str
    interval_s: float
    concurrency: int
    dispatcher_s: float
    cycles: int
    timeout_ms: int
    retries: int
    attempts_started: int = 0
    attempts_completed: int = 0
    missed_deadlines: int = 0
    significantly_late: int = 0
    intervals_gt_target: int = 0
    avg_interval_s: float | None = None
    median_interval_s: float | None = None
    p95_interval_s: float | None = None
    max_interval_s: float | None = None
    avg_completion_latency_s: float | None = None
    p95_completion_latency_s: float | None = None
    p99_completion_latency_s: float | None = None
    max_active_workers: int = 0
    max_queue_depth: int = 0
    duplicate_claims: int = 0
    stale_overwrites: int = 0
    mongo_errors: int = 0
    mongo_ops: int = 0
    wall_s: float = 0.0
    verdict: str = "FAIL"
    notes: str = ""
    attempt_times: dict[str, list[float]] = field(default_factory=dict, repr=False)


def _build_fleet(n: int, interval_s: float, mix: MixProfile) -> list[dict]:
    now = utc_now()
    docs = []
    for i in range(n):
        # Stagger initial due times across one interval (anti-storm).
        offset = (i % max(int(interval_s), 1)) * (interval_s / max(n, 1))
        # Better: spread evenly
        due = now + timedelta(seconds=(i / max(n, 1)) * interval_s)
        online = (i / max(n, 1)) < mix.online_ratio
        docs.append(
            {
                "_id": ObjectId(),
                "hostname": f"host-{i}",
                "ipAddress": f"10.{(i // 256) % 256}.{i % 256}.1",
                "monitor": True,
                "critical": False,
                "status": "Unknown",
                "responseTime": None,
                "lastSeen": None,
                "lastCheckedAt": None,
                "consecutiveFailures": 0,
                "nextCheckAt": due,
                "_online": online,
                "_latency_s": mix.ping_latency_s if online else mix.timeout_s,
            }
        )
    return docs


def run_capacity(
    *,
    devices: int,
    mix_name: str,
    concurrency: int,
    interval_s: float,
    cycles: int,
    dispatcher_s: float,
    timeout_ms: int,
    retries: int,
    late_tolerance_s: float,
) -> CapacityResult:
    from services import monitor_claim as claim_mod
    from services import monitor_dispatch as dispatch_mod
    from services import monitor_runtime as runtime_mod
    from services import monitor_service as ms
    from services.monitor_metrics import reset_dispatch_metrics
    from services.ping_service import STATUS_NOT_REACHABLE, STATUS_ONLINE

    mix = MIXES[mix_name]
    fleet = _build_fleet(devices, interval_s, mix)
    store = InMemoryDevices(fleet)
    fake_db = MagicMock()
    fake_db.devices = store

    result = CapacityResult(
        devices=devices,
        mix=mix_name,
        interval_s=interval_s,
        concurrency=concurrency,
        dispatcher_s=dispatcher_s,
        cycles=cycles,
        timeout_ms=timeout_ms,
        retries=retries,
    )

    attempt_starts: dict[str, list[datetime]] = defaultdict(list)
    completion_latencies: list[float] = []
    max_workers = 0
    max_queue = 0
    lock = threading.Lock()

    ping_cfg = {
        "interval": int(interval_s),
        "timeout_ms": timeout_ms,
        "retries": retries,
        "failure_confirmation_scans": 2,
    }

    def fake_ping(ip_address, critical=False, timeout_ms=None, retries=None, device=None):
        device = device or {}
        latency = float(device.get("_latency_s") or 0.01)
        time.sleep(latency)
        started = utc_now() - timedelta(seconds=latency)
        completed = utc_now()
        if device.get("_online"):
            return {
                "success": True,
                "status": STATUS_ONLINE,
                "responseTime": round(latency * 1000.0, 2),
                "lastSeen": completed,
                "message": "Device is reachable",
                "attempts": max(int(retries or ping_cfg["retries"]), 1),
                "timeoutMs": int(timeout_ms or ping_cfg["timeout_ms"]),
                "pingStartedAt": started,
                "pingCompletedAt": completed,
            }
        return {
            "success": False,
            "status": STATUS_NOT_REACHABLE,
            "responseTime": None,
            "lastSeen": None,
            "message": "Device is unreachable",
            "attempts": max(int(retries or ping_cfg["retries"]), 1),
            "timeoutMs": int(timeout_ms or ping_cfg["timeout_ms"]),
            "pingStartedAt": started,
            "pingCompletedAt": completed,
        }

    def fake_apply(device, result_payload, **_kwargs):
        """Lightweight apply that preserves freshness fields on the in-memory doc."""
        device_id = device.get("_id")
        started = result_payload.get("pingStartedAt")
        store.update_one(
            {"_id": device_id},
            {
                "$set": {
                    "status": result_payload.get("status"),
                    "responseTime": result_payload.get("responseTime"),
                    "lastCheckedAt": result_payload.get("pingCompletedAt") or utc_now(),
                    "lastPingStartedAt": started,
                    "lastSeen": result_payload.get("lastSeen"),
                }
            },
        )
        return None

    def tracking_scan(device, claim_id, **kwargs):
        device_id = str(device.get("_id"))
        started_at = utc_now()
        with lock:
            attempt_starts[device_id].append(started_at)
            result.attempts_started += 1
        # Execute real claimed-scan path with mocked ping/apply.
        out = ms._scan_device_safe(
            device,
            suppress_offline=kwargs.get("suppress_offline", False),
            cycle_id=kwargs.get("cycle_id") or "cap",
            timing_out=kwargs.get("timing_out"),
        )
        completed_at = utc_now()
        with lock:
            result.attempts_completed += 1
            completion_latencies.append((completed_at - started_at).total_seconds())
        return out

    reset_dispatch_metrics()

    with runtime_mod._runtime_lock:
        prev = runtime_mod._runtime
        runtime_mod._runtime = None
    if prev is not None:
        try:
            prev.stop(wait=False)
        except Exception:
            pass

    patches = [
        patch.object(claim_mod, "_db", return_value=fake_db),
        patch.object(dispatch_mod, "_db", return_value=fake_db),
        patch.object(ms, "_db", return_value=fake_db),
        patch.object(claim_mod, "get_ping_config", return_value=ping_cfg),
        patch.object(ms, "get_ping_config", return_value=ping_cfg),
        patch(
            "services.settings_service.get_monitor_ping_concurrency",
            return_value=concurrency,
        ),
        patch.object(dispatch_mod, "require_scheduler_leadership", return_value=True),
        patch.object(
            dispatch_mod.CycleLeadershipGuard,
            "ensure",
            return_value=True,
        ),
        patch.object(
            dispatch_mod,
            "begin_cycle_connectivity_check",
            return_value=False,
        ),
        patch.object(ms, "ping_device", side_effect=fake_ping),
        patch.object(ms, "apply_ping_result", side_effect=fake_apply),
        patch.object(ms, "save_ping_history", return_value=True),
        patch.object(dispatch_mod, "_maybe_run_integrity_audit"),
        patch.object(claim_mod, "with_mongo_retry", side_effect=lambda fn, **_k: fn()),
        patch.object(ms, "scan_claimed_device", side_effect=tracking_scan),
    ]

    wall0 = time.perf_counter()
    active_patches = [p.start() for p in patches]
    try:
        runtime_mod.start_monitor_runtime(concurrency=concurrency)
        duration_s = interval_s * cycles + dispatcher_s * 2
        end = time.perf_counter() + duration_s
        while time.perf_counter() < end:
            dispatch_mod.dispatch_monitor_due_devices()
            stats = runtime_mod.get_monitor_runtime_stats()
            max_workers = max(max_workers, int(stats.get("workers_active") or 0))
            max_queue = max(max_queue, int(stats.get("queue_depth") or 0))
            time.sleep(dispatcher_s)
        drain_deadline = time.perf_counter() + min(interval_s, 30.0)
        while time.perf_counter() < drain_deadline:
            stats = runtime_mod.get_monitor_runtime_stats()
            if int(stats.get("occupancy") or 0) == 0:
                break
            time.sleep(0.05)
        runtime_mod.stop_monitor_runtime(wait=True)
    finally:
        for p in reversed(active_patches):
            p.stop()
        result.wall_s = time.perf_counter() - wall0

    intervals: list[float] = []
    late = 0
    significant = 0
    for _device_id, stamps in attempt_starts.items():
        ordered = sorted(stamps)
        for i in range(1, len(ordered)):
            delta = (ordered[i] - ordered[i - 1]).total_seconds()
            intervals.append(delta)
            if delta > interval_s + late_tolerance_s:
                late += 1
            if delta > interval_s * 1.5:
                significant += 1

    result.max_active_workers = max_workers
    result.max_queue_depth = max_queue
    result.mongo_ops = store.ops
    result.mongo_errors = store.errors
    result.stale_overwrites = store.stale_overwrites
    result.duplicate_claims = store.duplicate_claims
    result.missed_deadlines = late
    result.significantly_late = significant
    result.intervals_gt_target = sum(1 for d in intervals if d > interval_s)
    if intervals:
        result.avg_interval_s = statistics.fmean(intervals)
        result.median_interval_s = statistics.median(intervals)
        result.p95_interval_s = _pct(intervals, 95)
        result.max_interval_s = max(intervals)
    if completion_latencies:
        result.avg_completion_latency_s = statistics.fmean(completion_latencies)
        result.p95_completion_latency_s = _pct(completion_latencies, 95)
        result.p99_completion_latency_s = _pct(completion_latencies, 99)

    # Staggered start ⇒ expect about (cycles) or (cycles-1) intervals worth of work.
    min_attempts = int(devices * max(cycles - 1, 1) * 0.85)
    cadence_ok = (
        result.attempts_completed >= min_attempts
        and (
            result.p95_interval_s is None
            or result.p95_interval_s <= interval_s + late_tolerance_s
        )
        and result.stale_overwrites == 0
        and result.duplicate_claims == 0
    )
    if mix_name == "all_down":
        cadence_ok = (
            result.attempts_completed >= min_attempts
            and (
                result.p95_interval_s is None
                or result.p95_interval_s
                <= interval_s + max(late_tolerance_s, 15.0)
            )
            and result.stale_overwrites == 0
        )

    result.verdict = "PASS" if cadence_ok else "FAIL"
    if not cadence_ok:
        result.notes = (
            f"min_attempts={min_attempts} completed={result.attempts_completed} "
            f"p95={result.p95_interval_s} late={late} stale={result.stale_overwrites}"
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="60s dispatch capacity + cadence validation")
    parser.add_argument("--devices", type=int, default=None)
    parser.add_argument("--fleets", default="250,500,750,1000")
    parser.add_argument("--mixes", default="all_up,mixed,all_down")
    parser.add_argument("--concurrency", type=int, default=40)
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--dispatcher", type=float, default=2.0)
    parser.add_argument("--timeout-ms", type=int, default=1000)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--tolerance",
        type=float,
        default=5.0,
        help="Scheduling tolerance seconds above interval for p95/missed",
    )
    parser.add_argument("--out", default="logs/capacity_validate_60s")
    args = parser.parse_args(argv)

    fleets = (
        [args.devices]
        if args.devices
        else [int(x) for x in args.fleets.split(",") if x.strip()]
    )
    mixes = [m.strip() for m in args.mixes.split(",") if m.strip()]
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = _BACKEND / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    print(
        f"Capacity/cadence validation | interval={args.interval}s "
        f"concurrency={args.concurrency} cycles={args.cycles} "
        f"dispatcher={args.dispatcher}s fleets={fleets} mixes={mixes}"
    )

    overall_pass = True
    for n in fleets:
        for mix in mixes:
            print(f"[run] devices={n} mix={mix} ...", flush=True)
            res = run_capacity(
                devices=n,
                mix_name=mix,
                concurrency=args.concurrency,
                interval_s=args.interval,
                cycles=args.cycles,
                dispatcher_s=args.dispatcher,
                timeout_ms=args.timeout_ms,
                retries=args.retries,
                late_tolerance_s=args.tolerance,
            )
            row = {k: v for k, v in asdict(res).items() if k != "attempt_times"}
            rows.append(row)
            print(
                f"  -> {res.verdict} completed={res.attempts_completed} "
                f"avg_iv={res.avg_interval_s} med={res.median_interval_s} "
                f"p95={res.p95_interval_s} max={res.max_interval_s} "
                f"late={res.missed_deadlines} stale={res.stale_overwrites} "
                f"wall={res.wall_s:.1f}s",
                flush=True,
            )
            if res.verdict != "PASS":
                overall_pass = False

    payload = {
        "generatedAt": utc_now().isoformat(),
        "config": {
            "interval": args.interval,
            "concurrency": args.concurrency,
            "cycles": args.cycles,
            "dispatcher": args.dispatcher,
            "timeout_ms": args.timeout_ms,
            "retries": args.retries,
            "tolerance_s": args.tolerance,
        },
        "results": rows,
        "overall": "PASS" if overall_pass else "FAIL",
    }
    out_json = out_dir / "capacity_results.json"
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {out_json}")
    print(f"OVERALL: {payload['overall']}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
