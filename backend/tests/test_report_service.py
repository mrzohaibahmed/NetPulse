"""Unit tests for report period parsing, KPI labeling, and bounded queries."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from services.report_period import (
    MAX_RANGE_DAYS,
    parse_report_datetime,
    resolve_report_period,
)
from services.report_service import (
    AVAILABILITY_LIMITATIONS,
    PERFORMANCE_LIMITATIONS,
    _format_duration,
    _is_missing_hostname,
    _pct,
    _percentile_from_histogram,
    parse_page,
    period_payload,
)


class ReportPeriodTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)

    def test_default_is_last_24_hours(self):
        window = resolve_report_period(None, now=self.now)
        self.assertEqual(window["period"], "24h")
        self.assertEqual(window["end"], self.now)
        self.assertEqual(window["start"], self.now - timedelta(hours=24))

    def test_7d_and_30d(self):
        week = resolve_report_period("7d", now=self.now)
        month = resolve_report_period("30d", now=self.now)
        self.assertEqual(week["start"], self.now - timedelta(days=7))
        self.assertEqual(month["start"], self.now - timedelta(days=30))

    def test_custom_requires_bounds(self):
        with self.assertRaises(ValueError):
            resolve_report_period("custom", now=self.now)

    def test_custom_inverted_range(self):
        with self.assertRaises(ValueError):
            resolve_report_period(
                "custom",
                "2026-08-18",
                "2026-08-01",
                now=self.now,
            )

    def test_custom_exceeds_max(self):
        with self.assertRaises(ValueError):
            resolve_report_period(
                "custom",
                "2026-01-01",
                "2026-08-01",
                now=self.now,
            )
        self.assertEqual(MAX_RANGE_DAYS, 90)

    def test_invalid_period(self):
        with self.assertRaises(ValueError):
            resolve_report_period("forever", now=self.now)

    def test_invalid_date(self):
        with self.assertRaises(ValueError):
            parse_report_datetime("not-a-date")

    def test_end_of_day(self):
        dt = parse_report_datetime("2026-08-01", end_of_day=True)
        self.assertEqual(dt.hour, 23)
        self.assertEqual(dt.minute, 59)


class ReportHelperTests(unittest.TestCase):
    def test_percentile_histogram(self):
        buckets = [
            {"lo": 0, "hi": 10, "count": 50},
            {"lo": 10, "hi": 20, "count": 40},
            {"lo": 20, "hi": 50, "count": 10},
        ]
        self.assertIsNone(_percentile_from_histogram([], 0, 50))
        p50 = _percentile_from_histogram(buckets, 100, 50)
        self.assertIsNotNone(p50)
        self.assertGreaterEqual(p50, 0)
        p99 = _percentile_from_histogram(buckets, 100, 99)
        self.assertGreaterEqual(p99, p50)

    def test_probe_ratio_formula_is_not_availability_label(self):
        self.assertEqual(_pct(8, 10), 80.0)
        self.assertIsNone(_pct(0, 0))
        joined = " ".join(AVAILABILITY_LIMITATIONS).lower()
        self.assertIn("not time-based availability", joined)
        self.assertIn("not an sla", joined)

    def test_performance_does_not_claim_packet_loss(self):
        text = " ".join(PERFORMANCE_LIMITATIONS).lower()
        self.assertIn("packet loss is not available", text)

    def test_missing_hostname(self):
        self.assertTrue(_is_missing_hostname("Unknown"))
        self.assertTrue(_is_missing_hostname(""))
        self.assertTrue(_is_missing_hostname(None))
        self.assertFalse(_is_missing_hostname("NMS-LAB-SW1"))

    def test_naive_last_checked_compares_to_aware_cutoff(self):
        from services.report_service import _as_utc, _stale_cutoff

        now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
        naive = datetime(2026, 8, 18, 11, 0)  # Mongo-style naive UTC
        checked = _as_utc(naive)
        cutoff = _stale_cutoff(now)
        self.assertIsNotNone(checked)
        # Must not raise TypeError (the executive-report 500).
        self.assertTrue(checked < now)
        self.assertIsNotNone(cutoff)
        _ = checked < cutoff

    def test_parse_page_clamps(self):
        page, limit = parse_page("0", "999")
        self.assertEqual(page, 1)
        self.assertEqual(limit, 100)

    def test_duration_format(self):
        self.assertEqual(_format_duration(45), "45s")
        self.assertEqual(_format_duration(120), "2m")
        self.assertIn("h", _format_duration(7200))

    def test_period_payload_iso(self):
        window = resolve_report_period(
            "24h", now=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
        )
        payload = period_payload(window)
        self.assertTrue(payload["start"].endswith("Z"))
        self.assertEqual(payload["period"], "24h")


class ExecutiveReportEmptyDbTests(unittest.TestCase):
    @patch("services.report_service._db")
    @patch("services.report_service._ping_interval_seconds", return_value=60)
    def test_empty_inventory(self, _interval, mock_db):
        db = MagicMock()
        db.devices.find.return_value.sort.return_value.limit.return_value = []
        db.interfaces.count_documents.return_value = 0
        db.alerts.count_documents.return_value = 0
        db.storm_incidents.count_documents.return_value = 0
        db.storm_risk_latest.count_documents.return_value = 0
        db.storm_risk_latest.find.return_value.sort.return_value.limit.return_value = []
        db.pingHistory.aggregate.return_value = iter([])
        mock_db.return_value = db

        from services.report_service import build_executive_report

        window = resolve_report_period(
            "24h", now=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
        )
        payload = build_executive_report(window, {})
        self.assertTrue(payload["success"])
        self.assertEqual(payload["snapshot"]["totalDevices"], 0)
        self.assertIsNone(payload["periodMetrics"]["probeSuccessRatio"])
        self.assertNotIn("availability", str(payload["periodMetrics"]).lower())
        self.assertIn("limitations", payload)


class ExecutiveReportNaiveTimestampTests(unittest.TestCase):
    @patch("services.report_service._db")
    @patch("services.report_service._ping_interval_seconds", return_value=60)
    def test_naive_last_checked_at_does_not_500(self, _interval, mock_db):
        from bson import ObjectId
        from services.report_service import build_executive_report

        device = {
            "_id": ObjectId(),
            "hostname": "lab-sw1",
            "ipAddress": "10.0.0.1",
            "deviceType": "Switch",
            "status": "Online",
            "monitor": True,
            "lastCheckedAt": datetime(2026, 8, 18, 11, 50),  # naive UTC
        }
        db = MagicMock()
        db.devices.find.return_value.sort.return_value.limit.return_value = [device]
        db.interfaces.count_documents.return_value = 0
        db.alerts.count_documents.return_value = 0
        db.storm_incidents.count_documents.return_value = 0
        db.storm_risk_latest.count_documents.return_value = 0
        db.storm_risk_latest.find.return_value.sort.return_value.limit.return_value = []
        db.pingHistory.aggregate.return_value = iter([])
        mock_db.return_value = db

        window = resolve_report_period(
            "24h", now=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
        )
        payload = build_executive_report(window, {})
        self.assertTrue(payload["success"])
        self.assertEqual(payload["snapshot"]["totalDevices"], 1)


class AvailabilityInvalidDeviceTests(unittest.TestCase):
    def test_invalid_device_id(self):
        from services.report_service import build_availability_report

        window = resolve_report_period(
            "24h", now=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
        )
        with self.assertRaises(ValueError):
            build_availability_report(window, {"device_id": "not-an-id"})


if __name__ == "__main__":
    unittest.main()
