"""Phase 6: leadership loss, shutdown, and claim recovery hardening."""

from __future__ import annotations

import threading
import time
import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

from bson import ObjectId

from services.monitor_runtime import (
    MonitorRuntime,
    get_monitor_runtime,
    start_monitor_runtime,
    stop_monitor_runtime,
)
from utils.utc import utc_now


def _device(**overrides):
    doc = {
        "_id": ObjectId(),
        "hostname": "host-a",
        "ipAddress": "10.0.0.1",
        "monitor": True,
        "scanClaimId": "c1",
    }
    doc.update(overrides)
    return doc


def _wait_until(pred, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


class TestClaimTtlIndependentOfInterval(unittest.TestCase):
    def test_ttl_not_tied_to_ping_interval(self):
        from services.monitor_claim import compute_claim_ttl_seconds

        with patch(
            "services.monitor_claim.get_ping_config",
            return_value={
                "interval": 300,
                "timeout_ms": 1000,
                "retries": 2,
                "failure_confirmation_scans": 2,
            },
        ):
            ttl = compute_claim_ttl_seconds()
        # worst ping = 2s; TTL = max(15, 12) = 15 — not 300
        self.assertEqual(ttl, 15.0)
        self.assertLess(ttl, 300)

    def test_ttl_exceeds_worst_ping(self):
        from services.monitor_claim import compute_claim_ttl_seconds

        with patch(
            "services.monitor_claim.get_ping_config",
            return_value={
                "interval": 30,
                "timeout_ms": 2000,
                "retries": 3,
                "failure_confirmation_scans": 2,
            },
        ):
            ttl = compute_claim_ttl_seconds()
        self.assertGreater(ttl, 6.0)  # 2*3
        self.assertEqual(ttl, 16.0)  # 6+10


class TestClaimActiveHelper(unittest.TestCase):
    def test_active_and_expired(self):
        from services.monitor_claim import is_claim_active

        now = utc_now()
        active = {
            "scanClaimId": "x",
            "scanClaimExpiresAt": now + timedelta(seconds=20),
        }
        expired = {
            "scanClaimId": "x",
            "scanClaimExpiresAt": now - timedelta(seconds=1),
        }
        self.assertTrue(is_claim_active(active, now=now))
        self.assertFalse(is_claim_active(expired, now=now))
        self.assertFalse(is_claim_active({}, now=now))


class TestLeadershipLossDispatch(unittest.TestCase):
    def test_leadership_lost_before_claim_stops_claiming(self):
        from services.monitor_dispatch import dispatch_monitor_due_devices

        now = utc_now()
        devices = [
            {
                "_id": ObjectId(),
                "hostname": "a",
                "ipAddress": "10.0.0.1",
                "monitor": True,
                "nextCheckAt": now - timedelta(seconds=1),
            }
        ]
        guard = MagicMock()
        guard.ensure.side_effect = [True, False]  # start ok, pre-claim fail
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
                return_value=1,
            ),
            patch("services.monitor_dispatch._db", return_value=fake_db),
            patch("services.monitor_dispatch.claim_device") as claim,
            patch(
                "services.monitor_dispatch.signal_monitor_runtime_leadership_lost"
            ) as signal,
            patch("services.monitor_dispatch._maybe_run_integrity_audit"),
        ):
            result = dispatch_monitor_due_devices()

        claim.assert_not_called()
        signal.assert_called()
        self.assertTrue(result["aborted"])

    def test_leadership_lost_after_claim_releases_without_submit(self):
        from services.monitor_dispatch import dispatch_monitor_due_devices

        now = utc_now()
        device_id = ObjectId()
        devices = [
            {
                "_id": device_id,
                "hostname": "a",
                "ipAddress": "10.0.0.1",
                "monitor": True,
                "nextCheckAt": now - timedelta(seconds=1),
            }
        ]
        guard = MagicMock()
        # start, pre-claim, post-claim
        guard.ensure.side_effect = [True, True, False]
        fake_db = MagicMock()
        fake_db.devices.find.return_value.sort.return_value.limit.return_value = devices
        claimed = {
            "_id": device_id,
            "scanClaimId": "claimed-1",
            "hostname": "a",
            "ipAddress": "10.0.0.1",
        }
        released = []

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
                return_value=1,
            ),
            patch("services.monitor_dispatch._db", return_value=fake_db),
            patch(
                "services.monitor_dispatch.claim_device",
                return_value=claimed,
            ),
            patch(
                "services.monitor_claim.release_device_claim",
                side_effect=lambda did, cid: released.append(cid) or True,
            ),
            patch(
                "services.monitor_dispatch.submit_claimed_device"
            ) as submit,
            patch(
                "services.monitor_dispatch.signal_monitor_runtime_leadership_lost"
            ) as signal,
            patch("services.monitor_dispatch._maybe_run_integrity_audit"),
        ):
            result = dispatch_monitor_due_devices()

        submit.assert_not_called()
        self.assertEqual(released, ["claimed-1"])
        signal.assert_called()
        self.assertEqual(result["reason"], "leadership_lost_after_claim")
        self.assertTrue(result["aborted"])


