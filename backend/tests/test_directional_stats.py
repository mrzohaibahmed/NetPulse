"""
Tests for RX/TX directional interface statistics and storm source analysis.

Run::

    python -m unittest tests.test_directional_stats -v
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from models.interface_stats import create_interface_stat
from services.interface_collection.ssh_stats import (
    merge_cisco_counter_tables,
    parse_cisco_counter_errors,
    parse_cisco_counters,
)
from services.storm.analyzers.broadcast import BroadcastAnalyzer
from services.storm.risk_engine import RiskScoreEngine
from services.storm.source_classification import (
    LIKELY_RECEIVER,
    LIKELY_SOURCE,
    classify_storm_source,
)
from services.storm.thresholds import MetricThresholds, RiskConfig, RiskWeights
from utils.serializers import serialize_interface_stat, serialize_risk_result


CISCO_COUNTERS = """
Port        InOctets    InUcastPkts    InMcastPkts    InBcastPkts
Gi1/0/1     1000        100            20             5
Gi1/0/2     2000        200            40             10

Port        OutOctets   OutUcastPkts   OutMcastPkts   OutBcastPkts
Gi1/0/1     500         50             10             2
Gi1/0/2     800         80             16             4
"""

CISCO_ERRORS = """
Port        Align-Err   FCS-Err   Xmit-Err   Rcv-Err   UnderSize   OutDiscards
Gi1/0/1     0           0         0          1         0           3
Gi1/0/2     0           0         0          0         0           0
"""


class DirectionalStatsTests(unittest.TestCase):
    def test_ssh_parse_preserves_rx_tx_broadcast_multicast(self):
        counters = parse_cisco_counters(CISCO_COUNTERS)
        port = counters["Gi1/0/1"]
        self.assertEqual(port["rx_broadcast_packets"], 5)
        self.assertEqual(port["tx_broadcast_packets"], 2)
        self.assertEqual(port["rx_multicast_packets"], 20)
        self.assertEqual(port["tx_multicast_packets"], 10)
        self.assertEqual(port["broadcast_packets"], 7)
        self.assertEqual(port["multicast_packets"], 30)

    def test_ssh_parse_preserves_rx_tx_discards(self):
        errors = parse_cisco_counter_errors(CISCO_ERRORS)
        port = errors["Gi1/0/1"]
        self.assertEqual(port["rx_discards"], 0)
        self.assertEqual(port["tx_discards"], 3)
        self.assertEqual(port["discards"], 3)

    def test_ssh_merge_includes_directional_fields(self):
        rows = merge_cisco_counter_tables(
            parse_cisco_counters(CISCO_COUNTERS),
            parse_cisco_counter_errors(CISCO_ERRORS),
            {},
        )
        row = next(r for r in rows if r["name"] == "Gi1/0/1")
        self.assertEqual(row["rx_broadcast_packets"], 5)
        self.assertEqual(row["tx_broadcast_packets"], 2)
        self.assertEqual(row["broadcast_packets"], 7)
        self.assertEqual(row["tx_discards"], 3)

    def test_create_interface_stat_stores_directional_fields(self):
        doc = create_interface_stat(
            "dev",
            "host",
            "1.1.1.1",
            "Gi1/0/1",
            broadcast_packets=7,
            multicast_packets=30,
            rx_broadcast_packets=5,
            tx_broadcast_packets=2,
            rx_multicast_packets=20,
            tx_multicast_packets=10,
            rx_discards=1,
            tx_discards=3,
            discards=4,
        )
        self.assertEqual(doc["rxBroadcastPackets"], 5)
        self.assertEqual(doc["txBroadcastPackets"], 2)
        self.assertEqual(doc["broadcastPackets"], 7)
        self.assertEqual(doc["rxDiscards"], 1)
        self.assertEqual(doc["txDiscards"], 3)
        self.assertEqual(doc["discards"], 4)

    def test_create_interface_stat_omits_missing_directional_fields(self):
        doc = create_interface_stat("dev", "host", "1.1.1.1", "Gi1/0/1")
        self.assertNotIn("rxBroadcastPackets", doc)
        self.assertEqual(doc["broadcastPackets"], 0)

    def test_serializer_exposes_directional_fields(self):
        doc = create_interface_stat(
            "dev",
            "host",
            "1.1.1.1",
            "Gi1/0/1",
            rx_broadcast_packets=5,
            tx_broadcast_packets=2,
            broadcast_packets=7,
        )
        doc["_id"] = "abc"
        payload = serialize_interface_stat(doc)
        self.assertEqual(payload["rxBroadcastPackets"], 5)
        self.assertEqual(payload["txBroadcastPackets"], 2)
        self.assertEqual(payload["broadcastPackets"], 7)

    def test_serializer_old_document_without_directional_fields(self):
        legacy = {
            "_id": "abc",
            "deviceId": "dev",
            "interfaceName": "Gi1/0/1",
            "broadcastPackets": 100,
            "multicastPackets": 50,
            "discards": 3,
        }
        payload = serialize_interface_stat(legacy)
        self.assertIsNone(payload["rxBroadcastPackets"])
        self.assertEqual(payload["broadcastPackets"], 100)

    def test_broadcast_analyzer_prefers_rx_on_access_port(self):
        config = RiskConfig(
            broadcast=MetricThresholds(50, 200, 1000, 5000),
            weights=RiskWeights(broadcast=1.0),
        )
        analyzer = BroadcastAnalyzer()
        t0 = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=10)
        previous = {
            "timestamp": t0,
            "rxBroadcastPackets": 0,
            "txBroadcastPackets": 0,
            "broadcastPackets": 0,
        }
        current = {
            "timestamp": t1,
            "rxBroadcastPackets": 5000,  # 500 pps
            "txBroadcastPackets": 100,    # 10 pps
            "broadcastPackets": 5100,
        }
        result = analyzer.analyze(
            current,
            previous,
            config,
            interface_context={"isAccess": True, "portMode": "access"},
        )
        self.assertTrue(result.supported)
        self.assertAlmostEqual(result.value or 0, 500.0, places=1)
        detail = result.detail
        self.assertAlmostEqual(detail["rxRate"] or 0, 500.0, places=1)
        self.assertAlmostEqual(detail["txRate"] or 0, 10.0, places=1)

    def test_broadcast_analyzer_legacy_combined_fallback(self):
        config = RiskConfig(
            broadcast=MetricThresholds(50, 200, 1000, 5000),
            weights=RiskWeights(broadcast=1.0),
        )
        analyzer = BroadcastAnalyzer()
        t0 = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=10)
        previous = {"timestamp": t0, "broadcastPackets": 0}
        current = {"timestamp": t1, "broadcastPackets": 500}
        result = analyzer.analyze(current, previous, config)
        self.assertTrue(result.supported)
        self.assertAlmostEqual(result.value or 0, 50.0, places=1)
        self.assertFalse(result.detail.get("directional"))

    def test_source_classification_access_tx_dominant(self):
        t0 = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=10)
        previous = {
            "timestamp": t0,
            "rxBroadcastPackets": 0,
            "txBroadcastPackets": 0,
        }
        current = {
            "timestamp": t1,
            "rxBroadcastPackets": 100,
            "txBroadcastPackets": 5000,
        }
        out = classify_storm_source(
            current=current,
            previous=previous,
            interface_context={"isAccess": True},
            risk_score=80.0,
        )
        # TX-dominant access = flooded receiver (Cisco OutBcast)
        self.assertEqual(out["sourceClassification"], LIKELY_RECEIVER)
        self.assertGreater(out["sourceConfidence"], 0)

    def test_source_classification_access_rx_dominant(self):
        t0 = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=10)
        previous = {
            "timestamp": t0,
            "rxBroadcastPackets": 0,
            "txBroadcastPackets": 0,
        }
        current = {
            "timestamp": t1,
            "rxBroadcastPackets": 5000,
            "txBroadcastPackets": 100,
        }
        out = classify_storm_source(
            current=current,
            previous=previous,
            interface_context={"isAccess": True},
            risk_score=80.0,
        )
        # RX-dominant access = originating host (Cisco InBcast)
        self.assertEqual(out["sourceClassification"], LIKELY_SOURCE)

    def test_risk_engine_stores_source_fields(self):
        config = RiskConfig(
            weights=RiskWeights(),
            broadcast=MetricThresholds(50, 200, 1000, 5000),
            multicast=MetricThresholds(100, 500, 2000, 8000),
        )
        engine = RiskScoreEngine(config=config)
        t0 = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=10)
        previous = {
            "timestamp": t0,
            "rxBroadcastPackets": 0,
            "txBroadcastPackets": 0,
            "broadcastPackets": 0,
            "multicastPackets": 0,
            "utilization": 5.0,
        }
        current = {
            "timestamp": t1,
            "rxBroadcastPackets": 62000,
            "txBroadcastPackets": 100,
            "broadcastPackets": 62100,
            "multicastPackets": 0,
            "utilization": 40.0,
        }
        result = engine.calculate(
            device_id="dev1",
            interface="Gi1/0/5",
            eligible=True,
            current_stats=current,
            previous_stats=previous,
            persist=False,
        )
        self.assertIsNotNone(result.source_classification)
        self.assertGreaterEqual(result.source_confidence, 0)

    def test_serialize_risk_result_includes_source_fields(self):
        payload = serialize_risk_result({
            "_id": "1",
            "deviceId": "dev",
            "interface": "Gi1/0/1",
            "riskScore": 80,
            "severity": "CRITICAL",
            "confidence": 90,
            "contributors": [],
            "rawMetrics": {},
            "eligible": True,
            "sourceClassification": "LIKELY_SOURCE",
            "sourceConfidence": 72.5,
            "sourceRationale": "Dominant TX on access port.",
            "timestamp": datetime.now(timezone.utc),
        })
        self.assertEqual(payload["sourceClassification"], "LIKELY_SOURCE")
        self.assertEqual(payload["sourceConfidence"], 72.5)
        self.assertEqual(payload["broadcastRate"], None)


if __name__ == "__main__":
    unittest.main()
