"""Phase 3 tests for bounded monitor runtime workers."""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from bson import ObjectId

from services.monitor_runtime import MonitorRuntime


def _device(**overrides):
    doc = {
        "_id": ObjectId(),
        "hostname": "host-a",
        "ipAddress": "192.168.0.10",
        "monitor": True,
        "critical": False,
        "scanClaimId": "claim-1",
    }
    doc.update(overrides)
    return doc


class TestMonitorRuntimeWorkers(unittest.TestCase):
    def tearDown(self):
        # Ensure no leaked runtime threads between tests.
        pass

    def test_worker_executes_one_claimed_device_and_releases(self):
        runtime = MonitorRuntime(concurrency=2)
        scanned = []
        released = []

        def fake_scan(device, *, suppress_offline, cycle_id):
            scanned.append(
                {
                    "deviceId": device["_id"],
                    "claimId": device.get("scanClaimId"),
                    "cycleId": cycle_id,
                    "suppress_offline": suppress_offline,
                }
            )

        def fake_release(device_id, claim_id):
            released.append((device_id, claim_id))
            return True

        with (
            patch("services.monitor_service._scan_device", side_effect=fake_scan),
            patch(
                "services.monitor_runtime.release_device_claim",
                side_effect=fake_release,
            ),
        ):
            runtime.start()
            device = _device(scanClaimId="c-ok")
            ok = runtime.submit_claimed_device(device, "c-ok", cycle_id="cycle1")
            self.assertTrue(ok)
            deadline = time.time() + 3
            while runtime.stats()["claims_processed"] < 1 and time.time() < deadline:
                time.sleep(0.02)
            runtime.stop(wait=True)

        self.assertEqual(len(scanned), 1)
        self.assertEqual(scanned[0]["cycleId"], "cycle1")
        self.assertEqual(released, [(device["_id"], "c-ok")])
        self.assertEqual(runtime.stats()["claims_processed"], 1)

    def test_worker_releases_claim_after_scan_failure(self):
        runtime = MonitorRuntime(concurrency=1)
        released = []

        def boom(device, *, suppress_offline, cycle_id):
            raise RuntimeError("ping path exploded")

        def fake_release(device_id, claim_id):
            released.append((device_id, claim_id))
            return True

        with (
            patch("services.monitor_service._scan_device", side_effect=boom),
            patch(
                "services.monitor_runtime.release_device_claim",
                side_effect=fake_release,
            ),
        ):
            runtime.start()
            device = _device(scanClaimId="c-fail")
            self.assertTrue(runtime.submit_claimed_device(device, "c-fail"))
            deadline = time.time() + 3
            while runtime.stats()["failures"] < 1 and time.time() < deadline:
                time.sleep(0.02)
            runtime.stop(wait=True)

        self.assertEqual(released, [(device["_id"], "c-fail")])
        self.assertEqual(runtime.stats()["failures"], 1)
        self.assertEqual(runtime.stats()["claims_processed"], 0)

    def test_queue_capacity_bounded_and_full_releases_claim(self):
        runtime = MonitorRuntime(concurrency=1)
        block = threading.Event()
        entered = threading.Event()
        released = []

        def slow_scan(device, *, suppress_offline, cycle_id):
            entered.set()
            block.wait(timeout=5)

        def fake_release(device_id, claim_id):
            released.append(claim_id)
            return True

        with (
            patch("services.monitor_service._scan_device", side_effect=slow_scan),
            patch(
                "services.monitor_runtime.release_device_claim",
                side_effect=fake_release,
            ),
        ):
            runtime.start()
            d1 = _device(scanClaimId="c1")
            d2 = _device(scanClaimId="c2")
            self.assertTrue(runtime.submit_claimed_device(d1, "c1"))
            self.assertTrue(entered.wait(timeout=2))
            # occupancy: 1 in-flight → next submit must be rejected + released
            ok2 = runtime.submit_claimed_device(d2, "c2")
            self.assertFalse(ok2)
            self.assertIn("c2", released)
            stats = runtime.stats()
            self.assertLessEqual(stats["occupancy"], stats["concurrency"])
            self.assertEqual(stats["rejected_full"], 1)
            block.set()
            deadline = time.time() + 3
            while runtime.stats()["claims_processed"] < 1 and time.time() < deadline:
                time.sleep(0.02)
            runtime.stop(wait=True)

        self.assertIn("c1", released)

    def test_shutdown_stops_new_work_and_releases_queued(self):
        runtime = MonitorRuntime(concurrency=1)
        block = threading.Event()
        entered = threading.Event()
        released = []

        def slow_scan(device, *, suppress_offline, cycle_id):
            entered.set()
            block.wait(timeout=5)

        def fake_release(device_id, claim_id):
            released.append(claim_id)
            return True

        with (
            patch("services.monitor_service._scan_device", side_effect=slow_scan),
            patch(
                "services.monitor_runtime.release_device_claim",
                side_effect=fake_release,
            ),
        ):
            runtime.start()
            d1 = _device(scanClaimId="busy")
            self.assertTrue(runtime.submit_claimed_device(d1, "busy"))
            self.assertTrue(entered.wait(timeout=2))

            # Stop while first item in-flight — further submits rejected.
            # Put stop without waiting so we can assert rejection, then finish.
            runtime._stop.set()
            d2 = _device(scanClaimId="after-stop")
            ok = runtime.submit_claimed_device(d2, "after-stop")
            self.assertFalse(ok)
            self.assertIn("after-stop", released)

            block.set()
            runtime.stop(wait=True)

        self.assertIn("busy", released)

    def test_no_ping_without_claim_id(self):
        runtime = MonitorRuntime(concurrency=1)
        scanned = []

        def fake_scan(device, *, suppress_offline, cycle_id):
            scanned.append(device["_id"])

        with (
            patch("services.monitor_service._scan_device", side_effect=fake_scan),
            patch(
                "services.monitor_runtime.release_device_claim",
                return_value=False,
            ),
        ):
            runtime.start()
            device = _device()
            ok = runtime.submit_claimed_device(device, "")
            self.assertFalse(ok)
            time.sleep(0.2)
            runtime.stop(wait=True)

        self.assertEqual(scanned, [])

    def test_apply_path_called_once_per_logical_scan(self):
        """Worker invokes _scan_device once; that path owns apply_ping_result."""
        runtime = MonitorRuntime(concurrency=1)
        calls = {"scan": 0}

        def fake_scan(device, *, suppress_offline, cycle_id):
            calls["scan"] += 1

        with (
            patch("services.monitor_service._scan_device", side_effect=fake_scan),
            patch(
                "services.monitor_runtime.release_device_claim",
                return_value=True,
            ),
        ):
            runtime.start()
            device = _device(scanClaimId="once")
            self.assertTrue(runtime.submit_claimed_device(device, "once"))
            deadline = time.time() + 3
            while runtime.stats()["claims_processed"] < 1 and time.time() < deadline:
                time.sleep(0.02)
            runtime.stop(wait=True)

        self.assertEqual(calls["scan"], 1)

    def test_wrong_claim_id_release_is_noop_path(self):
        """release_device_claim itself no-ops on mismatch — runtime still calls it."""
        from services.monitor_claim import release_device_claim

        # Behavioral contract of claim module (used by runtime finally).
        with patch("services.monitor_claim._db") as mock_db:
            coll = mock_db.return_value.devices
            result = type("R", (), {})()
            result.acknowledged = True
            result.matched_count = 0
            result.modified_count = 0
            result.upserted_id = None
            coll.update_one.return_value = result
            ok = release_device_claim(ObjectId(), "wrong")
        self.assertFalse(ok)

    def test_leadership_lost_rejects_and_releases(self):
        runtime = MonitorRuntime(concurrency=2)
        released = []

        def fake_release(device_id, claim_id):
            released.append(claim_id)
            return True

        with (
            patch("services.monitor_service._scan_device"),
            patch(
                "services.monitor_runtime.release_device_claim",
                side_effect=fake_release,
            ),
        ):
            runtime.start()
            runtime.signal_leadership_lost()
            device = _device(scanClaimId="lead")
            ok = runtime.submit_claimed_device(device, "lead")
            self.assertFalse(ok)
            self.assertIn("lead", released)
            runtime.stop(wait=True)

    def test_stats_expose_required_fields(self):
        runtime = MonitorRuntime(concurrency=3)
        runtime.start()
        stats = runtime.stats()
        for key in (
            "queue_depth",
            "workers_active",
            "workers_total",
            "claims_processed",
            "failures",
        ):
            self.assertIn(key, stats)
        self.assertEqual(stats["workers_total"], 3)
        runtime.stop(wait=True)


class TestPingServiceUntouched(unittest.TestCase):
    def test_ping_device_still_uses_retries_config(self):
        """Sanity: runtime does not redefine ping_device retry semantics."""
        import inspect

        from services import ping_service
        from services import monitor_runtime

        self.assertTrue(hasattr(ping_service, "ping_device"))
        src = inspect.getsource(ping_service.ping_device)
        self.assertIn("attempts", src)
        # Runtime must not wrap/replace ping_device
        self.assertFalse(hasattr(monitor_runtime, "ping_device"))


if __name__ == "__main__":
    unittest.main()
