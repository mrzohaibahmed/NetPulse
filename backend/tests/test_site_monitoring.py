"""Tests for location-based site monitoring on the Enterprise Dashboard."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_mock_db_module = MagicMock()
_mock_db_module.db = MagicMock()
_mock_db_module.MAX_SCAN_THREADS = 5
sys.modules.setdefault("config.database", _mock_db_module)

from bson import ObjectId

from models.device import create_device
from models.location import DEFAULT_SITE_LOCATION, SITE_LOCATIONS, validate_location


class LocationValidationTests(unittest.TestCase):
    def test_validate_location_accepts_known_sites(self):
        for site in SITE_LOCATIONS:
            self.assertEqual(validate_location(site), site)

    def test_validate_location_allows_none(self):
        self.assertIsNone(validate_location(None))
        self.assertIsNone(validate_location(""))

    def test_validate_location_maps_legacy_mill_to_mills(self):
        self.assertEqual(validate_location("Mill"), "Mills")
        self.assertEqual(validate_location("Mills"), "Mills")

    def test_validate_location_rejects_too_long_value(self):
        with self.assertRaises(ValueError):
            validate_location("x" * 65)


class DeviceLocationModelTests(unittest.TestCase):
    def test_create_device_with_location(self):
        doc = create_device(
            hostname="Mills Firewall",
            ip_address="192.168.1.10",
            device_type="Server",
            location="Mills",
        )
        self.assertEqual(doc["location"], "Mills")
        self.assertEqual(doc["deviceType"], "Server")
        self.assertFalse(doc["showOnDashboard"])

    def test_create_device_without_location(self):
        doc = create_device(
            hostname="Existing Switch",
            ip_address="192.168.1.11",
            device_type="Switch",
        )
        self.assertNotIn("location", doc)

    def test_create_device_show_on_dashboard_opt_in(self):
        doc = create_device(
            hostname="Dashboard Server",
            ip_address="192.168.1.20",
            device_type="Server",
            location="Karachi",
            show_on_dashboard=True,
        )
        self.assertTrue(doc["showOnDashboard"])


class SiteMonitoringServiceTests(unittest.TestCase):
    @patch("services.site_monitoring_service.list_isp_connections")
    def test_groups_servers_by_location(self, mock_list_isps):
        from services.site_monitoring_service import build_site_monitoring_payload

        mill_id = ObjectId()
        karachi_id = ObjectId()
        lahore_id = ObjectId()

        mock_list_isps.return_value = [
            {
                "_id": "isp-1",
                "name": "Multinet",
                "target": "8.8.8.8",
                "location": "Mills",
                "monitor": True,
                "status": "Online",
                "responseTime": 52.4,
                "lastSeen": None,
                "lastCheckedAt": None,
                "consecutiveFailures": 0,
                "createdAt": None,
                "updatedAt": None,
            }
        ]

        mock_db = MagicMock()
        # Mock returns only server devices (MongoDB filter is applied server-side).
        mock_db.devices.find.return_value.sort.return_value = [
            {
                "_id": mill_id,
                "hostname": "Mills Firewall",
                "ipAddress": "192.168.1.10",
                "deviceType": "Server",
                "location": "Mills",
                "status": "Online",
                "responseTime": 5.0,
                "lastSeen": None,
                "lastCheckedAt": None,
                "monitor": True,
                "critical": False,
            },
            {
                "_id": karachi_id,
                "hostname": "Karachi DNS",
                "ipAddress": "10.0.0.5",
                "deviceType": "Server",
                "location": "Karachi",
                "status": "Online",
                "responseTime": 8.0,
                "lastSeen": None,
                "lastCheckedAt": None,
                "monitor": True,
                "critical": False,
            },
            {
                "_id": lahore_id,
                "hostname": "Lahore AV",
                "ipAddress": "10.0.1.5",
                "deviceType": "Server",
                "location": "Lahore",
                "status": "Not Reachable",
                "responseTime": None,
                "lastSeen": None,
                "lastCheckedAt": None,
                "monitor": True,
                "critical": False,
            },
            {
                "_id": ObjectId(),
                "hostname": "Unassigned Server",
                "ipAddress": "10.0.2.5",
                "deviceType": "Server",
                "status": "Online",
                "responseTime": 2.0,
                "lastSeen": None,
                "lastCheckedAt": None,
                "monitor": True,
                "critical": False,
            },
        ]

        payload = build_site_monitoring_payload(mock_db)
        sites = {site["name"]: site for site in payload["sites"]}

        self.assertIn("Mills", sites)
        self.assertIn("Karachi", sites)
        self.assertIn("Lahore", sites)

        mills_servers = sites["Mills"]["servers"]
        self.assertEqual(len(mills_servers), 1)
        self.assertEqual(mills_servers[0]["hostname"], "Mills Firewall")
        self.assertEqual(mills_servers[0]["status"], "Online")

        karachi_servers = sites["Karachi"]["servers"]
        self.assertEqual(len(karachi_servers), 1)
        self.assertEqual(karachi_servers[0]["hostname"], "Karachi DNS")

        lahore_servers = sites["Lahore"]["servers"]
        self.assertEqual(len(lahore_servers), 1)
        self.assertEqual(lahore_servers[0]["hostname"], "Lahore AV")

    @patch("services.site_monitoring_service.list_isp_connections")
    def test_dashboard_query_filters_show_on_dashboard(self, mock_list_isps):
        from services.site_monitoring_service import (
            _DASHBOARD_SERVER_MATCH,
            build_site_monitoring_payload,
        )

        mock_list_isps.return_value = []
        mock_db = MagicMock()
        mock_db.devices.find.return_value.sort.return_value = []

        build_site_monitoring_payload(mock_db)

        mock_db.devices.find.assert_called_once_with(_DASHBOARD_SERVER_MATCH)

    @patch("services.site_monitoring_service.list_isp_connections")
    def test_groups_legacy_mill_location_under_mills(self, mock_list_isps):
        from services.site_monitoring_service import build_site_monitoring_payload

        mock_list_isps.return_value = []
        mock_db = MagicMock()
        mock_db.devices.find.return_value.sort.return_value = [
            {
                "_id": ObjectId(),
                "hostname": "Legacy Mills Firewall",
                "ipAddress": "192.168.1.12",
                "deviceType": "Server",
                "location": "Mill",
                "status": "Online",
                "responseTime": 4.0,
                "lastSeen": None,
                "lastCheckedAt": None,
                "monitor": True,
                "critical": False,
            }
        ]

        payload = build_site_monitoring_payload(mock_db)
        mills = next(site for site in payload["sites"] if site["name"] == "Mills")

        self.assertEqual(len(mills["servers"]), 1)
        self.assertEqual(mills["servers"][0]["hostname"], "Legacy Mills Firewall")
        self.assertTrue(mills["servers"][0]["showOnDashboard"])

    @patch("services.site_monitoring_service.list_isp_connections")
    def test_isp_and_server_data_are_independent(self, mock_list_isps):
        from services.site_monitoring_service import build_site_monitoring_payload

        mock_list_isps.return_value = [
            {
                "_id": "isp-1",
                "name": "Multinet",
                "target": "8.8.8.8",
                "location": DEFAULT_SITE_LOCATION,
                "monitor": True,
                "status": "Online",
                "responseTime": 10.0,
                "lastSeen": None,
                "lastCheckedAt": None,
                "consecutiveFailures": 0,
                "createdAt": None,
                "updatedAt": None,
            }
        ]

        mock_db = MagicMock()
        mock_db.devices.find.return_value.sort.return_value = [
            {
                "_id": ObjectId(),
                "hostname": "Mills Firewall",
                "ipAddress": "192.168.1.10",
                "deviceType": "Server",
                "location": "Mills",
                "status": "Online",
                "responseTime": 5.0,
                "lastSeen": None,
                "lastCheckedAt": None,
                "monitor": True,
                "critical": False,
            }
        ]

        payload = build_site_monitoring_payload(mock_db)
        mills = next(site for site in payload["sites"] if site["name"] == "Mills")

        self.assertEqual(len(mills["isps"]), 3)
        self.assertEqual(mills["isps"][0]["name"], "Multinet")
        self.assertEqual(len(mills["servers"]), 1)
        self.assertEqual(mills["servers"][0]["hostname"], "Mills Firewall")
        self.assertNotEqual(mills["isps"][0]["name"], mills["servers"][0]["hostname"])


class SiteMonitoringRouteTests(unittest.TestCase):
    @patch("routes.dashboard_routes.db")
    @patch("services.site_monitoring_service.list_isp_connections")
    def test_dashboard_site_monitoring_route(self, mock_list_isps, mock_db):
        from routes.dashboard_routes import dashboard_site_monitoring

        mock_list_isps.return_value = []
        mock_db.devices.find.return_value.sort.return_value = []

        view = dashboard_site_monitoring
        while hasattr(view, "__wrapped__"):
            view = view.__wrapped__

        from flask import Flask

        app = Flask(__name__)
        with app.app_context():
            response, status = view()

        self.assertEqual(status, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertIn("sites", payload)
        self.assertEqual(len(payload["sites"]), len(SITE_LOCATIONS))


if __name__ == "__main__":
    unittest.main()
