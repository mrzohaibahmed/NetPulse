"""
Unit tests for storm source classification and arbitration.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from services.storm.source_arbitration_config import (
    SourceArbitrationConfig,
    reload_source_arbitration_config,
)
from services.storm.source_classification import (
    LIKELY_FORWARDER,
    LIKELY_RECEIVER,
    LIKELY_SOURCE,
    NORMAL,
    POSSIBLE_SOURCE,
    classify_storm_source,
)
from services.storm.storm_source_selector import (
    SourceCandidate,
    _score_candidate,
    confirmation_allowed_for_source,
    select_storm_source,
)


def _pair(rx: float, tx: float, seconds: float = 10.0):
    t0 = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=seconds)
    previous = {
        "timestamp": t0,
        "rxBroadcastPackets": 0,
        "txBroadcastPackets": 0,
        "rxMulticastPackets": 0,
        "txMulticastPackets": 0,
    }
    current = {
        "timestamp": t1,
        "rxBroadcastPackets": int(rx * seconds),
        "txBroadcastPackets": int(tx * seconds),
        "rxMulticastPackets": 0,
        "txMulticastPackets": 0,
    }
    return current, previous


class SourceClassificationFixTests(unittest.TestCase):
    def test_access_high_rx_low_tx_is_likely_source(self):
        current, previous = _pair(rx=31000, tx=0.4)
        out = classify_storm_source(
            current=current,
            previous=previous,
            interface_context={"isAccess": True},
            risk_score=58,
        )
        self.assertEqual(out["sourceClassification"], LIKELY_SOURCE)

    def test_access_high_tx_low_rx_is_likely_receiver(self):
        current, previous = _pair(rx=0.4, tx=31000)
        out = classify_storm_source(
            current=current,
            previous=previous,
            interface_context={"isAccess": True},
            risk_score=38,
        )
        self.assertEqual(out["sourceClassification"], LIKELY_RECEIVER)

    def test_access_both_high_is_possible_source(self):
        current, previous = _pair(rx=20000, tx=18000)
        out = classify_storm_source(
            current=current,
            previous=previous,
            interface_context={"isAccess": True},
            risk_score=70,
        )
        self.assertEqual(out["sourceClassification"], POSSIBLE_SOURCE)

    def test_low_risk_is_normal(self):
        current, previous = _pair(rx=1, tx=1)
        out = classify_storm_source(
            current=current,
            previous=previous,
            interface_context={"isAccess": True},
            risk_score=10,
        )
        self.assertEqual(out["sourceClassification"], NORMAL)

    def test_trunk_bidirectional_is_forwarder(self):
        current, previous = _pair(rx=15000, tx=14000)
        out = classify_storm_source(
            current=current,
            previous=previous,
            interface_context={"isTrunk": True},
            risk_score=80,
        )
        self.assertEqual(out["sourceClassification"], LIKELY_FORWARDER)


class SourceSelectorTests(unittest.TestCase):
    def setUp(self):
        reload_source_arbitration_config()
        self.cfg = SourceArbitrationConfig(
            enable_source_arbitration=True,
            enable_receiver_filtering=True,
            filter_forwarders=True,
            allow_confirm_receivers=False,
            minimum_source_confidence=20.0,
            maximum_candidates=25,
            tie_threshold=5.0,
            receiver_penalty=40.0,
            forwarder_penalty=35.0,
            rx_weight=1.0,
            tx_penalty=0.5,
            risk_weight=0.35,
            allow_multiple_sources=False,
        )

    def _cand(self, **kwargs):
        base = dict(
            device_id="dev1",
            interface="Gi1/0/1",
            broadcast_domain="vlan:10",
            risk_score=50,
            source_classification=LIKELY_SOURCE,
            source_confidence=80,
            rx_broadcast_rate=1000,
            tx_broadcast_rate=10,
            eligible=True,
        )
        base.update(kwargs)
        return SourceCandidate(**base)

    def test_single_source_selected(self):
        risk_rows = [
            {
                "deviceId": "dev1",
                "interface": "Gi1/0/19",
                "riskScore": 58.61,
                "sourceClassification": LIKELY_SOURCE,
                "sourceConfidence": 90,
                "eligible": True,
                "rawMetrics": {
                    "broadcast": {
                        "value": 31000,
                        "detail": {"rxRate": 31000, "txRate": 0.4},
                    }
                },
            }
        ]
        with patch(
            "services.storm.storm_source_selector._interface_doc",
            return_value={"isAccess": True, "accessVlan": 10},
        ), patch(
            "services.storm.storm_source_selector._stats_pair",
            return_value=(None, None),
        ), patch(
            "services.storm.storm_source_selector._is_mitigated",
            return_value=False,
        ), patch(
            "services.storm.storm_source_selector._in_cooldown",
            return_value=False,
        ):
            result = select_storm_source(
                "dev1",
                broadcast_domain="vlan:10",
                risk_threshold=25,
                config=self.cfg,
                risk_rows=risk_rows,
            )
        self.assertIsNotNone(result.best)
        self.assertEqual(result.best.interface, "Gi1/0/19")
        self.assertEqual(result.candidate_count, 1)

    def test_multiple_victims_only_origin_selected(self):
        risk_rows = [
            {
                "deviceId": "dev1",
                "interface": "Gi1/0/19",
                "riskScore": 58.61,
                "sourceClassification": LIKELY_SOURCE,
                "sourceConfidence": 95,
                "eligible": True,
                "rawMetrics": {
                    "broadcast": {"detail": {"rxRate": 31000, "txRate": 0.4}}
                },
            },
            {
                "deviceId": "dev1",
                "interface": "Gi1/0/48",
                "riskScore": 38.21,
                "sourceClassification": LIKELY_RECEIVER,
                "sourceConfidence": 95,
                "eligible": True,
                "rawMetrics": {
                    "broadcast": {"detail": {"rxRate": 0.4, "txRate": 31000}}
                },
            },
            {
                "deviceId": "dev1",
                "interface": "Gi1/0/11",
                "riskScore": 38.2,
                "sourceClassification": LIKELY_RECEIVER,
                "sourceConfidence": 95,
                "eligible": True,
                "rawMetrics": {
                    "broadcast": {"detail": {"rxRate": 0.0, "txRate": 31000}}
                },
            },
        ]

        def iface_doc(_d, name):
            return {"isAccess": True, "accessVlan": 10, "name": name}

        with patch(
            "services.storm.storm_source_selector._interface_doc",
            side_effect=iface_doc,
        ), patch(
            "services.storm.storm_source_selector._stats_pair",
            return_value=(None, None),
        ), patch(
            "services.storm.storm_source_selector._is_mitigated",
            return_value=False,
        ), patch(
            "services.storm.storm_source_selector._in_cooldown",
            return_value=False,
        ):
            result = select_storm_source(
                "dev1",
                broadcast_domain="vlan:10",
                risk_threshold=25,
                config=self.cfg,
                risk_rows=risk_rows,
            )
        self.assertEqual(result.best.interface, "Gi1/0/19")
        self.assertEqual(result.candidate_count, 1)
        self.assertEqual(result.receiver_count, 2)

    def test_receiver_only_yields_no_candidate(self):
        risk_rows = [
            {
                "deviceId": "dev1",
                "interface": "Gi1/0/48",
                "riskScore": 38,
                "sourceClassification": LIKELY_RECEIVER,
                "sourceConfidence": 90,
                "eligible": True,
                "rawMetrics": {
                    "broadcast": {"detail": {"rxRate": 0.4, "txRate": 31000}}
                },
            }
        ]
        with patch(
            "services.storm.storm_source_selector._interface_doc",
            return_value={"isAccess": True, "accessVlan": 10},
        ), patch(
            "services.storm.storm_source_selector._stats_pair",
            return_value=(None, None),
        ), patch(
            "services.storm.storm_source_selector._is_mitigated",
            return_value=False,
        ), patch(
            "services.storm.storm_source_selector._in_cooldown",
            return_value=False,
        ):
            result = select_storm_source(
                "dev1",
                risk_threshold=25,
                config=self.cfg,
                risk_rows=risk_rows,
            )
        self.assertIsNone(result.best)
        self.assertEqual(result.receiver_count, 1)

    def test_trunk_filtered_out(self):
        risk_rows = [
            {
                "deviceId": "dev1",
                "interface": "Gi1/0/1",
                "riskScore": 90,
                "sourceClassification": LIKELY_FORWARDER,
                "sourceConfidence": 80,
                "eligible": True,
                "rawMetrics": {
                    "broadcast": {"detail": {"rxRate": 20000, "txRate": 20000}}
                },
            }
        ]
        with patch(
            "services.storm.storm_source_selector._interface_doc",
            return_value={"isTrunk": True, "accessVlan": None},
        ), patch(
            "services.storm.storm_source_selector._stats_pair",
            return_value=(None, None),
        ), patch(
            "services.storm.storm_source_selector._is_mitigated",
            return_value=False,
        ), patch(
            "services.storm.storm_source_selector._in_cooldown",
            return_value=False,
        ):
            result = select_storm_source(
                "dev1",
                risk_threshold=25,
                config=self.cfg,
                risk_rows=risk_rows,
            )
        self.assertIsNone(result.best)

    def test_infrastructure_filtered(self):
        cand = self._cand(
            interface="Gi1/0/2",
            is_infrastructure=True,
            source_classification=LIKELY_SOURCE,
        )
        score = _score_candidate(cand, self.cfg)
        normal = _score_candidate(self._cand(), self.cfg)
        self.assertLess(score, normal)

    def test_equal_rx_tie_break_by_risk(self):
        risk_rows = [
            {
                "deviceId": "dev1",
                "interface": "Gi1/0/7",
                "riskScore": 55,
                "sourceClassification": LIKELY_SOURCE,
                "sourceConfidence": 80,
                "eligible": True,
                "rawMetrics": {
                    "broadcast": {"detail": {"rxRate": 10000, "txRate": 1}}
                },
            },
            {
                "deviceId": "dev1",
                "interface": "Gi1/0/8",
                "riskScore": 70,
                "sourceClassification": LIKELY_SOURCE,
                "sourceConfidence": 80,
                "eligible": True,
                "rawMetrics": {
                    "broadcast": {"detail": {"rxRate": 10000, "txRate": 1}}
                },
            },
        ]
        with patch(
            "services.storm.storm_source_selector._interface_doc",
            return_value={"isAccess": True, "accessVlan": 10},
        ), patch(
            "services.storm.storm_source_selector._stats_pair",
            return_value=(None, None),
        ), patch(
            "services.storm.storm_source_selector._is_mitigated",
            return_value=False,
        ), patch(
            "services.storm.storm_source_selector._in_cooldown",
            return_value=False,
        ):
            result = select_storm_source(
                "dev1",
                risk_threshold=25,
                config=self.cfg,
                risk_rows=risk_rows,
            )
        self.assertEqual(result.best.interface, "Gi1/0/8")
        self.assertEqual(result.candidate_count, 2)

    def test_no_candidate(self):
        result = select_storm_source(
            "dev1",
            risk_threshold=25,
            config=self.cfg,
            risk_rows=[],
        )
        self.assertIsNone(result.best)
        self.assertIn("No eligible", result.reason)

    def test_high_tx_low_rx_penalized(self):
        origin = self._cand(
            rx_broadcast_rate=31000,
            tx_broadcast_rate=0.4,
            source_classification=LIKELY_SOURCE,
        )
        victim = self._cand(
            interface="Gi1/0/48",
            rx_broadcast_rate=0.4,
            tx_broadcast_rate=31000,
            source_classification=LIKELY_RECEIVER,
            risk_score=38,
        )
        self.assertGreater(
            _score_candidate(origin, self.cfg),
            _score_candidate(victim, self.cfg),
        )

    def test_confirmation_blocks_receiver(self):
        allowed, reason, _ = confirmation_allowed_for_source(
            "dev1",
            "Gi1/0/48",
            risk_doc={"sourceClassification": LIKELY_RECEIVER, "riskScore": 38},
            config=self.cfg,
        )
        self.assertFalse(allowed)
        self.assertIn("receiver", reason.lower())

    def test_confirmation_allows_selected_source(self):
        with patch(
            "services.storm.storm_source_selector.is_selected_storm_source",
            return_value=(
                True,
                MagicMock(
                    reason="Selected",
                    to_dict=lambda: {"selectedInterface": "Gi1/0/19"},
                ),
            ),
        ):
            allowed, reason, _ = confirmation_allowed_for_source(
                "dev1",
                "Gi1/0/19",
                risk_doc={"sourceClassification": LIKELY_SOURCE, "riskScore": 58},
                config=self.cfg,
            )
        self.assertTrue(allowed)

    def test_arbitration_disabled_passes_through(self):
        cfg = SourceArbitrationConfig(
            enable_source_arbitration=False,
            enable_receiver_filtering=False,
            allow_confirm_receivers=True,
        )
        allowed, reason, _ = confirmation_allowed_for_source(
            "dev1",
            "Gi1/0/48",
            risk_doc={"sourceClassification": LIKELY_RECEIVER},
            config=cfg,
        )
        self.assertTrue(allowed)
        self.assertIn("disabled", reason.lower())


class ConfirmationGateIntegrationTests(unittest.TestCase):
    @patch("services.storm.storm_source_selector.confirmation_allowed_for_source")
    @patch("services.storm.confirmation.load_latest_confirmation", return_value=None)
    @patch("services.storm.confirmation.detect_poll_failure", return_value=(False, None))
    @patch("services.storm.confirmation.load_eligibility", return_value=True)
    @patch("services.storm.confirmation.load_recent_risk_scores")
    def test_receiver_never_reaches_confirmed(
        self,
        mock_risk_rows,
        _elig,
        _poll,
        _prev,
        mock_gate,
    ):
        from services.storm.confirmation import ConfirmationEngine
        from services.storm.confirmation_rules import ConfirmationConfig

        mock_gate.return_value = (
            False,
            "Likely receiver — confirmation blocked by source attribution",
            None,
        )
        mock_risk_rows.return_value = [
            {"riskScore": 80, "sourceClassification": LIKELY_RECEIVER}
            for _ in range(6)
        ]
        engine = ConfirmationEngine(
            config=ConfirmationConfig(
                confirmation_enabled=True,
                required_confirmations=2,
                risk_threshold=25,
            )
        )
        result = engine.evaluate("dev1", "Gi1/0/48", persist=False)
        self.assertFalse(result.confirmed)
        self.assertIn("receiver", result.reason.lower())
        mock_gate.assert_called()


if __name__ == "__main__":
    unittest.main()
