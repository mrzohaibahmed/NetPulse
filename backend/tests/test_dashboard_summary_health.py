"""Dashboard summary response shape used by Network Health UI."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from flask import Flask


def _count_side_effect(query=None):
    query = query or {}
    if query == {}:
        return 200
    status = query.get("status")
    if status == "Online":
        return 180
    if status == "Not Reachable":
        return 20
    if status == "Offline (Critical)":
        return 0
    if status == "Unknown":
        return 0
    if status == "Offline":
        return 0
    if query.get("critical") is True:
        return 0
    if query.get("monitor") is True:
        return 200
    if "$or" in query:
        return 0
    return 0


class DashboardSummaryContractTests(unittest.TestCase):
    @patch("routes.dashboard_routes.db")
    def test_summary_exposes_numeric_health_fields(self, mock_db):
        mock_db.devices.aggregate.return_value = iter([
            {
                "total": [{"n": 200}],
                "online": [{"n": 180}],
                "notReachable": [{"n": 20}],
                "offlineCritical": [{"n": 0}],
                "legacyOffline": [{"n": 0}],
                "unknown": [{"n": 0}],
                "criticalFlag": [{"n": 0}],
                "monitored": [{"n": 200}],
            }
        ])

        from routes.dashboard_routes import dashboard_summary

        view = dashboard_summary
        while hasattr(view, "__wrapped__"):
            view = view.__wrapped__

        app = Flask(__name__)
        with app.app_context():
            response, status = view()

        self.assertEqual(status, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        summary = payload["summary"]

        self.assertEqual(summary["totalDevices"], 200)
        self.assertEqual(summary["onlineDevices"], 180)
        self.assertEqual(summary["notReachableDevices"], 20)
        self.assertEqual(summary["criticalOfflineDevices"], 0)
        self.assertEqual(summary["onlinePercentage"], 90.0)
        self.assertEqual(summary["notReachablePercentage"], 10.0)
        self.assertEqual(summary["criticalOfflinePercentage"], 0.0)
        self.assertIsInstance(summary["onlinePercentage"], float)
        self.assertIsInstance(summary["totalDevices"], int)
        self.assertIsInstance(summary["onlineDevices"], int)


if __name__ == "__main__":
    unittest.main()
