"""Phase 8: dispatch observability metrics / SLO helpers."""

from __future__ import annotations

import logging
import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

from bson import ObjectId

from utils.utc import utc_now


class TestMetricCalculations(unittest.TestCase):
    def test_start_to_start_and_due_lag(self):
        from services.monitor_metrics import (
            compute_due_lag_ms,
            compute_ping_duration_ms,
            compute_queue_wait_ms,
            compute_start_to_start_ms,
        )

        t0 = utc_now()
        t1 = t0 + timedelta(seconds=30)
        t2 = t1 + timedelta(milliseconds=850)

        self.assertEqual(compute_start_to_start_ms(t1, t0), 30_000)
        self.assertEqual(compute_due_lag_ms(t1, t0 + timedelta(seconds=-2)), 32_000)
        self.assertEqual(compute_queue_wait_ms(t1, t0 + timedelta(milliseconds=100)), 29_900)
        self.assertEqual(compute_ping_duration_ms(t2, t1), 850)

    def test_missing_previous_start_returns_none(self):
        from services.monitor_metrics import compute_start_to_start_ms

        now = utc_now()
        self.assertIsNone(compute_start_to_start_ms(now, None))
        self.assertIsNone(compute_start_to_start_ms(None, now))
        self.assertIsNone(compute_start_to_start_ms(None, None))

    def test_negative_and_invalid_deltas_safe(self):
        from services.monitor_metrics import safe_ms_delta

        now = utc_now()
        earlier = now - timedelta(seconds=5)
        # later < earlier → invalid
        self.assertIsNone(safe_ms_delta(earlier, now))
        # absurd multi-day delta
        self.assertIsNone(safe_ms_delta(now + timedelta(days=3), now))
        # non-datetime
        self.assertIsNone(safe_ms_delta("not-a-time", now))
        self.assertIsNone(safe_ms_delta(now, "nope"))


class TestSloAndCounters(unittest.TestCase):
    def setUp(self):
        from services.monitor_metrics import reset_dispatch_metrics

        reset_dispatch_metrics()

    def tearDown(self):
        from services.monitor_metrics import reset_dispatch_metrics

        reset_dispatch_metrics()

    def test_slo_miss_counter(self):
        from services.monitor_metrics import (
            SLO_MAX_ALERT_MS,
            SLO_P95_BUDGET_MS,
            get_dispatch_metrics,
            is_slo_miss,
        )

        self.assertFalse(is_slo_miss(30_000))
        self.assertFalse(is_slo_miss(SLO_P95_BUDGET_MS))
        self.assertTrue(is_slo_miss(SLO_P95_BUDGET_MS + 1))

        metrics = get_dispatch_metrics()
        metrics.record_scan_timing(start_to_start_ms=30_000)
        metrics.record_scan_timing(start_to_start_ms=36_000)
        metrics.record_scan_timing(start_to_start_ms=SLO_MAX_ALERT_MS + 1)
        snap = metrics.snapshot()
        self.assertEqual(snap["slo_misses"], 2)
        self.assertEqual(snap["slo_max_alerts"], 1)
        self.assertEqual(snap["scans_observed"], 3)

    def test_claim_conflict_counter(self):
        from services.monitor_metrics import get_dispatch_metrics

        metrics = get_dispatch_metrics()
        metrics.incr_claims_conflict()
        metrics.incr_claims_conflict(2)
        self.assertEqual(metrics.snapshot()["claims_conflict"], 3)

    def test_percentile_snapshot(self):
        from services.monitor_metrics import get_dispatch_metrics

        metrics = get_dispatch_metrics()
        for ms in (30_000, 31_000, 32_000, 33_000, 40_000):
            metrics.record_scan_timing(start_to_start_ms=ms)
        snap = metrics.snapshot()
        self.assertEqual(snap["startToStart_p50Ms"], 32_000)
        self.assertEqual(snap["startToStart_samples"], 5)
        self.assertIsNotNone(snap["startToStart_p95Ms"])


