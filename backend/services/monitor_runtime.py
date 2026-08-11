"""
Bounded continuous worker runtime for dispatch-mode device monitoring (Phase 3–5).

Producers (dispatcher) must:
  1. atomically claim a device via ``monitor_claim.claim_device``
  2. submit the claimed device here via ``submit_claimed_device``

Workers reuse ``monitor_service.scan_claimed_device`` → ``_scan_device_safe`` →
``_scan_device`` (``ping_device`` + ``apply_ping_result``) and always best-effort
release the claim afterward. ``nextCheckAt`` is owned by the claim step and is
never rewritten here.
"""

from __future__ import annotations

import queue
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from services.monitor_claim import release_device_claim
from services.monitor_metrics import (
    compute_due_lag_ms,
    compute_ping_duration_ms,
    compute_queue_wait_ms,
    compute_start_to_start_ms,
    format_metric_fields,
    get_dispatch_metrics,
    is_slo_max_alert,
    is_slo_miss,
)
from services.settings_service import get_monitor_ping_concurrency
from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("monitor_runtime")


@dataclass(frozen=True)
class _WorkItem:
    device: dict[str, Any]
    claim_id: str
    suppress_offline: bool
    cycle_id: str
    queued_at: datetime
    due_at: datetime | None = None
    claimed_at: datetime | None = None
    previous_ping_started_at: datetime | None = None
    next_check_at: datetime | None = None


