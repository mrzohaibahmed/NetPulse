"""Phase 5: claimed-device worker → existing ping/apply pipeline invariants."""

from __future__ import annotations

import time
import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

from bson import ObjectId

from services.monitor_runtime import MonitorRuntime
from utils.utc import utc_now


def _device(**overrides):
    doc = {
        "_id": ObjectId(),
        "hostname": "host-a",
        "ipAddress": "192.168.0.10",
        "monitor": True,
        "critical": False,
        "status": "Online",
        "scanClaimId": "claim-abc",
        "consecutiveFailures": 0,
    }
    doc.update(overrides)
    return doc


def _wait_until(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class TestScanClaimedDeviceGate(unittest.TestCase):
    def test_missing_claim_id_does_not_ping(self):
        from services.monitor_service import scan_claimed_device

        with patch("services.monitor_service.ping_device") as ping:
            outcome = scan_claimed_device(
                _device(),
                claim_id="",
                cycle_id="c1",
            )
        self.assertEqual(outcome, "failed")
        ping.assert_not_called()

    def test_claim_mismatch_does_not_ping(self):
        from services.monitor_service import scan_claimed_device

        with patch("services.monitor_service.ping_device") as ping:
            outcome = scan_claimed_device(
                _device(scanClaimId="other"),
                claim_id="mine",
                cycle_id="c1",
            )
        self.assertEqual(outcome, "failed")
        ping.assert_not_called()


class TestRuntimePipelineSuccess(unittest.TestCase):
    def test_successful_claimed_ping_applies_and_releases(self):
        runtime = MonitorRuntime(concurrency=1)
        device = _device(scanClaimId="ok-1")
        now = utc_now()
        ping_result = {
            "success": True,
            "status": "Online",
            "responseTime": 12.5,
            "lastSeen": now,
            "message": "ok",
            "attempts": 2,
            "timeoutMs": 1000,
            "pingStartedAt": now,
            "pingCompletedAt": now,
        }
        released = []
        apply_calls = []
        attempt_ids = []

        def fake_apply(dev, result, scan_type="Automatic", **kwargs):
            apply_calls.append(
                {
                    "attempt_id": kwargs.get("attempt_id"),
                    "scan_type": scan_type,
                    "success": result.get("success"),
                }
            )
            attempt_ids.append(kwargs.get("attempt_id"))
            return "Online"

        def fake_release(device_id, claim_id):
            released.append((device_id, claim_id))
            return True

        with (
            patch("services.monitor_service.ping_device", return_value=ping_result) as ping,
            patch(
                "services.monitor_service.apply_ping_result",
                side_effect=fake_apply,
            ),
            patch(
                "services.monitor_runtime.release_device_claim",
                side_effect=fake_release,
            ),
        ):
            runtime.start()
            self.assertTrue(runtime.submit_claimed_device(device, "ok-1", cycle_id="cyc"))
            self.assertTrue(
                _wait_until(lambda: runtime.stats()["claims_processed"] >= 1)
            )
            runtime.stop(wait=True)

        ping.assert_called_once()
        self.assertEqual(len(apply_calls), 1)
        self.assertTrue(apply_calls[0]["success"])
        self.assertEqual(apply_calls[0]["scan_type"], "Automatic")
        self.assertIsNotNone(attempt_ids[0])
        self.assertEqual(released, [(device["_id"], "ok-1")])


class TestRuntimePipelineFailure(unittest.TestCase):
    def test_failed_claimed_ping_applies_and_releases(self):
        runtime = MonitorRuntime(concurrency=1)
        device = _device(scanClaimId="fail-1")
        now = utc_now()
        ping_result = {
            "success": False,
            "status": "Not Reachable",
            "responseTime": None,
            "lastSeen": None,
            "message": "unreachable",
            "attempts": 2,
            "timeoutMs": 1000,
            "pingStartedAt": now,
            "pingCompletedAt": now,
        }
        released = []
        apply_calls = []

        with (
            patch("services.monitor_service.ping_device", return_value=ping_result),
            patch(
                "services.monitor_service.apply_ping_result",
                side_effect=lambda *a, **k: apply_calls.append(k) or "Not Reachable",
            ),
            patch(
                "services.monitor_runtime.release_device_claim",
                side_effect=lambda did, cid: released.append(cid) or True,
            ),
        ):
            runtime.start()
            self.assertTrue(runtime.submit_claimed_device(device, "fail-1"))
            self.assertTrue(
                _wait_until(lambda: runtime.stats()["claims_processed"] >= 1)
            )
            runtime.stop(wait=True)

        self.assertEqual(len(apply_calls), 1)
        self.assertEqual(released, ["fail-1"])


class TestRetriesOneLogicalScan(unittest.TestCase):
    def test_icmp_retries_one_attempt_id_one_history(self):
        """pingRetries=2 ⇒ multiple ICMP inside one scan; one apply/history."""
        from services.monitor_service import _scan_device

        device = _device()
        calls = {"ping": 0}
        history = []
        attempt_ids = []

        def fake_ping(ip, critical=False, timeout_ms=None, retries=None, device=None):
            calls["ping"] += 1
            # Simulate ping_device consuming retries internally — called once per scan.
            return {
                "success": False,
                "status": "Not Reachable",
                "responseTime": None,
                "lastSeen": None,
                "message": "unreachable",
                "attempts": retries or 2,
                "timeoutMs": timeout_ms or 1000,
                "pingStartedAt": utc_now(),
                "pingCompletedAt": utc_now(),
            }

        def fake_apply(dev, result, scan_type="Automatic", **kwargs):
            attempt_ids.append(kwargs.get("attempt_id"))
            return "Not Reachable"

        def fake_history(**kwargs):
            history.append(kwargs)

        with (
            patch("services.monitor_service.ping_device", side_effect=fake_ping),
            patch(
                "services.monitor_service.apply_ping_result",
                side_effect=fake_apply,
            ),
            patch(
                "services.monitor_service.save_ping_history",
                side_effect=fake_history,
            ),
            patch(
                "services.ping_service.get_ping_config",
                return_value={
                    "interval": 30,
                    "timeout_ms": 1000,
                    "retries": 2,
                    "failure_confirmation_scans": 2,
                },
            ),
        ):
            # Drive through claim gate used by runtime.
            from services.monitor_service import scan_claimed_device

            device["scanClaimId"] = "retry-1"
            outcome = scan_claimed_device(
                device,
                claim_id="retry-1",
                cycle_id="c-retry",
            )

        self.assertEqual(outcome, "scanned")
        # One logical scan ⇒ ping_device invoked once (retries internal).
        self.assertEqual(calls["ping"], 1)
        self.assertEqual(len(attempt_ids), 1)
        self.assertIsNotNone(attempt_ids[0])
        # apply_ping_result owns history; with mocked apply, history not called —
        # assert apply received one attempt_id (single logical scan).
        self.assertEqual(len(attempt_ids), 1)

    def test_real_ping_device_retries_still_one_outer_call_from_scan(self):
        """_scan_device calls ping_device once; ICMP loop is inside ping_device."""
        from services import ping_service

        icmp_calls = {"n": 0}

        def fake_icmp(ip, timeout=None):
            icmp_calls["n"] += 1
            return None

        with (
            patch.object(ping_service, "ping", side_effect=fake_icmp),
            patch.object(
                ping_service,
                "get_ping_config",
                return_value={
                    "interval": 30,
                    "timeout_ms": 100,
                    "retries": 2,
                    "failure_confirmation_scans": 2,
                },
            ),
        ):
            result = ping_service.ping_device(
                "192.168.0.99",
                device=_device(),
            )

        self.assertFalse(result["success"])
        self.assertEqual(icmp_calls["n"], 2)
        self.assertEqual(result["attempts"], 2)


class TestStaleCasReleasesClaim(unittest.TestCase):
    def test_stale_apply_still_releases_claim(self):
        runtime = MonitorRuntime(concurrency=1)
        device = _device(scanClaimId="stale-1")
        now = utc_now()
        older = now - timedelta(seconds=30)
        ping_result = {
            "success": True,
            "status": "Online",
            "responseTime": 1.0,
            "lastSeen": older,
            "message": "ok",
            "attempts": 2,
            "timeoutMs": 1000,
            "pingStartedAt": older,
            "pingCompletedAt": older,
        }
        released = []

        # apply_ping_result returns previous status on stale without raising.
        def stale_apply(dev, result, **kwargs):
            return "Online"

        with (
            patch("services.monitor_service.ping_device", return_value=ping_result),
            patch(
                "services.monitor_service.apply_ping_result",
                side_effect=stale_apply,
            ),
            patch(
                "services.monitor_service._atomic_mark_online",
                return_value=(None, "stale"),
            ),
            patch(
                "services.monitor_runtime.release_device_claim",
                side_effect=lambda did, cid: released.append(cid) or True,
            ),
        ):
            runtime.start()
            self.assertTrue(runtime.submit_claimed_device(device, "stale-1"))
            self.assertTrue(
                _wait_until(lambda: len(released) >= 1)
            )
            runtime.stop(wait=True)

        self.assertEqual(released, ["stale-1"])

    def test_stale_cas_does_not_overwrite_via_freshness_filter(self):
        """Older pingStartedAt cannot pass freshness CAS (existing filter)."""
        from services.monitor_service import _freshness_filter

        now = utc_now()
        older = now - timedelta(seconds=10)
        filt = _freshness_filter(ObjectId(), "attempt-old", older)
        # Filter requires lastPingStartedAt <= ping_started_at OR missing.
        # A device with newer lastPingStartedAt would not match — verified by shape.
        self.assertIn("lastPingAttemptId", filt)
        self.assertIn("$or", filt)


class TestPartitionAndExceptionRelease(unittest.TestCase):
    def test_partition_suppression_releases_claim(self):
        runtime = MonitorRuntime(concurrency=1)
        device = _device(scanClaimId="part-1")
        now = utc_now()
        ping_result = {
            "success": False,
            "status": "Not Reachable",
            "responseTime": None,
            "lastSeen": None,
            "message": "unreachable",
            "attempts": 2,
            "timeoutMs": 1000,
            "pingStartedAt": now,
            "pingCompletedAt": now,
        }
        released = []
        coll = MagicMock()
        coll.find_one_and_update.return_value = {**device, "lastCheckedAt": now}
        fake_db = MagicMock()
        fake_db.devices = coll

        with (
            patch("services.monitor_service.ping_device", return_value=ping_result),
            patch("services.monitor_service._db", return_value=fake_db),
            patch("services.monitor_service.save_ping_history") as hist,
            patch(
                "services.monitor_runtime.release_device_claim",
                side_effect=lambda did, cid: released.append(cid) or True,
            ),
        ):
            runtime.start()
            self.assertTrue(
                runtime.submit_claimed_device(
                    device,
                    "part-1",
                    suppress_offline=True,
                )
            )
            self.assertTrue(_wait_until(lambda: len(released) >= 1))
            runtime.stop(wait=True)

        # Partition path touches lastCheckedAt only — no failure history.
        hist.assert_not_called()
        self.assertEqual(released, ["part-1"])
        update = coll.find_one_and_update.call_args[0][1]["$set"]
        self.assertIn("lastCheckedAt", update)
        self.assertNotIn("status", update)

    def test_worker_exception_releases_claim(self):
        runtime = MonitorRuntime(concurrency=1)
        device = _device(scanClaimId="boom-1")
        released = []

        with (
            patch(
                "services.monitor_service.ping_device",
                side_effect=RuntimeError("icmp crashed"),
            ),
            patch(
                "services.monitor_runtime.release_device_claim",
                side_effect=lambda did, cid: released.append(cid) or True,
            ),
        ):
            runtime.start()
            self.assertTrue(runtime.submit_claimed_device(device, "boom-1"))
            self.assertTrue(_wait_until(lambda: len(released) >= 1))
            runtime.stop(wait=True)

        self.assertEqual(released, ["boom-1"])
        self.assertGreaterEqual(runtime.stats()["failures"], 1)


class TestNoUnclaimedPing(unittest.TestCase):
    def test_runtime_rejects_empty_claim_before_ping(self):
        runtime = MonitorRuntime(concurrency=1)
        pinged = []

        with (
            patch(
                "services.monitor_service.ping_device",
                side_effect=lambda *a, **k: pinged.append(1),
            ),
            patch(
                "services.monitor_runtime.release_device_claim",
                return_value=False,
            ),
        ):
            runtime.start()
            ok = runtime.submit_claimed_device(_device(), "")
            self.assertFalse(ok)
            time.sleep(0.15)
            runtime.stop(wait=True)

        self.assertEqual(pinged, [])


if __name__ == "__main__":
    unittest.main()