class TestRuntimeQueueAndWorkers(unittest.TestCase):
    def tearDown(self):
        from services import monitor_runtime as rt
        from services.monitor_metrics import reset_dispatch_metrics

        reset_dispatch_metrics()
        with rt._runtime_lock:
            previous = rt._runtime
            rt._runtime = None
        if previous is not None:
            try:
                previous.stop(wait=True)
            except Exception:
                pass

    def test_queue_depth_and_workers_active_accurate(self):
        from services.monitor_runtime import MonitorRuntime

        gate = MagicMock()
        gate.wait = MagicMock()

        started = []

        def slow_scan(device, claim_id="", suppress_offline=False, cycle_id="", timing_out=None):
            started.append(claim_id)
            # Block until test releases — keep worker active.
            import time

            time.sleep(0.15)
            if timing_out is not None:
                now = utc_now()
                timing_out["pingStartedAt"] = now
                timing_out["pingCompletedAt"] = now + timedelta(milliseconds=10)
            return "scanned"

        runtime = MonitorRuntime(concurrency=2)
        runtime.start()
        try:
            with patch(
                "services.monitor_service.scan_claimed_device",
                side_effect=slow_scan,
            ), patch(
                "services.monitor_runtime.release_device_claim",
                return_value=True,
            ):
                d1 = {"_id": ObjectId(), "hostname": "a", "ipAddress": "10.0.0.1"}
                d2 = {"_id": ObjectId(), "hostname": "b", "ipAddress": "10.0.0.2"}
                # Fill both slots; workers take them — queue depth should drop.
                self.assertTrue(
                    runtime.submit_claimed_device(d1, "c1", cycle_id="d1")
                )
                self.assertTrue(
                    runtime.submit_claimed_device(d2, "c2", cycle_id="d1")
                )
                # Wait briefly for workers to pick up.
                import time

                time.sleep(0.05)
                stats = runtime.stats()
                self.assertEqual(stats["workers_total"], 2)
                self.assertGreaterEqual(stats["workers_active"], 1)
                self.assertLessEqual(stats["queue_depth"], 2)
                self.assertEqual(stats["occupancy"], 2)

                # Third submit must be queue_full
                d3 = {"_id": ObjectId(), "hostname": "c", "ipAddress": "10.0.0.3"}
                self.assertFalse(
                    runtime.submit_claimed_device(d3, "c3", cycle_id="d1")
                )
                from services.monitor_metrics import get_dispatch_metrics

                self.assertGreaterEqual(
                    get_dispatch_metrics().snapshot()["queue_full_skips"], 1
                )
                time.sleep(0.25)
        finally:
            runtime.stop(wait=True)


class TestDispatchConflictWiring(unittest.TestCase):
    def tearDown(self):
        from services.monitor_metrics import reset_dispatch_metrics

        reset_dispatch_metrics()

    def test_claim_conflict_increments_metrics(self):
        from services.monitor_dispatch import dispatch_monitor_due_devices
        from services.monitor_metrics import get_dispatch_metrics, reset_dispatch_metrics

        reset_dispatch_metrics()
        now = utc_now()
        d1 = {
            "_id": ObjectId(),
            "hostname": "a",
            "ipAddress": "10.0.0.1",
            "monitor": True,
            "nextCheckAt": now - timedelta(seconds=1),
        }
        guard = MagicMock()
        guard.ensure.return_value = True
        fake_db = MagicMock()
        fake_db.devices.find.return_value.sort.return_value.limit.return_value = [d1]

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
            patch("services.monitor_dispatch.claim_device", return_value=None),
            patch("services.monitor_dispatch.submit_claimed_device") as submit,
            patch("services.monitor_dispatch._maybe_run_integrity_audit"),
            patch("services.monitor_dispatch._emit_heartbeat"),
        ):
            result = dispatch_monitor_due_devices()

        submit.assert_not_called()
        self.assertEqual(result["claim_conflicts"], 1)
        self.assertEqual(get_dispatch_metrics().snapshot()["claims_conflict"], 1)