class MonitorRuntime:
    """
    Dedicated ping worker pool with a bounded work queue.

    Capacity invariant: ``occupancy == queued + in_flight <= concurrency``.
    """

    def __init__(self, *, concurrency: int | None = None):
        workers = int(
            concurrency if concurrency is not None else get_monitor_ping_concurrency()
        )
        self._concurrency = max(1, min(workers, 64))
        self._queue: queue.Queue[_WorkItem | None] = queue.Queue(
            maxsize=self._concurrency
        )
        self._lock = threading.RLock()
        self._occupancy = 0
        self._in_flight = 0
        self._stop = threading.Event()
        self._leadership_lost = threading.Event()
        self._started = False
        self._executor: ThreadPoolExecutor | None = None
        self._claims_processed = 0
        self._failures = 0
        self._rejected_full = 0
        self._rejected_stopped = 0

    @property
    def concurrency(self) -> int:
        return self._concurrency

    def start(self) -> None:
        """Start continuous worker loops. Idempotent."""
        with self._lock:
            if self._started:
                return
            self._stop.clear()
            self._leadership_lost.clear()
            self._executor = ThreadPoolExecutor(
                max_workers=self._concurrency,
                thread_name_prefix="monitor-ping",
            )
            for _ in range(self._concurrency):
                self._executor.submit(self._worker_loop)
            self._started = True
        logger.info(
            "Monitor runtime started | workers=%s | queueMax=%s",
            self._concurrency,
            self._concurrency,
        )

    def stop(self, *, wait: bool = True) -> None:
        """
        Stop accepting work, release queued claims, shut down the executor.

        ``wait=True`` joins worker threads (for tests). ``wait=False`` returns
        immediately after signaling stop and draining the queue — in-flight
        workers may finish and release their own claims; the scheduler must not
        block indefinitely on ping workers.
        """
        with self._lock:
            already_stopped = not self._started
            self._stop.set()
            self._leadership_lost.set()
            executor = self._executor
            if already_stopped and executor is None:
                drained = self._drain_queue_release_claims()
                if drained:
                    logger.info(
                        "Monitor runtime stop (idle) released queued claims=%s",
                        drained,
                    )
                return

        drained = self._drain_queue_release_claims()

        # Wake blocked workers so they can exit the loop.
        for _ in range(self._concurrency):
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                break

        if executor is not None:
            # Always shut down the pool; never leave it accepting work.
            # wait=False avoids blocking the scheduler / atexit path.
            executor.shutdown(wait=wait, cancel_futures=False)

        with self._lock:
            self._executor = None
            self._started = False

        logger.info(
            "Monitor runtime stopped | wait=%s | drainedQueued=%s | "
            "processed=%s | failures=%s",
            wait,
            drained,
            self._claims_processed,
            self._failures,
        )

    def signal_leadership_lost(self) -> None:
        """
        Stop accepting new submissions and release queued claims.

        In-flight workers finish their current device and release *their own*
        claim id only (``release_device_claim`` matches ``{_id, scanClaimId}``).
        Does not modify the Mongo scheduler lease.
        """
        self._leadership_lost.set()
        released = self._drain_queue_release_claims()
        get_dispatch_metrics().incr_leadership_lost()
        logger.warning(
            "Monitor runtime leadership lost signal | releasedQueuedClaims=%s | "
            "inFlight=%s | workers_active=%s | queue_depth=%s",
            released,
            self._in_flight,
            self._in_flight,
            self._queue.qsize(),
        )

    def clear_leadership_lost(self) -> None:
        """Allow submissions again after a new leader starts this runtime."""
        self._leadership_lost.clear()

    def submit_claimed_device(
        self,
        device: dict[str, Any],
        claim_id: str,
        *,
        suppress_offline: bool = False,
        cycle_id: str | None = None,
        due_at=None,
        claimed_at=None,
        previous_ping_started_at=None,
        next_check_at=None,
    ) -> bool:
        """
        Enqueue a device that the caller already claimed.

        Returns True when accepted. On rejection the claim is released so it is
        not silently orphaned (TTL remains the fallback).
        """
        device_id = (device or {}).get("_id")
        if not claim_id:
            logger.error(
                "Submit rejected - missing claimId | deviceId=%s",
                device_id,
            )
            return False
        if not device_id:
            logger.error("Submit rejected - missing deviceId | claimId=%s", claim_id)
            self._best_effort_release(None, claim_id)
            return False

        with self._lock:
            if (
                not self._started
                or self._stop.is_set()
                or self._leadership_lost.is_set()
            ):
                self._rejected_stopped += 1
                reject_reason = "stopped_or_leadership_lost"
            elif self._occupancy >= self._concurrency:
                self._rejected_full += 1
                reject_reason = "queue_full"
            else:
                reject_reason = ""
                self._occupancy += 1

        if reject_reason:
            if reject_reason == "queue_full":
                get_dispatch_metrics().incr_queue_full_skips()
            logger.warning(
                "Submit rejected - releasing claim | deviceId=%s | claimId=%s | "
                "reason=%s | occupancy=%s | concurrency=%s",
                device_id,
                claim_id,
                reject_reason,
                self.stats()["occupancy"],
                self._concurrency,
            )
            self._best_effort_release(device_id, claim_id)
            return False

        item = _WorkItem(
            device=device,
            claim_id=claim_id,
            suppress_offline=bool(suppress_offline),
            cycle_id=cycle_id or uuid.uuid4().hex[:12],
            queued_at=utc_now(),
            due_at=due_at,
            claimed_at=claimed_at,
            previous_ping_started_at=previous_ping_started_at,
            next_check_at=next_check_at
            if next_check_at is not None
            else (device or {}).get("nextCheckAt"),
        )
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            with self._lock:
                self._occupancy = max(0, self._occupancy - 1)
                self._rejected_full += 1
            get_dispatch_metrics().incr_queue_full_skips()
            logger.warning(
                "Submit rejected - queue put full | deviceId=%s | claimId=%s",
                device_id,
                claim_id,
            )
            self._best_effort_release(device_id, claim_id)
            return False

        # Keep enqueue path quiet — heartbeat + per-scan completion carry metrics.
        logger.debug(
            "Claimed device queued | deviceId=%s | claimId=%s | dispatchId=%s | "
            "queueDepth=%s",
            device_id,
            claim_id,
            item.cycle_id,
            self._queue.qsize(),
        )
        return True

    def stats(self) -> dict[str, Any]:
        """Snapshot runtime counters for observability / tests."""
        with self._lock:
            return {
                "started": self._started,
                "stopping": self._stop.is_set(),
                "leadership_lost": self._leadership_lost.is_set(),
                "workers_total": self._concurrency,
                "workers_active": self._in_flight,
                "queue_depth": self._queue.qsize(),
                "occupancy": self._occupancy,
                "concurrency": self._concurrency,
                "claims_processed": self._claims_processed,
                "failures": self._failures,
                "rejected_full": self._rejected_full,
                "rejected_stopped": self._rejected_stopped,
            }

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if item is None:
                self._queue.task_done()
                break

            self._process_work_item(item)
            self._queue.task_done()

    def _process_work_item(self, item: _WorkItem) -> None:
        device = item.device
        claim_id = item.claim_id
        device_id = device.get("_id")
        hostname = device.get("hostname", "unknown")
        ip_address = device.get("ipAddress", "unknown")
        worker_started_at = utc_now()
        queue_wait_ms = compute_queue_wait_ms(worker_started_at, item.queued_at)

        with self._lock:
            self._in_flight += 1

        timing: dict[str, Any] = {}
        outcome = "failed"
        try:
            if not claim_id:
                logger.error(
                    "Worker refusing ping - missing claimId | deviceId=%s | "
                    "dispatchId=%s",
                    device_id,
                    item.cycle_id,
                )
                with self._lock:
                    self._failures += 1
                return

            # Import lazily to avoid circular imports at module load.
            from services.monitor_service import scan_claimed_device  # noqa: PLC0415

            outcome = scan_claimed_device(
                device,
                claim_id=claim_id,
                suppress_offline=item.suppress_offline,
                cycle_id=item.cycle_id,
                timing_out=timing,
            )
            with self._lock:
                if outcome == "scanned":
                    self._claims_processed += 1
                else:
                    self._failures += 1
        except Exception as error:  # noqa: BLE001
            # scan_claimed_device / _scan_device_safe already trap most errors;
            # this is a last-resort guard so claims are never stranded.
            with self._lock:
                self._failures += 1
            logger.exception(
                "Monitor runtime worker failure | dispatchId=%s | deviceId=%s | "
                "claimId=%s | hostname=%s | ip=%s | error=%s",
                item.cycle_id,
                device_id,
                claim_id,
                hostname,
                ip_address,
                error,
            )
        finally:
            self._emit_scan_observability(
                item,
                timing=timing,
                outcome=outcome,
                queue_wait_ms=queue_wait_ms,
            )
            # Always release after success, failure, stale apply, or partition
            # suppress — nextCheckAt was advanced at claim time and is left intact.
            self._best_effort_release(device_id, claim_id)
            with self._lock:
                self._in_flight = max(0, self._in_flight - 1)
                self._occupancy = max(0, self._occupancy - 1)

    def _emit_scan_observability(
        self,
        item: _WorkItem,
        *,
        timing: dict[str, Any],
        outcome: str,
        queue_wait_ms: int | None,
    ) -> None:
        """One structured completion line per claimed scan — not per idle loop."""
        ping_started = timing.get("pingStartedAt")
        ping_completed = timing.get("pingCompletedAt")
        start_to_start_ms = compute_start_to_start_ms(
            ping_started, item.previous_ping_started_at
        )
        due_lag_ms = compute_due_lag_ms(ping_started, item.due_at)
        ping_duration_ms = compute_ping_duration_ms(ping_completed, ping_started)

        get_dispatch_metrics().record_scan_timing(start_to_start_ms=start_to_start_ms)

        fields = format_metric_fields(
            dispatchId=item.cycle_id,
            claimId=item.claim_id,
            deviceId=item.device.get("_id"),
            nextCheckAt=item.next_check_at,
            claimedAt=item.claimed_at,
            pingStartedAt=ping_started,
            pingCompletedAt=ping_completed,
            queueWaitMs=queue_wait_ms,
            pingDurationMs=ping_duration_ms,
            dueLagMs=due_lag_ms,
            startToStartMs=start_to_start_ms,
            outcome=outcome,
            workers_active=self._in_flight,
            queue_depth=self._queue.qsize(),
        )

        if is_slo_max_alert(start_to_start_ms):
            logger.warning("Dispatch scan SLO max alert | %s", fields)
        elif is_slo_miss(start_to_start_ms):
            logger.warning("Dispatch scan SLO miss | %s", fields)
        else:
            logger.info("Dispatch scan completed | %s", fields)

    def _drain_queue_release_claims(self) -> int:
        """Remove queued work and release each claim. Returns release attempts."""
        released = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                self._queue.task_done()
                continue
            self._best_effort_release(item.device.get("_id"), item.claim_id)
            released += 1
            with self._lock:
                self._occupancy = max(0, self._occupancy - 1)
            self._queue.task_done()
        return released

    @staticmethod
    def _best_effort_release(device_id: Any, claim_id: str) -> None:
        if device_id is None or not claim_id:
            return
        try:
            release_device_claim(device_id, claim_id)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Best-effort claim release failed | deviceId=%s | claimId=%s | "
                "error=%s",
                device_id,
                claim_id,
                exc,
            )


