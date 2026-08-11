"""
Phase 9: comprehensive regression + concurrency audit for dispatch redesign.

Uses mocks/fakes — no production load testing, no destructive Mongo writes.
Architectural behavior is asserted; code is only changed if a test finds a bug.
"""

from __future__ import annotations

import inspect
import threading
import time
import unittest
from copy import deepcopy
from datetime import timedelta
from unittest.mock import MagicMock, patch

from bson import ObjectId
from pymongo import ReturnDocument

from utils.utc import ensure_utc, utc_now


# ── Minimal multi-doc fake collection (claim concurrency / restart sims) ─────


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


class _MultiDeviceCollection:
    def __init__(self, docs: list[dict]):
        self._lock = threading.Lock()
        self.docs = {d["_id"]: deepcopy(d) for d in docs}

    def find_one_and_update(self, filt, update, return_document=None):
        with self._lock:
            device_id = filt.get("_id")
            doc = self.docs.get(device_id)
            if doc is None or not _matches(doc, filt):
                return None
            for key, value in (update.get("$set") or {}).items():
                doc[key] = value
            for key in update.get("$unset") or {}:
                doc.pop(key, None)
            return deepcopy(doc)

    def update_one(self, filt, update):
        with self._lock:
            device_id = filt.get("_id")
            doc = self.docs.get(device_id)
            matched = 1 if doc is not None and _matches(doc, filt) else 0
            if matched:
                for key, value in (update.get("$set") or {}).items():
                    doc[key] = value
                for key in update.get("$unset") or {}:
                    doc.pop(key, None)
            result = MagicMock()
            result.acknowledged = True
            result.matched_count = matched
            result.modified_count = matched
            result.upserted_id = None
            return result

    def find(self, filt=None, projection=None):
        with self._lock:
            matched = [
                deepcopy(d) for d in self.docs.values() if _matches(d, filt or {})
            ]

        class _Cursor:
            def __init__(self, rows):
                self._rows = rows

            def sort(self, *_a, **_k):
                return self

            def limit(self, n):
                self._rows = self._rows[:n]
                return self

            def __iter__(self):
                return iter(self._rows)

            def __list__(self):
                return list(self._rows)

        return _Cursor(matched)


class _FakeDb:
    def __init__(self, devices: _MultiDeviceCollection):
        self.devices = devices


def _device(**overrides):
    doc = {
        "_id": ObjectId(),
        "hostname": "host",
        "ipAddress": "10.0.0.1",
        "monitor": True,
        "critical": False,
        "status": "Online",
    }
    doc.update(overrides)
    return doc


