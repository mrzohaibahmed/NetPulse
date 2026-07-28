"""
Unit tests for the Confirmation Engine.

Run::

    python -m unittest tests.test_confirmation -v
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from services.storm.confirmation import ConfirmationEngine
from services.storm.confirmation_history import count_trailing_high, window_stats
from services.storm.confirmation_rules import (
    STATE_CONFIRMED,
    STATE_NOT_CONFIRMED,
    STATE_PENDING,
    ConfirmationConfig,
)
from services.storm.history import counter_delta


def _risk_rows(*scores: float, eligible: bool = True) -> list[dict]:
    """Newest-first risk documents."""
    now = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
    rows = []
    for index, score in enumerate(scores):
        rows.append({
            "riskScore": score,
            "eligible": eligible,
            "timestamp": now - timedelta(minutes=index),
        })
    return rows


class ConfirmationEngineTests(unittest.TestCase):
    def setUp(self):
        self.config = ConfirmationConfig(
            confirmation_enabled=True,
            required_confirmations=4,
            risk_threshold=75.0,
            reset_on_poll_failure=True,
            reset_on_ineligible=True,
            reset_on_low_risk=True,
            poll_stale_seconds=180,
        )
        self.engine = ConfirmationEngine(config=self.config)

    def _eval(self, scores, **kwargs):
        rows = _risk_rows(*scores) if scores is not None else []
        defaults = {
            "eligible": True,
            "poll_failed": False,
            "previous_confirmation": {},
            "risk_rows": rows,
            "current_risk": scores[0] if scores else 0.0,
            "persist": False,
        }
        defaults.update(kwargs)
        return self.engine.evaluate("507f1f77bcf86cd799439011", "Gi1/0/10", **defaults)

    def test_four_consecutive_high_risk_samples(self):
        result = self._eval([92, 90, 88, 97])
        self.assertTrue(result.confirmed)
        self.assertEqual(result.state, STATE_CONFIRMED)
        self.assertEqual(result.consecutive_high_samples, 4)
        self.assertEqual(result.required_samples, 4)
        self.assertGreaterEqual(result.highest_risk, 97)
        self.assertIn("4 consecutive", result.reason.lower())

    def test_pending_before_required(self):
        result = self._eval([88, 90])
        self.assertFalse(result.confirmed)
        self.assertEqual(result.state, STATE_PENDING)
        self.assertEqual(result.consecutive_high_samples, 2)
        self.assertIn("Awaiting", result.reason)

    def test_risk_drops_below_threshold(self):
        result = self._eval(
            [40, 90, 91, 92],
            previous_confirmation={
                "state": STATE_PENDING,
                "consecutiveHighSamples": 3,
            },
        )
        self.assertFalse(result.confirmed)
        self.assertEqual(result.state, STATE_NOT_CONFIRMED)
        self.assertTrue(result.reset)
        self.assertIn("dropped below threshold", result.reset_reason.lower())

    def test_polling_failure(self):
        result = self._eval(
            [95, 94, 93, 92],
            poll_failed=True,
            poll_failure_reason="Stale statistics (polling failure)",
            previous_confirmation={
                "state": STATE_PENDING,
                "consecutiveHighSamples": 3,
            },
        )
        self.assertFalse(result.confirmed)
        self.assertEqual(result.state, STATE_NOT_CONFIRMED)
        self.assertTrue(result.reset)
        self.assertIn("polling", result.reset_reason.lower())

    def test_interface_becomes_ineligible(self):
        result = self._eval(
            [96, 95, 94, 93],
            eligible=False,
            previous_confirmation={
                "state": STATE_PENDING,
                "consecutiveHighSamples": 2,
            },
        )
        self.assertFalse(result.confirmed)
        self.assertEqual(result.state, STATE_NOT_CONFIRMED)
        self.assertTrue(result.reset)
        self.assertIn("not eligible", result.reset_reason.lower())

    def test_device_offline(self):
        result = self._eval(
            [91, 90, 89],
            poll_failed=True,
            poll_failure_reason="Device unreachable (Offline)",
            previous_confirmation={
                "state": STATE_PENDING,
                "consecutiveHighSamples": 2,
            },
        )
        self.assertTrue(result.reset)
        self.assertEqual(result.state, STATE_NOT_CONFIRMED)
        self.assertIn("unreachable", result.reset_reason.lower())

    def test_missing_statistics(self):
        result = self._eval(
            [0],
            current_risk=0.0,
            risk_rows=[],
            poll_failed=True,
            poll_failure_reason="Missing statistics",
            previous_confirmation={
                "state": STATE_PENDING,
                "consecutiveHighSamples": 1,
            },
        )
        self.assertTrue(result.reset)
        self.assertIn("Missing statistics", result.reset_reason)

    def test_counter_rollover_helper(self):
        # Confirmation relies on risk scores (already rollover-safe).
        # Verify underlying counter math still wraps correctly.
        delta = counter_delta(100, 2**32 - 50)
        self.assertEqual(delta, 150)
        # Trailing high streak ignores scores below threshold after wrap noise.
        rows = _risk_rows(80, 10, 90)
        trailing = count_trailing_high(rows, 75)
        self.assertEqual(trailing, [80.0])

    def test_threshold_configuration_changes(self):
        strict = ConfirmationEngine(
            config=ConfirmationConfig(
                required_confirmations=2,
                risk_threshold=90.0,
            )
        )
        rows = _risk_rows(92, 91, 80)
        result = strict.evaluate(
            "dev1",
            "Gi1/0/5",
            eligible=True,
            poll_failed=False,
            previous_confirmation={},
            risk_rows=rows,
            current_risk=92,
            persist=False,
        )
        self.assertTrue(result.confirmed)
        self.assertEqual(result.required_samples, 2)
        self.assertEqual(result.state, STATE_CONFIRMED)

        # Same scores fail confirmation when threshold rises above them.
        higher = ConfirmationEngine(
            config=ConfirmationConfig(
                required_confirmations=2,
                risk_threshold=95.0,
            )
        )
        result2 = higher.evaluate(
            "dev1",
            "Gi1/0/5",
            eligible=True,
            poll_failed=False,
            previous_confirmation={
                "state": STATE_PENDING,
                "consecutiveHighSamples": 1,
            },
            risk_rows=rows,
            current_risk=92,
            persist=False,
        )
        self.assertFalse(result2.confirmed)
        self.assertEqual(result2.state, STATE_NOT_CONFIRMED)

    def test_window_stats(self):
        current, highest, average = window_stats([92, 90, 88, 97])
        self.assertEqual(current, 92)
        self.assertEqual(highest, 97)
        self.assertEqual(average, 91.75)

    def test_not_confirmed_when_never_high(self):
        result = self._eval([20, 15, 10])
        self.assertFalse(result.confirmed)
        self.assertEqual(result.state, STATE_NOT_CONFIRMED)
        self.assertEqual(result.consecutive_high_samples, 0)


if __name__ == "__main__":
    unittest.main()