class TestLeadershipLossWhileRunning(unittest.TestCase):
    def test_leadership_lost_drains_queue_inflight_releases_own_claim(self):
        runtime = MonitorRuntime(concurrency=1)
        block = threading.Event()
        entered = threading.Event()
        released = []

        def slow_scan(device, *, claim_id, suppress_offline=False, cycle_id=None, **_kwargs):
            entered.set()
            block.wait(timeout=5)
            return "scanned"

        def fake_release(device_id, claim_id):
            released.append(claim_id)
            return True

        with (
            patch(
                "services.monitor_service.scan_claimed_device",
                side_effect=slow_scan,
            ),
            patch(
                "services.monitor_runtime.release_device_claim",
                side_effect=fake_release,
            ),
        ):
            runtime.start()
            d1 = _device(scanClaimId="inflight")
            d2 = _device(scanClaimId="queued")
            self.assertTrue(runtime.submit_claimed_device(d1, "inflight"))
            self.assertTrue(entered.wait(timeout=2))
            # Second may be rejected (capacity 1) or queued if first still only
            # occupancy-counted; with concurrency=1 occupancy is full → reject.
            runtime.submit_claimed_device(d2, "queued")

            runtime.signal_leadership_lost()
            # Queued (if any) released; in-flight still held until worker finishes.
            self.assertTrue(runtime.stats()["leadership_lost"])
            # New submits rejected
            d3 = _device(scanClaimId="after")
            self.assertFalse(runtime.submit_claimed_device(d3, "after"))
            self.assertIn("after", released)

            block.set()
            self.assertTrue(_wait_until(lambda: "inflight" in released))
            runtime.stop(wait=True)

        self.assertIn("inflight", released)


class TestShutdown(unittest.TestCase):
    def tearDown(self):
        stop_monitor_runtime(wait=False)

    def test_shutdown_releases_queued_claims_without_blocking(self):
        runtime = MonitorRuntime(concurrency=2)
        block = threading.Event()
        entered = threading.Event()
        released = []

        def slow_scan(device, *, claim_id, suppress_offline=False, cycle_id=None, **_kwargs):
            entered.set()
            block.wait(timeout=5)
            return "scanned"

        def fake_release(device_id, claim_id):
            released.append(claim_id)
            return True

        with (
            patch(
                "services.monitor_service.scan_claimed_device",
                side_effect=slow_scan,
            ),
            patch(
                "services.monitor_runtime.release_device_claim",
                side_effect=fake_release,
            ),
        ):
            runtime.start()
            d1 = _device(scanClaimId="busy")
            d2 = _device(scanClaimId="queued")
            self.assertTrue(runtime.submit_claimed_device(d1, "busy"))
            self.assertTrue(entered.wait(timeout=2))
            self.assertTrue(runtime.submit_claimed_device(d2, "queued"))

            started = time.time()
            runtime.stop(wait=False)
            elapsed = time.time() - started
            self.assertLess(elapsed, 1.0)
            self.assertFalse(runtime.stats()["started"])
            self.assertIn("queued", released)

            block.set()
            # In-flight worker should still release its own claim.
            self.assertTrue(_wait_until(lambda: "busy" in released, timeout=3))

    def test_stop_monitor_runtime_clears_singleton(self):
        with patch("services.monitor_service.scan_claimed_device", return_value="scanned"):
            rt = start_monitor_runtime(concurrency=1)
            self.assertIs(get_monitor_runtime(), rt)
            stop_monitor_runtime(wait=False)
            self.assertIsNone(get_monitor_runtime())

    def test_scheduler_stop_signals_and_stops_runtime_nonblocking(self):
        import scheduler as sched

        mock_sched = MagicMock()
        mock_sched.running = True

        with (
            patch.object(sched, "scheduler", mock_sched),
            patch.object(sched, "release_scheduler_ownership"),
            patch(
                "services.monitor_runtime.signal_monitor_runtime_leadership_lost"
            ) as signal,
            patch("services.monitor_runtime.stop_monitor_runtime") as stop_rt,
        ):
            sched.stop_scheduler()

        signal.assert_called()
        stop_rt.assert_called_with(wait=False)
        mock_sched.shutdown.assert_called_with(wait=False)