# ── Module singleton ──────────────────────────────────────────────────────────

_runtime_lock = threading.Lock()
_runtime: MonitorRuntime | None = None


def get_monitor_runtime() -> MonitorRuntime | None:
    """Return the started runtime instance, or None if not started."""
    return _runtime


def start_monitor_runtime(*, concurrency: int | None = None) -> MonitorRuntime:
    """
    Create (if needed) and start the process-local monitor runtime.

    Idempotent while started: does **not** recreate the worker pool on every
    dispatcher tick (avoids worker leaks).
    """
    global _runtime
    with _runtime_lock:
        if _runtime is not None and _runtime.stats()["started"]:
            return _runtime
        if _runtime is not None:
            # Replace a stopped / half-stopped instance so we never reuse a
            # drained executor that still has lingering threads.
            try:
                _runtime.stop(wait=False)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Prior monitor runtime stop during restart failed | error=%s",
                    exc,
                )
        _runtime = MonitorRuntime(concurrency=concurrency)
        _runtime.start()
        return _runtime


def reconfigure_monitor_runtime_concurrency() -> dict[str, Any]:
    """
    Apply current ``pingConcurrency`` when safe — not on every scheduler tick.

    - No-op if runtime is absent / stopped, or value unchanged.
    - When occupancy is 0: stop + start with the new concurrency (no leak).
    - When work is in flight / queued: defer rebuild; occupancy cap unchanged
      until idle (avoids interrupting pings and orphaning pools).
    """
    global _runtime
    desired = max(1, min(int(get_monitor_ping_concurrency()), 64))
    with _runtime_lock:
        runtime = _runtime
        if runtime is None:
            return {"applied": False, "reason": "not_started", "desired": desired}
        snap = runtime.stats()
        if not snap.get("started"):
            return {"applied": False, "reason": "not_started", "desired": desired}
        current = int(snap.get("concurrency") or runtime.concurrency)
        if current == desired:
            return {
                "applied": False,
                "reason": "unchanged",
                "desired": desired,
                "concurrency": current,
            }
        occupancy = int(snap.get("occupancy") or 0)
        if occupancy > 0:
            logger.info(
                "Deferring concurrency reconfigure until idle | "
                "current=%s | desired=%s | occupancy=%s",
                current,
                desired,
                occupancy,
            )
            return {
                "applied": False,
                "reason": "busy",
                "desired": desired,
                "concurrency": current,
                "occupancy": occupancy,
            }

        try:
            runtime.stop(wait=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Concurrency reconfigure stop failed | error=%s",
                exc,
            )
        _runtime = MonitorRuntime(concurrency=desired)
        _runtime.start()
        logger.info(
            "Monitor runtime concurrency reconfigured | from=%s | to=%s",
            current,
            desired,
        )
        return {
            "applied": True,
            "reason": "rebuilt_idle",
            "desired": desired,
            "concurrency": desired,
            "previous": current,
        }


