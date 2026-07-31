"""
Tests for interface utilization calculation and Cisco speed parsing.

Run from the backend directory::

    python -m unittest tests.test_utilization -v
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from services.interface_collection.ssh_stats import parse_cisco_speed_map
from services.interface_collection.utilization import (
    compute_utilization,
    counter_delta,
    resolve_speed_bps,
)


class SpeedResolutionTests(unittest.TestCase):
    def test_bps_passthrough(self):
        self.assertEqual(resolve_speed_bps(1_000_000_000), 1_000_000_000)

    def test_mbps_heuristic(self):
        self.assertEqual(resolve_speed_bps(1000), 1_000_000_000)
        self.assertEqual(resolve_speed_bps("100"), 100_000_000)

    def test_skips_invalid(self):
        self.assertEqual(resolve_speed_bps(None, "auto", 0, 1000), 1_000_000_000)


class CiscoSpeedParseTests(unittest.TestCase):
    SAMPLE = """
Port      Name               Status       Vlan       Duplex  Speed Type
Gi1/0/1                      connected    10         a-full  a-1000 10/100/1000BaseTX
Gi1/0/2                      notconnect   1            auto   auto 10/100/1000BaseTX
Gi1/0/5                      connected    10         a-full  a-1000 10/100/1000BaseTX
Gi1/0/21                     connected    10         a-full   a-100 10/100/1000BaseTX
Gi1/0/25                     connected    20         a-full  a-1000 10/100/1000BaseTX
Gi1/0/48                     notconnect   1            auto   auto 10/100/1000BaseTX
Fa0/1                        connected    100        a-full    a-100 10/100BaseTX
"""

    def test_does_not_use_vlan_as_speed(self):
        speeds = parse_cisco_speed_map(self.SAMPLE)
        self.assertEqual(speeds["Gi1/0/1"], 1_000_000_000)
        self.assertEqual(speeds["Gi1/0/5"], 1_000_000_000)
        self.assertEqual(speeds["Gi1/0/21"], 100_000_000)
        self.assertEqual(speeds["Gi1/0/25"], 1_000_000_000)
        self.assertEqual(speeds["Fa0/1"], 100_000_000)
        # auto speed → omitted (inventory/SNMP fallback)
        self.assertNotIn("Gi1/0/2", speeds)
        self.assertNotIn("Gi1/0/48", speeds)


class CounterDeltaTests(unittest.TestCase):
    def test_normal_delta(self):
        delta, event = counter_delta(1500, 1000)
        self.assertEqual(delta, 500)
        self.assertEqual(event, "ok")

    def test_wrap_32(self):
        delta, event = counter_delta(10, 2**32 - 5)
        self.assertEqual(event, "wrap32")
        self.assertEqual(delta, 15)

    def test_reset_detection(self):
        delta, event = counter_delta(100, 50_000_000)
        self.assertEqual(event, "reset")
        self.assertEqual(delta, 0)


class UtilizationFormulaTests(unittest.TestCase):
    def test_one_percent_of_1g(self):
        """
        1% of 1 Gbps for 60s:
          bits = 0.01 * 1e9 * 60 = 6e8
          bytes = 6e8 / 8 = 75_000_000
        """
        now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
        prev = now - timedelta(seconds=60)
        result = compute_utilization(
            current_rx_bytes=75_000_000,
            current_tx_bytes=0,
            previous_rx_bytes=0,
            previous_tx_bytes=0,
            speed_bps=1_000_000_000,
            current_timestamp=now,
            previous_timestamp=prev,
        )
        self.assertAlmostEqual(result["utilization"], 1.0, places=3)
        self.assertAlmostEqual(result["rx_utilization"], 1.0, places=3)
        self.assertEqual(result["tx_utilization"], 0.0)
        self.assertEqual(result["event"], "ok")

    def test_light_traffic_not_zero(self):
        """Gi1/0/5-style light traffic must not collapse to 0."""
        now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
        prev = now - timedelta(seconds=120)
        # ~1.39 MB over 120s on 1G ≈ 0.0093%
        result = compute_utilization(
            current_rx_bytes=1_391_810,
            current_tx_bytes=48_307,
            previous_rx_bytes=0,
            previous_tx_bytes=0,
            speed_bps=1_000_000_000,
            current_timestamp=now,
            previous_timestamp=prev,
        )
        self.assertGreater(result["utilization"], 0.009)
        self.assertLess(result["utilization"], 0.01)
        self.assertIsNotNone(result["utilization"])

    def test_missing_previous_speed_skipped(self):
        now = datetime.now(timezone.utc)
        result = compute_utilization(
            current_rx_bytes=1000,
            current_tx_bytes=1000,
            previous_rx_bytes=0,
            previous_tx_bytes=0,
            speed_bps=None,
            current_timestamp=now,
            previous_timestamp=now - timedelta(seconds=60),
        )
        self.assertIsNone(result["utilization"])
        self.assertEqual(result["event"], "missing_speed")

    def test_counter_reset_skips_sample(self):
        now = datetime.now(timezone.utc)
        result = compute_utilization(
            current_rx_bytes=100,
            current_tx_bytes=100,
            previous_rx_bytes=90_000_000,
            previous_tx_bytes=80_000_000,
            speed_bps=1_000_000_000,
            current_timestamp=now,
            previous_timestamp=now - timedelta(seconds=60),
        )
        self.assertIsNone(result["utilization"])
        self.assertEqual(result["event"], "counter_reset")

    def test_full_duplex_overall_is_max(self):
        now = datetime.now(timezone.utc)
        prev = now - timedelta(seconds=10)
        # RX 2%, TX 5% of 100 Mbps
        # 5% * 100e6 * 10 / 8 = 6_250_000 bytes TX
        # 2% * 100e6 * 10 / 8 = 2_500_000 bytes RX
        result = compute_utilization(
            current_rx_bytes=2_500_000,
            current_tx_bytes=6_250_000,
            previous_rx_bytes=0,
            previous_tx_bytes=0,
            speed_bps=100_000_000,
            current_timestamp=now,
            previous_timestamp=prev,
        )
        self.assertAlmostEqual(result["rx_utilization"], 2.0, places=2)
        self.assertAlmostEqual(result["tx_utilization"], 5.0, places=2)
        self.assertAlmostEqual(result["utilization"], 5.0, places=2)


if __name__ == "__main__":
    unittest.main()
