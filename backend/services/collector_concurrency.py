"""
Global bounded SSH session slots for collector workloads.

Mitigation and recovery SSH sessions use ``slot_kind="priority"`` and do not
compete with collector semaphores so safety/mitigation stay responsive.

Ping monitoring (ICMP) is unaffected.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator

from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("collector_concurrency")

DEFAULT_COLLECTOR_SLOTS = 10
DEFAULT_SLOT_WAIT_SECONDS = 30.0


def _collector_limit() -> int:
    raw = (os.getenv("MAX_GLOBAL_SSH_SESSIONS") or "").strip()
    if not raw:
        return DEFAULT_COLLECTOR_SLOTS
    try:
        return max(1, min(int(raw), 64))
    except ValueError:
        return DEFAULT_COLLECTOR_SLOTS


def _slot_wait_seconds() -> float:
    raw = (os.getenv("SSH_SLOT_WAIT_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_SLOT_WAIT_SECONDS
    try:
        return max(1.0, float(raw))
    except ValueError:
        return DEFAULT_SLOT_WAIT_SECONDS


_collector_semaphore = threading.BoundedSemaphore(value=_collector_limit())
_stats_lock = threading.Lock()
_active_collector = 0
_active_priority = 0


def collector_slot_stats() -> dict[str, int]:
    with _stats_lock:
        return {
            "collectorSlotsLimit": _collector_limit(),
            "collectorSlotsActive": _active_collector,
            "prioritySshActive": _active_priority,
        }


@contextmanager
def ssh_session_slot(*, kind: str = "collector", label: str = "") -> Iterator[None]:
    """
    Acquire a global SSH slot.

    kind:
      collector — stats, discovery, MAC/ARP, diagnostics (bounded)
      priority  — mitigation / recovery (not bounded; tracked only)
    """
    global _active_collector, _active_priority
    is_priority = kind == "priority"
    acquired = False
    if not is_priority:
        acquired = _collector_semaphore.acquire(timeout=_slot_wait_seconds())
        if not acquired:
            raise TimeoutError(
                f"SSH collector slot unavailable after {_slot_wait_seconds():.0f}s"
                + (f" ({label})" if label else "")
            )
    try:
        with _stats_lock:
            if is_priority:
                _active_priority += 1
            else:
                _active_collector += 1
        yield
    finally:
        with _stats_lock:
            if is_priority:
                _active_priority = max(0, _active_priority - 1)
            else:
                _active_collector = max(0, _active_collector - 1)
        if acquired:
            _collector_semaphore.release()
