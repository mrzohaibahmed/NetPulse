#!/usr/bin/env python3
"""
TEMPORARY READ-ONLY AUDIT SCRIPT — do not use to mutate production.
Labels: AUDIT_ONLY, READ_ONLY
No updates, deletes, claim releases, lock deletes, or settings changes.
"""
from __future__ import annotations

import json
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure backend root is importable
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")

from config.database import db  # noqa: E402
from services.settings_service import (  # noqa: E402
    get_monitor_dispatcher_interval_seconds,
    get_monitor_ping_concurrency,
    get_monitor_runtime_mode,
    get_ping_config,
    get_settings,
)
from services.scheduler_ownership import (  # noqa: E402
    get_lock_ttl_seconds,
    ownership_status,
)
from services.monitor_claim import compute_claim_ttl_seconds  # noqa: E402


def utc_now():
    return datetime.now(timezone.utc)


def ensure_utc(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def pct(sorted_vals, p):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def classify_avg(avg_s, interval):
    if avg_s is None:
        return "no_recent"
    excellent = interval + 5
    acceptable = interval + 15
    delayed = interval + 30
    if avg_s <= excellent:
        return "excellent"
    if avg_s <= acceptable:
        return "acceptable"
    if avg_s <= delayed:
        return "delayed"
    return "severe"


def main():
    now = utc_now()
    report: dict = {"auditAt": now.isoformat(), "readOnly": True}

    # --- 1. Runtime config ---
    settings = get_settings()
    ping_cfg = get_ping_config()
    mode = get_monitor_runtime_mode()
    concurrency = get_monitor_ping_concurrency()
    dispatcher = get_monitor_dispatcher_interval_seconds()
    lock_ttl = get_lock_ttl_seconds()
    claim_ttl = compute_claim_ttl_seconds()

    report["runtime"] = {
        "MONITOR_RUNTIME_MODE": mode,
        "MONITOR_RUNTIME_MODE_env_raw": os.getenv("MONITOR_RUNTIME_MODE"),
        "pingInterval": ping_cfg["interval"],
        "pingConcurrency": concurrency,
        "pingTimeoutMs": ping_cfg["timeout_ms"],
        "pingRetries": ping_cfg["retries"],
        "pingFailureConfirmationScans": ping_cfg["failure_confirmation_scans"],
        "MONITOR_DISPATCHER_INTERVAL_SECONDS": dispatcher,
        "SCHEDULER_LOCK_TTL_SECONDS": lock_ttl,
        "claimTTL_seconds_formula": claim_ttl,
        "mongo_settings_snapshot": {
            "pingInterval": settings.get("pingInterval"),
            "pingTimeoutMs": settings.get("pingTimeoutMs"),
            "pingRetries": settings.get("pingRetries"),
            "pingConcurrency": settings.get("pingConcurrency"),
            "pingFailureConfirmationScans": settings.get(
                "pingFailureConfirmationScans"
            ),
            "updatedAt": str(settings.get("updatedAt")),
        },
        "env_seeds": {
            "SCAN_INTERVAL": os.getenv("SCAN_INTERVAL"),
            "PING_TIMEOUT_MS": os.getenv("PING_TIMEOUT_MS"),
            "PING_RETRIES": os.getenv("PING_RETRIES"),
            "MONITOR_PING_CONCURRENCY": os.getenv("MONITOR_PING_CONCURRENCY"),
            "MONITOR_DISPATCHER_INTERVAL_SECONDS": os.getenv(
                "MONITOR_DISPATCHER_INTERVAL_SECONDS"
            ),
            "SCHEDULER_LOCK_TTL_SECONDS": os.getenv("SCHEDULER_LOCK_TTL_SECONDS"),
        },
        "worker_count_legacy_note": "legacy uses ThreadPoolExecutor per batch = pingConcurrency",
        "queue_capacity_dispatch_only": "N/A in legacy",
    }

    # --- 2. Leadership ---
    own = ownership_status()
    lock_doc = db.scheduler_locks.find_one({"_id": "monitor_scheduler"})
    report["leadership"] = {
        "ownership_status": {
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in own.items()
        },
        "lock_doc": {
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in (lock_doc or {}).items()
        },
    }

    # --- 3. Device inventory ---
    devices = list(
        db.devices.find(
            {"monitor": True},
            {
                "hostname": 1,
                "ip": 1,
                "status": 1,
                "monitor": 1,
                "pingInterval": 1,
                "pingTimeoutMs": 1,
                "pingRetries": 1,
                "lastCheckedAt": 1,
                "lastPingStartedAt": 1,
                "lastSeen": 1,
                "nextCheckAt": 1,
                "scanClaimId": 1,
                "scanClaimedAt": 1,
                "scanClaimExpiresAt": 1,
                "consecutiveFailures": 1,
            },
        )
    )
    status_counts = Counter((d.get("status") or "Unknown") for d in devices)
    interval = int(ping_cfg["interval"])

    missing_next = sum(1 for d in devices if d.get("nextCheckAt") is None)
    missing_lps = sum(1 for d in devices if d.get("lastPingStartedAt") is None)
    missing_lc = sum(1 for d in devices if d.get("lastCheckedAt") is None)

    active_claims = 0
    expired_claims = 0
    claim_present = 0
    stale_next = 0
    overdue_next = 0
    due_lags = []

    for d in devices:
        nca = ensure_utc(d.get("nextCheckAt"))
        if nca is not None:
            if nca < now - timedelta(seconds=interval * 2):
                stale_next += 1
            if nca < now:
                overdue_next += 1
                due_lags.append((now - nca).total_seconds())

        cid = d.get("scanClaimId")
        exp = ensure_utc(d.get("scanClaimExpiresAt"))
        if cid:
            claim_present += 1
            if exp and exp > now:
                active_claims += 1
            else:
                expired_claims += 1

    due_lags_sorted = sorted(due_lags)
    report["fleet"] = {
        "total_monitored": len(devices),
        "status_counts": dict(status_counts),
        "missing_nextCheckAt": missing_next,
        "stale_nextCheckAt_gt_2x_interval": stale_next,
        "overdue_nextCheckAt": overdue_next,
        "due_lag_p50": pct(due_lags_sorted, 50),
        "due_lag_p95": pct(due_lags_sorted, 95),
        "due_lag_p99": pct(due_lags_sorted, 99),
        "due_lag_max": due_lags_sorted[-1] if due_lags_sorted else None,
        "active_claims_non_expired": active_claims,
        "expired_claims_still_set": expired_claims,
        "devices_with_scanClaimId": claim_present,
        "missing_lastPingStartedAt": missing_lps,
        "missing_lastCheckedAt": missing_lc,
    }

    # Per-device interval overrides
    ov = Counter()
    for d in devices:
        iv = d.get("pingInterval")
        if iv is None:
            ov[f"global:{interval}"] += 1
        else:
            ov[f"override:{int(iv)}"] += 1
    report["interval_overrides"] = dict(ov)

    # --- 4. Cadence from device lastPingStartedAt freshness + pingHistory ---
    # Use last ~2 hours of pingHistory for start-to-start intervals
    hist_since = now - timedelta(hours=2)
    # Aggregate pingHistory timestamps per device
    pipeline = [
        {"$match": {"timestamp": {"$gte": hist_since}}},
        {"$project": {"deviceId": 1, "timestamp": 1, "success": 1, "responseTime": 1}},
        {"$sort": {"deviceId": 1, "timestamp": 1}},
    ]
    # Prefer pingStartedAt if present
    sample = db.pingHistory.find_one({}, {"pingStartedAt": 1, "timestamp": 1, "startedAt": 1})
    report["pingHistory_sample_fields"] = list((sample or {}).keys())

    # Pull history in bulk (read-only)
    hist_cursor = db.pingHistory.find(
        {"timestamp": {"$gte": hist_since}},
        {
            "deviceId": 1,
            "timestamp": 1,
            "pingStartedAt": 1,
            "pingCompletedAt": 1,
            "success": 1,
            "responseTime": 1,
            "attemptId": 1,
            "status": 1,
        },
    ).sort([("deviceId", 1), ("timestamp", 1)])

    by_dev: dict = defaultdict(list)
    durations_ok = []
    durations_fail = []
    for h in hist_cursor:
        did = h.get("deviceId")
        started = ensure_utc(h.get("pingStartedAt") or h.get("timestamp"))
        completed = ensure_utc(h.get("pingCompletedAt"))
        if did is None or started is None:
            continue
        by_dev[str(did)].append(
            {
                "started": started,
                "completed": completed,
                "success": bool(h.get("success")),
                "rt": h.get("responseTime"),
                "attemptId": h.get("attemptId"),
            }
        )
        if completed and started:
            dur = (completed - started).total_seconds()
            if dur >= 0 and dur < 60:
                if h.get("success"):
                    durations_ok.append(dur)
                else:
                    durations_fail.append(dur)

    device_stats = []
    class_counts = Counter()
    all_avgs = []
    all_intervals_flat = []

    for d in devices:
        did = str(d["_id"])
        entries = by_dev.get(did, [])
        starts = [e["started"] for e in entries]
        gaps = []
        for i in range(1, len(starts)):
            g = (starts[i] - starts[i - 1]).total_seconds()
            if 0 < g < 3600:  # ignore huge outages for distribution
                gaps.append(g)
                all_intervals_flat.append(g)

        avg = statistics.mean(gaps) if gaps else None
        med = statistics.median(gaps) if gaps else None
        mx = max(gaps) if gaps else None
        mn = min(gaps) if gaps else None
        gaps_sorted = sorted(gaps)
        p95 = pct(gaps_sorted, 95) if gaps else None

        # missed intervals: gaps > 1.5 * interval
        missed = sum(1 for g in gaps if g > interval * 1.5) if gaps else None

        # freshness from device doc
        lps = ensure_utc(d.get("lastPingStartedAt"))
        age = (now - lps).total_seconds() if lps else None

        # If no history gaps, classify by age
        bucket = classify_avg(avg, interval)
        if avg is None:
            if age is None or age > interval * 3:
                bucket = "no_recent"
            elif age <= interval + 5:
                bucket = "excellent"
            elif age <= interval + 15:
                bucket = "acceptable"
            elif age <= interval + 30:
                bucket = "delayed"
            else:
                bucket = "severe"

        class_counts[bucket] += 1
        if avg is not None:
            all_avgs.append(avg)

        device_cfg = get_ping_config(d)
        device_stats.append(
            {
                "deviceId": did,
                "hostname": d.get("hostname"),
                "ip": d.get("ip"),
                "status": d.get("status"),
                "configuredInterval": device_cfg["interval"],
                "lastPingStartedAt": lps.isoformat() if lps else None,
                "lastCheckedAt": (
                    ensure_utc(d.get("lastCheckedAt")).isoformat()
                    if d.get("lastCheckedAt")
                    else None
                ),
                "lastSeen": (
                    ensure_utc(d.get("lastSeen")).isoformat()
                    if d.get("lastSeen")
                    else None
                ),
                "nextCheckAt": (
                    ensure_utc(d.get("nextCheckAt")).isoformat()
                    if d.get("nextCheckAt")
                    else None
                ),
                "scanClaimId": d.get("scanClaimId"),
                "scanClaimExpiresAt": (
                    ensure_utc(d.get("scanClaimExpiresAt")).isoformat()
                    if d.get("scanClaimExpiresAt")
                    else None
                ),
                "claimActive": bool(
                    d.get("scanClaimId")
                    and ensure_utc(d.get("scanClaimExpiresAt"))
                    and ensure_utc(d.get("scanClaimExpiresAt")) > now
                ),
                "pingAttempts_2h": len(entries),
                "gap_count": len(gaps),
                "min_interval": mn,
                "avg_interval": avg,
                "median_interval": med,
                "p95_interval": p95,
                "max_interval": mx,
                "missed_intervals": missed,
                "age_since_last_start_s": age,
                "bucket": bucket,
            }
        )

    # Worst 20 by avg interval (desc), then by age
    worst = sorted(
        device_stats,
        key=lambda x: (
            x["avg_interval"] is None,
            -(x["avg_interval"] or 0),
            -(x["age_since_last_start_s"] or 0),
        ),
    )[:20]

    all_avgs_sorted = sorted(all_avgs)
    flat_sorted = sorted(all_intervals_flat)

    report["cadence"] = {
        "window_hours": 2,
        "classification_relative_to_interval": interval,
        "thresholds": {
            "excellent_lte": interval + 5,
            "acceptable_lte": interval + 15,
            "delayed_lte": interval + 30,
            "severe_gt": interval + 30,
        },
        "bucket_counts": dict(class_counts),
        "device_avg_p50": pct(all_avgs_sorted, 50),
        "device_avg_p95": pct(all_avgs_sorted, 95),
        "device_avg_p99": pct(all_avgs_sorted, 99),
        "device_avg_max": all_avgs_sorted[-1] if all_avgs_sorted else None,
        "raw_gap_p50": pct(flat_sorted, 50),
        "raw_gap_p95": pct(flat_sorted, 95),
        "raw_gap_p99": pct(flat_sorted, 99),
        "raw_gap_max": flat_sorted[-1] if flat_sorted else None,
        "raw_gap_count": len(flat_sorted),
    }
    report["worst20"] = worst

    # Ping cost
    def summ(vals):
        if not vals:
            return None
        s = sorted(vals)
        return {
            "n": len(s),
            "avg": statistics.mean(s),
            "p50": pct(s, 50),
            "p95": pct(s, 95),
            "max": s[-1],
        }

    report["ping_cost"] = {
        "successful": summ(durations_ok),
        "failed": summ(durations_fail),
        "expected_timeout_x_retries": (ping_cfg["timeout_ms"] / 1000.0)
        * ping_cfg["retries"],
    }

    # Capacity estimate from measured fail/ok mix
    # Use current status distribution as mix proxy
    n = len(devices)
    online = status_counts.get("Online", 0)
    unreachableish = n - online
    ok_avg = statistics.mean(durations_ok) if durations_ok else 0.05
    fail_avg = (
        statistics.mean(durations_fail)
        if durations_fail
        else (ping_cfg["timeout_ms"] / 1000.0) * ping_cfg["retries"]
    )
    # blended service time if scanning all
    if n:
        blended = (online * ok_avg + unreachableish * fail_avg) / n
    else:
        blended = fail_avg
    batches = math.ceil(n / max(concurrency, 1)) if n else 0
    est_cycle = batches * blended  # optimistic if all similar; better: worst-batch
    # Better estimate: ceil(n/c) * measured batch-ish = use fail_avg as batch wall if mixed
    # Use observed: cycle wall ≈ batches * max(typical). Use fail_avg as conservative batch time.
    est_cycle_conservative = batches * max(fail_avg, ok_avg)

    report["capacity"] = {
        "required_starts_per_sec": n / interval if interval else None,
        "concurrency": concurrency,
        "blended_service_time_s": blended,
        "batches_per_full_wave": batches,
        "est_full_wave_seconds_blended_batch": batches * blended,
        "est_full_wave_seconds_conservative": est_cycle_conservative,
        "theoretical_capacity_starts_per_sec_if_fail_avg": concurrency / fail_avg
        if fail_avg
        else None,
        "theoretical_capacity_starts_per_sec_if_ok_avg": concurrency / ok_avg
        if ok_avg
        else None,
        "online": online,
        "non_online": unreachableish,
    }

    # --- Parse recent monitor.log for cycle pattern ---
    log_path = BACKEND / "logs" / "monitor.log"
    cycle_re = re.compile(
        r"^(?P<ts>\S+) \| INFO \| monitor \| Monitoring cycle (?P<kind>started|finished) \| cycleId=(?P<cid>\S+)(?: \| total=(?P<total>\d+) due=(?P<due>\d+) scanned=(?P<scanned>\d+) skipped=(?P<skipped>\d+) failed=(?P<failed>\d+) \| concurrency=(?P<conc>\d+) \| partitionSuppress=\S+ \| aborted=(?P<aborted>\S+) \| abortReason=(?P<reason>\S+))?"
    )
    start_detail = re.compile(
        r"Monitoring cycle started \| cycleId=(?P<cid>\S+) \| heartbeat_s=(?P<hb>\S+) \| "
        r"pingConcurrency=(?P<pc>\d+) \| timeoutMs=(?P<to>\d+) \| retries=(?P<rt>\d+)"
    )
    mode_re = re.compile(r"Device monitor job registered \| mode=(?P<mode>\S+)")
    sched_re = re.compile(r"Scheduler started \| .* runtime_mode=(?P<mode>\S+)")
    capacity_re = re.compile(r"Monitoring capacity risk \| cycleId=(?P<cid>\S+) \| due=(?P<due>\d+) \| batches=(?P<batches>\d+)")
    leader_re = re.compile(
        r"(leadership|Leadership|not leader|lost leadership|acquired|follower)",
        re.I,
    )

    cycles = []
    starts = {}
    modes_seen = []
    capacity_risks = []
    leadership_lines = []

    # Read last ~8MB of log for recent behavior (file is ~96MB)
    read_bytes = 8_000_000
    with open(log_path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - read_bytes))
        raw = f.read().decode("utf-8", errors="replace")

    for line in raw.splitlines():
        m = mode_re.search(line) or sched_re.search(line)
        if m:
            modes_seen.append((line[:32], m.group("mode")))
        if "Monitoring capacity risk" in line:
            cm = capacity_re.search(line)
            if cm:
                capacity_risks.append(cm.groupdict())
        if leader_re.search(line) and ("scheduler" in line.lower() or "leadership" in line.lower() or "leader" in line.lower()):
            if len(leadership_lines) < 50:
                leadership_lines.append(line[:300])

        if "Monitoring cycle started" in line and "ISP" not in line:
            sm = start_detail.search(line)
            ts_m = re.match(r"^(\S+)", line)
            cid = sm.group("cid") if sm else None
            if cid and ts_m:
                starts[cid] = {
                    "start_ts": ts_m.group(1),
                    "pingConcurrency": int(sm.group("pc")) if sm else None,
                    "timeoutMs": int(sm.group("to")) if sm else None,
                    "retries": int(sm.group("rt")) if sm else None,
                }
        if "Monitoring cycle finished" in line and "ISP" not in line:
            fm = re.search(
                r"^(?P<ts>\S+) \| INFO \| monitor \| Monitoring cycle finished \| cycleId=(?P<cid>\S+) \| total=(?P<total>\d+) due=(?P<due>\d+) scanned=(?P<scanned>\d+) skipped=(?P<skipped>\d+) failed=(?P<failed>\d+) \| concurrency=(?P<conc>\d+) .* aborted=(?P<aborted>\S+) \| abortReason=(?P<reason>\S+)",
                line,
            )
            if fm:
                cid = fm.group("cid")
                st = starts.get(cid, {})
                start_ts = ensure_utc(st.get("start_ts"))
                end_ts = ensure_utc(fm.group("ts"))
                dur = None
                if start_ts and end_ts:
                    dur = (end_ts - start_ts).total_seconds()
                cycles.append(
                    {
                        "cycleId": cid,
                        "start": st.get("start_ts"),
                        "end": fm.group("ts"),
                        "duration_s": dur,
                        "total": int(fm.group("total")),
                        "due": int(fm.group("due")),
                        "scanned": int(fm.group("scanned")),
                        "skipped": int(fm.group("skipped")),
                        "failed": int(fm.group("failed")),
                        "concurrency": int(fm.group("conc")),
                        "aborted": fm.group("aborted"),
                    }
                )

    # Cycle start-to-start gaps
    cycle_gaps = []
    parsed_starts = []
    for c in cycles:
        if c.get("start"):
            t = ensure_utc(c["start"])
            if t:
                parsed_starts.append(t)
    parsed_starts.sort()
    for i in range(1, len(parsed_starts)):
        cycle_gaps.append((parsed_starts[i] - parsed_starts[i - 1]).total_seconds())

    # Alternate tick pattern detection
    alternate = 0
    for i in range(1, len(cycles)):
        a, b = cycles[i - 1], cycles[i]
        if a["due"] > 0 and a["skipped"] == 0 and b["due"] == 0 and b["skipped"] > 0:
            alternate += 1
        elif b["due"] > 0 and b["skipped"] == 0 and a["due"] == 0 and a["skipped"] > 0:
            alternate += 1

    durs = [c["duration_s"] for c in cycles if c.get("duration_s") is not None]
    report["scheduler_log"] = {
        "modes_seen_recent": modes_seen[-10:],
        "cycles_parsed": len(cycles),
        "recent_cycles": cycles[-15:],
        "cycle_duration_avg": statistics.mean(durs) if durs else None,
        "cycle_duration_p50": pct(sorted(durs), 50) if durs else None,
        "cycle_duration_p95": pct(sorted(durs), 95) if durs else None,
        "cycle_duration_max": max(durs) if durs else None,
        "cycle_start_gap_avg": statistics.mean(cycle_gaps) if cycle_gaps else None,
        "cycle_start_gap_p50": pct(sorted(cycle_gaps), 50) if cycle_gaps else None,
        "cycle_start_gap_min": min(cycle_gaps) if cycle_gaps else None,
        "cycle_start_gap_max": max(cycle_gaps) if cycle_gaps else None,
        "alternate_tick_pairs_observed": alternate,
        "capacity_risk_events": len(capacity_risks),
        "capacity_risk_samples": capacity_risks[-5:],
        "leadership_log_samples": leadership_lines[-20:],
        "dispatch_heartbeat_present": "Dispatch metrics heartbeat" in raw,
        "legacy_cycle_present": "Monitoring cycle started" in raw,
    }

    # Trace one worst device through last few pings
    if worst:
        w0 = worst[0]
        did = w0["deviceId"]
        hist = by_dev.get(did, [])[-10:]
        report["device_trace_worst"] = {
            "device": w0,
            "recent_pings": [
                {
                    "started": e["started"].isoformat(),
                    "completed": e["completed"].isoformat() if e["completed"] else None,
                    "duration_s": (
                        (e["completed"] - e["started"]).total_seconds()
                        if e["completed"]
                        else None
                    ),
                    "success": e["success"],
                    "attemptId": e["attemptId"],
                }
                for e in hist
            ],
        }

    # AttemptId uniqueness check: one history row per attempt
    multi_attempt = 0
    attempt_counts = Counter()
    for did, entries in list(by_dev.items())[:50]:
        for e in entries:
            if e["attemptId"]:
                attempt_counts[e["attemptId"]] += 1
    multi_attempt = sum(1 for v in attempt_counts.values() if v > 1)
    report["retry_behavior"] = {
        "configured_pingRetries": ping_cfg["retries"],
        "note": "pingRetries = total ICMP attempts per logical scan",
        "duplicate_attemptId_in_history_sample": multi_attempt,
        "attemptIds_sampled": len(attempt_counts),
    }

    out_path = BACKEND / "logs" / "_tmp_audit_200_report.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"WROTE {out_path}")
    # Print compact summary to stdout
    print(json.dumps({
        "runtime": report["runtime"],
        "leadership_isLeader": report["leadership"]["ownership_status"].get("isLeader"),
        "lock": report["leadership"]["lock_doc"],
        "fleet": report["fleet"],
        "cadence": report["cadence"],
        "ping_cost": report["ping_cost"],
        "capacity": report["capacity"],
        "interval_overrides": report["interval_overrides"],
        "scheduler_log_summary": {
            k: report["scheduler_log"][k]
            for k in (
                "cycles_parsed",
                "cycle_duration_avg",
                "cycle_duration_p50",
                "cycle_duration_max",
                "cycle_start_gap_avg",
                "cycle_start_gap_p50",
                "alternate_tick_pairs_observed",
                "capacity_risk_events",
                "dispatch_heartbeat_present",
                "legacy_cycle_present",
                "modes_seen_recent",
                "recent_cycles",
            )
        },
        "worst5": [
            {
                "ip": w["ip"],
                "status": w["status"],
                "avg": w["avg_interval"],
                "max": w["max_interval"],
                "missed": w["missed_intervals"],
                "bucket": w["bucket"],
                "attempts": w["pingAttempts_2h"],
            }
            for w in report["worst20"][:5]
        ],
        "device_trace_worst_ip": (report.get("device_trace_worst") or {}).get("device", {}).get("ip"),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