def _wait_until(pred, timeout=4.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


CFG = {
    "interval": 30,
    "timeout_ms": 1000,
    "retries": 3,
    "failure_confirmation_scans": 2,
}


class TestPingInvariants(unittest.TestCase):
    def test_ping_device_signature_and_retry_path_untouched(self):
        import services.ping_service as ping

        src = inspect.getsource(ping.ping_device)
        self.assertIn("timeout_ms", src)
        self.assertIn("retries", src)
        # Still loops attempts inside one call (one logical scan).
        self.assertTrue("attempt" in src.lower() or "range(" in src)
        sig = inspect.signature(ping.ping_device)
        self.assertIn("timeout_ms", sig.parameters)
        self.assertIn("retries", sig.parameters)
        self.assertIn("device", sig.parameters)

    def test_one_logical_scan_per_worker_invocation(self):
        from services.monitor_runtime import MonitorRuntime

        scans = []

        def fake_scan(
            device, claim_id="", suppress_offline=False, cycle_id="", timing_out=None
        ):
            scans.append(claim_id)
            return "scanned"

        runtime = MonitorRuntime(concurrency=2)
        with (
            patch(
                "services.monitor_service.scan_claimed_device",
                side_effect=fake_scan,
            ),
            patch(
                "services.monitor_runtime.release_device_claim",
                return_value=True,
            ),
        ):
            runtime.start()
            d = _device(scanClaimId="c1")
            self.assertTrue(runtime.submit_claimed_device(d, "c1"))
            self.assertTrue(_wait_until(lambda: len(scans) == 1))
            runtime.stop(wait=True)
        self.assertEqual(scans, ["c1"])


class TestHistoryAndCasSmoke(unittest.TestCase):
    def test_attempt_id_idempotent_path_still_present(self):
        from services import history_service as hs
        from services import monitor_service as ms

        apply_src = inspect.getsource(ms.apply_ping_result)
        self.assertIn("attemptId", apply_src)
        hist_src = inspect.getsource(hs)
        self.assertIn("DuplicateKeyError", hist_src)
        self.assertIn("attemptId", hist_src)

    def test_freshness_cas_filter_present(self):
        from services import monitor_service as ms

        src = inspect.getsource(ms)
        self.assertIn("lastPingStartedAt", src)
        self.assertIn("$lte", src)


class TestSchedulerInvariants(unittest.TestCase):
    def test_legacy_and_dispatch_job_flags(self):
        import scheduler as sched

        mock_sched = MagicMock()
        mock_sched.running = False

        with (
            patch.object(sched, "scheduler", mock_sched),
            patch.object(sched, "get_monitor_runtime_mode", return_value="legacy"),
            patch.object(sched, "get_settings", return_value={"pingInterval": 30}),
            patch.object(sched, "_start_nmap_job"),
            patch.object(sched, "_start_interface_job"),
            patch.object(sched, "_start_interface_stats_job"),
            patch.object(sched, "_start_recovery_job"),
            patch.object(sched, "_start_retention_job"),
            patch.object(sched, "_register_isp_monitor_job"),
        ):
            sched.start_scheduler()

        kwargs = mock_sched.add_job.call_args_list[0].kwargs
        self.assertIs(kwargs["func"], sched.monitor_all_devices)
        self.assertEqual(kwargs["seconds"], 30)
        self.assertEqual(kwargs["max_instances"], 1)
        self.assertTrue(kwargs["coalesce"])

        mock_sched.reset_mock()
        mock_sched.running = False
        with (
            patch.object(sched, "scheduler", mock_sched),
            patch.object(sched, "get_monitor_runtime_mode", return_value="dispatch"),
            patch.object(sched, "get_settings", return_value={"pingInterval": 30}),
            patch.object(
                sched, "get_monitor_dispatcher_interval_seconds", return_value=5
            ),
            patch("services.monitor_runtime.start_monitor_runtime"),
            patch.object(sched, "_start_nmap_job"),
            patch.object(sched, "_start_interface_job"),
            patch.object(sched, "_start_interface_stats_job"),
            patch.object(sched, "_start_recovery_job"),
            patch.object(sched, "_start_retention_job"),
            patch.object(sched, "_register_isp_monitor_job"),
        ):
            sched.start_scheduler()

        kwargs = mock_sched.add_job.call_args_list[0].kwargs
        self.assertIs(kwargs["func"], sched.dispatch_monitor_due_devices)
        self.assertEqual(kwargs["seconds"], 5)
        self.assertEqual(kwargs["max_instances"], 1)
        self.assertTrue(kwargs["coalesce"])

    def test_only_leader_dispatches(self):
        from services.monitor_dispatch import dispatch_monitor_due_devices

        with (
            patch(
                "services.monitor_dispatch.require_scheduler_leadership",
                return_value=False,
            ),
            patch(
                "services.monitor_dispatch.signal_monitor_runtime_leadership_lost"
            ),
            patch("services.monitor_dispatch.claim_device") as claim,
        ):
            result = dispatch_monitor_due_devices()
        self.assertTrue(result["skipped"])
        claim.assert_not_called()


class TestClaimInvariants(unittest.TestCase):
    def test_active_claim_blocks_second_worker(self):
        from services.monitor_claim import claim_device

        now = utc_now()
        doc = _device(
            nextCheckAt=now - timedelta(seconds=1),
            scanClaimId="active",
            scanClaimExpiresAt=now + timedelta(seconds=30),
        )
        coll = _MultiDeviceCollection([doc])
        with (
            patch("services.monitor_claim._db", return_value=_FakeDb(coll)),
            patch("services.monitor_claim.get_ping_config", return_value=CFG),
            patch(
                "services.monitor_claim.with_mongo_retry",
                side_effect=lambda fn, **_k: fn(),
            ),
        ):
            second = claim_device(doc["_id"], device=doc, now=now)
        self.assertIsNone(second)

    def test_expired_claim_reclaimable(self):
        from services.monitor_claim import claim_device

        now = utc_now()
        doc = _device(
            nextCheckAt=now - timedelta(seconds=1),
            scanClaimId="old",
            scanClaimExpiresAt=now - timedelta(seconds=1),
        )
        coll = _MultiDeviceCollection([doc])
        with (
            patch("services.monitor_claim._db", return_value=_FakeDb(coll)),
            patch("services.monitor_claim.get_ping_config", return_value=CFG),
            patch(
                "services.monitor_claim.with_mongo_retry",
                side_effect=lambda fn, **_k: fn(),
            ),
        ):
            claimed = claim_device(doc["_id"], device=doc, now=now)
        self.assertIsNotNone(claimed)
        self.assertNotEqual(claimed["scanClaimId"], "old")

    def test_wrong_claim_id_cannot_release(self):
        from services.monitor_claim import release_device_claim

        now = utc_now()
        doc = _device(
            scanClaimId="newer",
            scanClaimedAt=now,
            scanClaimExpiresAt=now + timedelta(seconds=20),
        )
        coll = _MultiDeviceCollection([doc])
        with (
            patch("services.monitor_claim._db", return_value=_FakeDb(coll)),
            patch(
                "services.monitor_claim.with_mongo_retry",
                side_effect=lambda fn, **_k: fn(),
            ),
            patch("services.monitor_claim.assert_update_acknowledged"),
        ):
            ok = release_device_claim(doc["_id"], "stale-old")
        self.assertFalse(ok)
        self.assertEqual(coll.docs[doc["_id"]]["scanClaimId"], "newer")

    def test_monitor_false_and_missing_next_check(self):
        from services.monitor_claim import build_due_unclaimed_filter, claim_device

        now = utc_now()
        filt = build_due_unclaimed_filter(now)
        self.assertEqual(filt["monitor"], True)

        off = _device(monitor=False, nextCheckAt=now - timedelta(seconds=1))
        due = _device()  # missing nextCheckAt
        coll = _MultiDeviceCollection([off, due])
        with (
            patch("services.monitor_claim._db", return_value=_FakeDb(coll)),
            patch("services.monitor_claim.get_ping_config", return_value=CFG),
            patch(
                "services.monitor_claim.with_mongo_retry",
                side_effect=lambda fn, **_k: fn(),
            ),
        ):
            self.assertIsNone(claim_device(off["_id"], device=off, now=now))
            self.assertIsNotNone(claim_device(due["_id"], device=due, now=now))

    def test_concurrent_claim_race_one_winner(self):
        from services.monitor_claim import claim_device

        now = utc_now()
        doc = _device(nextCheckAt=now - timedelta(seconds=1))
        coll = _MultiDeviceCollection([doc])
        results = []
        lock = threading.Lock()
        barrier = threading.Barrier(12)

        def worker():
            barrier.wait()
            claimed = claim_device(doc["_id"], device=doc, now=now)
            with lock:
                results.append(claimed)

        with (
            patch("services.monitor_claim._db", return_value=_FakeDb(coll)),
            patch("services.monitor_claim.get_ping_config", return_value=CFG),
            patch(
                "services.monitor_claim.with_mongo_retry",
                side_effect=lambda fn, **_k: fn(),
            ),
        ):
            threads = [threading.Thread(target=worker) for _ in range(12)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        winners = [r for r in results if r is not None]
        self.assertEqual(len(winners), 1)


class TestQueueAndShutdown(unittest.TestCase):
    def test_queue_bounded_no_duplicate_device_occupancy(self):
        from services.monitor_runtime import MonitorRuntime

        block = threading.Event()
        seen = []

        def slow_scan(
            device, claim_id="", suppress_offline=False, cycle_id="", timing_out=None
        ):
            seen.append(claim_id)
            block.wait(timeout=2)
            return "scanned"

        runtime = MonitorRuntime(concurrency=1)
        with (
            patch(
                "services.monitor_service.scan_claimed_device",
                side_effect=slow_scan,
            ),
            patch(
                "services.monitor_runtime.release_device_claim",
                return_value=True,
            ),
        ):
            runtime.start()
            d1 = _device(scanClaimId="a")
            d2 = _device(scanClaimId="b")
            self.assertTrue(runtime.submit_claimed_device(d1, "a"))
            self.assertTrue(_wait_until(lambda: runtime.stats()["workers_active"] >= 1))
            # Capacity full — second rejected (no unbounded growth).
            self.assertFalse(runtime.submit_claimed_device(d2, "b"))
            stats = runtime.stats()
            self.assertLessEqual(stats["occupancy"], 1)
            self.assertLessEqual(stats["queue_depth"], 1)
            block.set()
            runtime.stop(wait=True)
        self.assertEqual(seen, ["a"])

    def test_worker_exception_still_releases(self):
        from services.monitor_runtime import MonitorRuntime

        released = []

        def boom(
            device, claim_id="", suppress_offline=False, cycle_id="", timing_out=None
        ):
            raise RuntimeError("worker boom")

        runtime = MonitorRuntime(concurrency=1)
        with (
            patch(
                "services.monitor_service.scan_claimed_device",
                side_effect=boom,
            ),
            patch(
                "services.monitor_runtime.release_device_claim",
                side_effect=lambda did, cid: released.append(cid) or True,
            ),
        ):
            runtime.start()
            d = _device(scanClaimId="boom")
            self.assertTrue(runtime.submit_claimed_device(d, "boom"))
            self.assertTrue(_wait_until(lambda: "boom" in released))
            runtime.stop(wait=True)
        self.assertIn("boom", released)


class TestFailoverAndRestart(unittest.TestCase):
    def test_new_leader_cannot_claim_active_old_claim(self):
        from services.monitor_claim import claim_device

        now = utc_now()
        doc = _device(
            nextCheckAt=now - timedelta(seconds=5),
            scanClaimId="leader-a",
            scanClaimExpiresAt=now + timedelta(seconds=25),
        )
        coll = _MultiDeviceCollection([doc])
        with (
            patch("services.monitor_claim._db", return_value=_FakeDb(coll)),
            patch("services.monitor_claim.get_ping_config", return_value=CFG),
            patch(
                "services.monitor_claim.with_mongo_retry",
                side_effect=lambda fn, **_k: fn(),
            ),
        ):
            # Simulate new leader trying the same device.
            self.assertIsNone(claim_device(doc["_id"], device=doc, now=now))

    def test_process_restart_leaves_recoverable_expired_claims(self):
        from services.monitor_claim import claim_device

        now = utc_now()
        # Crash left claim fields; after TTL expiry new process can reclaim.
        doc = _device(
            nextCheckAt=now - timedelta(seconds=1),
            scanClaimId="crashed-proc",
            scanClaimExpiresAt=now - timedelta(seconds=2),
        )
        coll = _MultiDeviceCollection([doc])
        with (
            patch("services.monitor_claim._db", return_value=_FakeDb(coll)),
            patch("services.monitor_claim.get_ping_config", return_value=CFG),
            patch(
                "services.monitor_claim.with_mongo_retry",
                side_effect=lambda fn, **_k: fn(),
            ),
        ):
            claimed = claim_device(doc["_id"], device=doc, now=now)
        self.assertIsNotNone(claimed)
        self.assertNotEqual(claimed["scanClaimId"], "crashed-proc")

    def test_scheduler_restart_safe_stop_start(self):
        import scheduler as sched
        from services.monitor_runtime import MonitorRuntime

        mock_sched = MagicMock()
        mock_sched.running = True
        runtime = MonitorRuntime(concurrency=1)
        runtime.start()

        with (
            patch.object(sched, "scheduler", mock_sched),
            patch.object(sched, "release_scheduler_ownership"),
            patch(
                "services.monitor_runtime.get_monitor_runtime",
                return_value=runtime,
            ),
            patch(
                "services.monitor_runtime.stop_monitor_runtime",
                side_effect=lambda wait=True: runtime.stop(wait=wait),
            ),
            patch(
                "services.monitor_runtime.signal_monitor_runtime_leadership_lost",
                side_effect=runtime.signal_leadership_lost,
            ),
        ):
            sched.stop_scheduler()

        self.assertFalse(runtime.stats()["started"])
        mock_sched.shutdown.assert_called()


class TestSettingsRegression(unittest.TestCase):
    def test_interval_changes_do_not_retarget_dispatcher(self):
        import scheduler as sched

        mock_sched = MagicMock()
        mock_sched.running = True
        mock_job = MagicMock()
        mock_job.trigger.interval = timedelta(seconds=5)
        mock_sched.get_job.return_value = mock_job

        with (
            patch.object(sched, "scheduler", mock_sched),
            patch.object(sched, "get_monitor_runtime_mode", return_value="dispatch"),
            patch.object(
                sched, "get_monitor_dispatcher_interval_seconds", return_value=5
            ),
            patch.object(sched, "_register_isp_monitor_job"),
        ):
            for interval in (60, 30, 15):
                sched.reschedule_monitor_job(interval)

        for call in mock_sched.add_job.call_args_list:
            self.assertNotEqual(call.kwargs.get("id"), sched.JOB_ID)

    def test_no_duplicate_jobs_after_settings_churn(self):
        import scheduler as sched

        mock_sched = MagicMock()
        mock_sched.running = True
        with (
            patch.object(sched, "scheduler", mock_sched),
            patch.object(sched, "get_monitor_runtime_mode", return_value="legacy"),
            patch.object(sched, "_register_isp_monitor_job"),
        ):
            for interval in (30, 60, 30, 15):
                sched.reschedule_monitor_job(interval)

        device_jobs = [
            c
            for c in mock_sched.add_job.call_args_list
            if c.kwargs.get("id") == sched.JOB_ID
        ]
        self.assertEqual(len(device_jobs), 4)
        for call in device_jobs:
            self.assertTrue(call.kwargs.get("replace_existing"))

    def test_concurrency_validation_still_1_to_64(self):
        from services import settings_service as ss

        current = {"_id": "global", "pingInterval": 30, "pingConcurrency": 20}
        with (
            patch.object(ss, "ensure_settings"),
            patch.object(ss, "get_settings", return_value=current),
            patch("services.settings_service.db", MagicMock()),
        ):
            with self.assertRaises(ValueError):
                ss.update_settings({"pingConcurrency": 0})
            with self.assertRaises(ValueError):
                ss.update_settings({"pingConcurrency": 65})


class TestFleetSimulations(unittest.TestCase):
    def test_15_device_dispatch_claim_submit(self):
        from services.monitor_dispatch import dispatch_monitor_due_devices

        now = utc_now()
        devices = [
            _device(
                hostname=f"h{i}",
                ipAddress=f"10.0.0.{i}",
                nextCheckAt=now - timedelta(seconds=i + 1),
            )
            for i in range(15)
        ]
        claimed = []
        submitted = []

        def fake_claim(device_id, device=None, now=None):
            claim_id = f"c-{device_id}"
            claimed.append(device_id)
            return {**(device or {}), "_id": device_id, "scanClaimId": claim_id}

        def fake_submit(device, claim_id, **_kwargs):
            submitted.append(claim_id)
            return True

        guard = MagicMock()
        guard.ensure.return_value = True
        fake_db = MagicMock()
        fake_db.devices.find.return_value.sort.return_value.limit.return_value = devices

        with (
            patch(
                "services.monitor_dispatch.require_scheduler_leadership",
                return_value=True,
            ),
            patch(
                "services.monitor_dispatch.start_monitor_runtime",
                return_value=MagicMock(),
            ),
            patch(
                "services.monitor_dispatch.CycleLeadershipGuard",
                return_value=guard,
            ),
            patch(
                "services.monitor_dispatch.begin_cycle_connectivity_check",
                return_value=False,
            ),
            patch(
                "services.monitor_dispatch.free_worker_capacity",
                return_value=15,
            ),
            patch("services.monitor_dispatch._db", return_value=fake_db),
            patch("services.monitor_dispatch.claim_device", side_effect=fake_claim),
            patch(
                "services.monitor_dispatch.submit_claimed_device",
                side_effect=fake_submit,
            ),
            patch("services.monitor_dispatch._maybe_run_integrity_audit"),
            patch("services.monitor_dispatch._emit_heartbeat"),
        ):
            result = dispatch_monitor_due_devices()

        self.assertEqual(result["candidates"], 15)
        self.assertEqual(result["claimed"], 15)
        self.assertEqual(result["submitted"], 15)
        self.assertEqual(len(set(claimed)), 15)
        fake_db.devices.find.return_value.sort.return_value.limit.assert_called_with(15)

    def test_100_device_runtime_processes_without_duplicate_scan(self):
        from services.monitor_runtime import MonitorRuntime

        scans = []
        lock = threading.Lock()

        def fake_scan(
            device, claim_id="", suppress_offline=False, cycle_id="", timing_out=None
        ):
            with lock:
                scans.append(str(device["_id"]))
            return "scanned"

        runtime = MonitorRuntime(concurrency=20)
        devices = [_device(scanClaimId=f"c{i}", hostname=f"h{i}") for i in range(100)]
        with (
            patch(
                "services.monitor_service.scan_claimed_device",
                side_effect=fake_scan,
            ),
            patch(
                "services.monitor_runtime.release_device_claim",
                return_value=True,
            ),
        ):
            runtime.start()
            accepted = 0
            # Feed in waves respecting capacity.
            idx = 0
            while idx < 100:
                while (
                    runtime.stats()["occupancy"] < runtime.concurrency and idx < 100
                ):
                    d = devices[idx]
                    if runtime.submit_claimed_device(d, d["scanClaimId"]):
                        accepted += 1
                    idx += 1
                time.sleep(0.01)
            self.assertTrue(
                _wait_until(
                    lambda: runtime.stats()["claims_processed"] + runtime.stats()["failures"]
                    >= accepted,
                    timeout=8,
                )
            )
            runtime.stop(wait=True)

        self.assertEqual(accepted, 100)
        self.assertEqual(len(scans), 100)
        self.assertEqual(len(set(scans)), 100)

    def test_100_device_claim_race_across_devices(self):
        from services.monitor_claim import claim_device

        now = utc_now()
        docs = [
            _device(
                hostname=f"h{i}",
                ipAddress=f"10.1.0.{i % 250}",
                nextCheckAt=now - timedelta(seconds=1),
            )
            for i in range(100)
        ]
        coll = _MultiDeviceCollection(docs)
        wins = []
        lock = threading.Lock()

        def claim_one(doc):
            claimed = claim_device(doc["_id"], device=doc, now=now)
            if claimed is not None:
                with lock:
                    wins.append(doc["_id"])

        with (
            patch("services.monitor_claim._db", return_value=_FakeDb(coll)),
            patch("services.monitor_claim.get_ping_config", return_value=CFG),
            patch(
                "services.monitor_claim.with_mongo_retry",
                side_effect=lambda fn, **_k: fn(),
            ),
        ):
            threads = [
                threading.Thread(target=claim_one, args=(doc,)) for doc in docs
            ]
            # Also double-claim pressure on first 10 devices.
            for doc in docs[:10]:
                threads.append(threading.Thread(target=claim_one, args=(doc,)))
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        # Each device at most once.
        self.assertEqual(len(wins), len(set(wins)))
        self.assertEqual(len(wins), 100)


if __name__ == "__main__":
    unittest.main()