class TestClaimRecovery(unittest.TestCase):
    def test_expired_claim_reclaimed_by_new_leader_path(self):
        from services.monitor_claim import claim_device

        now = utc_now()
        doc = {
            "_id": ObjectId(),
            "monitor": True,
            "hostname": "x",
            "ipAddress": "10.0.0.9",
            "nextCheckAt": now - timedelta(seconds=1),
            "scanClaimId": "old",
            "scanClaimExpiresAt": now - timedelta(seconds=1),
        }
        # Reuse Phase-1 style fake via claim filter matching expired.
        from tests.test_monitor_claim import _FakeDb, _FakeDevicesCollection

        coll = _FakeDevicesCollection(doc)
        with (
            patch("services.monitor_claim._db", return_value=_FakeDb(coll)),
            patch(
                "services.monitor_claim.get_ping_config",
                return_value={
                    "interval": 30,
                    "timeout_ms": 1000,
                    "retries": 2,
                    "failure_confirmation_scans": 2,
                },
            ),
        ):
            claimed = claim_device(doc["_id"], device=doc, now=now)
        self.assertIsNotNone(claimed)
        self.assertNotEqual(claimed["scanClaimId"], "old")

    def test_active_claim_not_reclaimable(self):
        from services.monitor_claim import claim_device
        from tests.test_monitor_claim import _FakeDb, _FakeDevicesCollection

        now = utc_now()
        doc = {
            "_id": ObjectId(),
            "monitor": True,
            "hostname": "x",
            "ipAddress": "10.0.0.9",
            "nextCheckAt": now - timedelta(seconds=1),
            "scanClaimId": "active",
            "scanClaimExpiresAt": now + timedelta(seconds=30),
        }
        coll = _FakeDevicesCollection(doc)
        with (
            patch("services.monitor_claim._db", return_value=_FakeDb(coll)),
            patch(
                "services.monitor_claim.get_ping_config",
                return_value={
                    "interval": 30,
                    "timeout_ms": 1000,
                    "retries": 2,
                    "failure_confirmation_scans": 2,
                },
            ),
        ):
            claimed = claim_device(doc["_id"], device=doc, now=now)
        self.assertIsNone(claimed)
        self.assertEqual(coll.doc["scanClaimId"], "active")

    def test_wrong_claim_id_cannot_clear_newer_claim(self):
        from services.monitor_claim import release_device_claim
        from tests.test_monitor_claim import _FakeDb, _FakeDevicesCollection

        now = utc_now()
        doc = {
            "_id": ObjectId(),
            "scanClaimId": "newer",
            "scanClaimedAt": now,
            "scanClaimExpiresAt": now + timedelta(seconds=15),
            "nextCheckAt": now + timedelta(seconds=30),
        }
        coll = _FakeDevicesCollection(doc)
        with patch("services.monitor_claim._db", return_value=_FakeDb(coll)):
            ok = release_device_claim(doc["_id"], "older")
        self.assertFalse(ok)
        self.assertEqual(coll.doc["scanClaimId"], "newer")


class TestNoDuplicatePingWhileClaimActive(unittest.TestCase):
    def test_second_claim_fails_while_active(self):
        from services.monitor_claim import claim_device
        from tests.test_monitor_claim import _FakeDb, _FakeDevicesCollection

        now = utc_now()
        doc = {
            "_id": ObjectId(),
            "monitor": True,
            "hostname": "x",
            "ipAddress": "10.0.0.8",
        }
        coll = _FakeDevicesCollection(doc)
        cfg = {
            "interval": 30,
            "timeout_ms": 1000,
            "retries": 2,
            "failure_confirmation_scans": 2,
        }
        with (
            patch("services.monitor_claim._db", return_value=_FakeDb(coll)),
            patch("services.monitor_claim.get_ping_config", return_value=cfg),
        ):
            first = claim_device(doc["_id"], device=doc, now=now)
            second = claim_device(doc["_id"], device=coll.doc, now=now)
        self.assertIsNotNone(first)
        self.assertIsNone(second)


if __name__ == "__main__":
    unittest.main()