class TestNoSecretsInLogs(unittest.TestCase):
    def test_format_metric_fields_strips_secret_keys(self):
        from services.monitor_metrics import contains_secret_keys, format_metric_fields

        line = format_metric_fields(
            deviceId="abc",
            claimId="c1",
            sshPassword="hunter2",
            api_token="sekret",
            mongo_uri="mongodb://user:pass@host",
            queueWaitMs=12,
        )
        self.assertIn("deviceId=abc", line)
        self.assertIn("queueWaitMs=12", line)
        self.assertNotIn("hunter2", line)
        self.assertNotIn("sekret", line)
        self.assertNotIn("mongodb://", line)
        self.assertTrue(
            contains_secret_keys({"sshPassword": "x", "deviceId": "1"})
        )
        self.assertFalse(contains_secret_keys({"deviceId": "1", "claimId": "c"}))

    def test_heartbeat_payload_has_no_secret_keys(self):
        from services.monitor_metrics import get_dispatch_metrics, reset_dispatch_metrics

        reset_dispatch_metrics()
        metrics = get_dispatch_metrics()
        metrics.incr_claims_won()
        snap = metrics.snapshot(workers_active=2, queue_depth=1, workers_total=4)
        for key in snap:
            lowered = key.lower()
            for frag in (
                "password",
                "token",
                "jwt",
                "mongo",
                "snmp",
                "secret",
                "credential",
            ):
                self.assertNotIn(frag, lowered)

    def test_scan_completed_log_omits_credentials_even_if_on_device(self):
        from services.monitor_runtime import MonitorRuntime

        records: list[str] = []

        class ListHandler(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))

        handler = ListHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        rt_logger = logging.getLogger("monitor_runtime")
        rt_logger.addHandler(handler)
        try:
            runtime = MonitorRuntime(concurrency=1)
            runtime.start()

            def fake_scan(
                device, claim_id="", suppress_offline=False, cycle_id="", timing_out=None
            ):
                now = utc_now()
                if timing_out is not None:
                    timing_out["pingStartedAt"] = now
                    timing_out["pingCompletedAt"] = now + timedelta(milliseconds=5)
                return "scanned"

            device = {
                "_id": ObjectId(),
                "hostname": "sw1",
                "ipAddress": "10.0.0.9",
                "sshPassword": "should-not-log",
                "snmpCommunity": "public-secret",
            }
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
                runtime.submit_claimed_device(
                    device,
                    "claim-1",
                    cycle_id="disp1",
                    due_at=utc_now() - timedelta(seconds=1),
                    claimed_at=utc_now(),
                    previous_ping_started_at=utc_now() - timedelta(seconds=30),
                )
                import time

                time.sleep(0.2)
            runtime.stop(wait=True)
        finally:
            rt_logger.removeHandler(handler)

        joined = "\n".join(records)
        self.assertIn("Dispatch scan completed", joined)
        self.assertNotIn("should-not-log", joined)
        self.assertNotIn("public-secret", joined)
        self.assertNotIn("sshPassword", joined)


class TestHeartbeatThrottle(unittest.TestCase):
    def tearDown(self):
        from services.monitor_metrics import reset_dispatch_metrics

        reset_dispatch_metrics()

    def test_heartbeat_not_every_tick(self):
        from services.monitor_metrics import get_dispatch_metrics, reset_dispatch_metrics

        reset_dispatch_metrics()
        metrics = get_dispatch_metrics()
        now = utc_now()
        self.assertTrue(
            metrics.maybe_emit_heartbeat(dispatch_id="a", now=now, force=False)
        )
        self.assertFalse(
            metrics.maybe_emit_heartbeat(
                dispatch_id="b",
                now=now + timedelta(seconds=10),
                force=False,
            )
        )
        self.assertTrue(
            metrics.maybe_emit_heartbeat(
                dispatch_id="c",
                now=now + timedelta(seconds=50),
                force=False,
            )
        )


if __name__ == "__main__":
    unittest.main()