def stop_monitor_runtime(*, wait: bool = True) -> None:
    """
    Stop the process-local runtime if present.

    Scheduler shutdown must pass ``wait=False`` so the APScheduler / atexit
    thread is not blocked on in-flight ICMP work. Queued claims are released
    immediately; in-flight claims are released by workers or expire via TTL.
    """
    global _runtime
    with _runtime_lock:
        runtime = _runtime
        _runtime = None
    if runtime is not None:
        runtime.stop(wait=wait)


def submit_claimed_device(
    device: dict[str, Any],
    claim_id: str,
    *,
    suppress_offline: bool = False,
    cycle_id: str | None = None,
    due_at=None,
    claimed_at=None,
    previous_ping_started_at=None,
    next_check_at=None,
) -> bool:
    """Submit to the process-local runtime, or reject+release if not started."""
    runtime = get_monitor_runtime()
    if runtime is None or not runtime.stats()["started"]:
        logger.warning(
            "Submit rejected - runtime not started | deviceId=%s | claimId=%s",
            (device or {}).get("_id"),
            claim_id,
        )
        if claim_id and (device or {}).get("_id") is not None:
            try:
                release_device_claim(device["_id"], claim_id)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Release after runtime-missing reject failed | error=%s",
                    exc,
                )
        return False
    return runtime.submit_claimed_device(
        device,
        claim_id,
        suppress_offline=suppress_offline,
        cycle_id=cycle_id,
        due_at=due_at,
        claimed_at=claimed_at,
        previous_ping_started_at=previous_ping_started_at,
        next_check_at=next_check_at,
    )


def signal_monitor_runtime_leadership_lost() -> None:
    runtime = get_monitor_runtime()
    if runtime is not None:
        runtime.signal_leadership_lost()


def get_monitor_runtime_stats() -> dict[str, Any]:
    runtime = get_monitor_runtime()
    if runtime is None:
        return {
            "started": False,
            "workers_total": 0,
            "workers_active": 0,
            "queue_depth": 0,
            "occupancy": 0,
            "claims_processed": 0,
            "failures": 0,
        }
    return runtime.stats()
