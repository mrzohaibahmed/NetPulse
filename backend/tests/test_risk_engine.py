"""
Unit tests for the Advanced Risk Score Engine.

Run::

    python -m unittest tests.test_risk_engine -v
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from services.storm.history import counter_delta, rate_per_second
from services.storm.risk_engine import RiskScoreEngine
from services.storm.thresholds import (
    RiskConfig,
    RiskWeights,
    MetricThresholds,
    score_from_thresholds,
    severity_from_score,
)


def _sample(ts: datetime, **counters) -> dict:
    base = {
        "broadcastPackets": 0,
        "multicastPackets": 0,
        "inputErrors": 0,
        "outputErrors": 0,
        "discards": 0,
        "utilization": 5.0,
        "timestamp": ts,
    }
    base.update(counters)
    return base


class RiskEngineTests(unittest.TestCase):
    def setUp(self):
        self.config = RiskConfig(
            enable_risk=True,
            weights=RiskWeights(),
            broadcast=MetricThresholds(50, 200, 1000, 5000),
            multicast=MetricThresholds(100, 500, 2000, 8000),
            unknown_unicast=MetricThresholds(50, 200, 1000, 5000),
            utilization=MetricThresholds(30, 50, 75, 90),
            errors=MetricThresholds(1, 5, 20, 50),
            discards=MetricThresholds(1, 10, 50, 200),
            crc=MetricThresholds(1, 5, 20, 50),
        )
        self.engine = RiskScoreEngine(config=self.config)
        self.t0 = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        self.t1 = self.t0 + timedelta(seconds=10)

    def test_normal_traffic(self):
        previous = _sample(self.t0, broadcastPackets=100, multicastPackets=50)
        current = _sample(
            self.t1,
            broadcastPackets=150,  # 5 pps
            multicastPackets=80,   # 3 pps
            utilization=8.0,
        )
        result = self.engine.calculate(
            device_id="dev1",
            interface="Gi1/0/5",
            eligible=True,
            current_stats=current,
            previous_stats=previous,
        )
        self.assertTrue(result.eligible)
        self.assertLess(result.risk_score, 25)
        self.assertEqual(result.severity, "LOW")
        metrics = {c["metric"] for c in result.contributors}
        self.assertIn("broadcast", metrics)

    def test_broadcast_storm(self):
        previous = _sample(self.t0, broadcastPackets=0)
        current = _sample(
            self.t1,
            broadcastPackets=62000,  # 6200 pps
            utilization=40.0,
        )
        result = self.engine.calculate(
            device_id="dev1",
            interface="Gi1/0/5",
            eligible=True,
            current_stats=current,
            previous_stats=previous,
        )
        self.assertGreaterEqual(result.risk_score, 75)
        self.assertEqual(result.severity, "CRITICAL")
        top = result.contributors[0]
        self.assertEqual(top["metric"], "broadcast")
        self.assertGreaterEqual(top["score"], 95)

    def test_multicast_storm(self):
        previous = _sample(self.t0, multicastPackets=0, broadcastPackets=1000)
        current = _sample(
            self.t1,
            multicastPackets=90000,  # 9000 pps
            broadcastPackets=1000,   # 0 pps — no dilution
            utilization=0.0,
        )
        result = self.engine.calculate(
            device_id="dev1",
            interface="Gi1/0/8",
            eligible=True,
            current_stats=current,
            previous_stats=previous,
        )
        self.assertGreaterEqual(result.risk_score, 75)
        self.assertEqual(result.severity, "CRITICAL")
        mcast = next(c for c in result.contributors if c["metric"] == "multicast")
        self.assertGreaterEqual(mcast["score"], 75)

    def test_counter_rollover(self):
        # 32-bit wrap
        delta = counter_delta(100, 2**32 - 50)
        self.assertEqual(delta, 150)

        previous = _sample(self.t0, broadcastPackets=2**32 - 100)
        current = _sample(self.t1, broadcastPackets=900)  # +1000 over 10s → 100 pps
        rate, supported = rate_per_second(
            current, previous, "broadcast_packets"
        )
        self.assertTrue(supported)
        self.assertAlmostEqual(rate or 0, 100.0, places=1)

    def test_missing_history(self):
        current = _sample(self.t1, broadcastPackets=5000, utilization=10.0)
        result = self.engine.calculate(
            device_id="dev1",
            interface="Gi1/0/5",
            eligible=True,
            current_stats=current,
            previous_stats=None,
        )
        # Utilization can still score; counter rates have no value.
        util = next(
            (c for c in result.contributors if c["metric"] == "utilization"),
            None,
        )
        self.assertIsNotNone(util)
        broadcast_raw = result.raw_metrics.get("broadcast", {})
        self.assertTrue(broadcast_raw.get("supported"))
        self.assertIsNone(broadcast_raw.get("value"))

    def test_unsupported_metrics(self):
        previous = _sample(self.t0)
        current = _sample(self.t1, utilization=12.0)
        # No unknownUnicastPackets / crcErrors fields → unsupported
        result = self.engine.calculate(
            device_id="dev1",
            interface="Gi1/0/5",
            eligible=True,
            current_stats=current,
            previous_stats=previous,
        )
        self.assertFalse(result.raw_metrics["unknown_unicast"]["supported"])
        self.assertEqual(result.raw_metrics["unknown_unicast"]["score"], 0)
        self.assertFalse(result.raw_metrics["crc"]["supported"])
        # Aggregation still succeeds
        self.assertGreaterEqual(result.risk_score, 0)

    def test_high_utilization(self):
        previous = _sample(self.t0)
        current = _sample(self.t1, utilization=95.0)
        result = self.engine.calculate(
            device_id="dev1",
            interface="Gi1/0/5",
            eligible=True,
            current_stats=current,
            previous_stats=previous,
        )
        util = next(c for c in result.contributors if c["metric"] == "utilization")
        self.assertGreaterEqual(util["score"], 75)

    def test_high_error_rate(self):
        previous = _sample(self.t0, inputErrors=0, outputErrors=0)
        current = _sample(
            self.t1,
            inputErrors=400,   # 40/s
            outputErrors=200,  # 20/s → 60/s total
            utilization=10.0,
        )
        result = self.engine.calculate(
            device_id="dev1",
            interface="Gi1/0/5",
            eligible=True,
            current_stats=current,
            previous_stats=previous,
        )
        errors = next(c for c in result.contributors if c["metric"] == "errors")
        self.assertGreaterEqual(errors["score"], 75)

    def test_eligibility_false(self):
        previous = _sample(self.t0, broadcastPackets=0)
        current = _sample(self.t1, broadcastPackets=100000, utilization=99.0)
        result = self.engine.calculate(
            device_id="dev1",
            interface="Gi1/0/24",
            eligible=False,
            current_stats=current,
            previous_stats=previous,
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.risk_score, 0)
        self.assertEqual(result.severity, "LOW")
        self.assertEqual(result.contributors, [])
        self.assertEqual(result.skipped_reason, "Interface not eligible")

    def test_severity_bands(self):
        self.assertEqual(severity_from_score(0), "LOW")
        self.assertEqual(severity_from_score(24), "LOW")
        self.assertEqual(severity_from_score(25), "MEDIUM")
        self.assertEqual(severity_from_score(49), "MEDIUM")
        self.assertEqual(severity_from_score(50), "HIGH")
        self.assertEqual(severity_from_score(74), "HIGH")
        self.assertEqual(severity_from_score(75), "CRITICAL")
        self.assertEqual(severity_from_score(100), "CRITICAL")

    def test_score_from_thresholds_broadcast_example(self):
        score = score_from_thresholds(6200, self.config.broadcast)
        self.assertGreaterEqual(score, 95)


if __name__ == "__main__":
    unittest.main()
