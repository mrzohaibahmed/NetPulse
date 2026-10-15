from __future__ import annotations

import unittest
from unittest.mock import patch

from services.interface_collection.collector import (
    _is_interface_discovery_candidate,
    _is_switch_discovery_candidate,
    discover_all_switch_interfaces,
)


class BulkSwitchEligibilityTests(unittest.TestCase):
    def test_bulk_discovery_requires_switch_type_not_merely_credentials(self):
        self.assertTrue(_is_switch_discovery_candidate({"deviceType": "Managed Switch"}))
        self.assertTrue(_is_switch_discovery_candidate({"deviceType": "L3 Switch"}))
        self.assertTrue(_is_switch_discovery_candidate({"type": "Core Switch"}))
        self.assertFalse(_is_switch_discovery_candidate({
            "deviceType": "Router", "credentials": {"sshUsername": "operator"},
        }))
        self.assertFalse(_is_switch_discovery_candidate({
            "deviceType": "Server", "credentials": {"sshUsername": "operator"},
        }))

    def test_discover_all_queues_only_online_switches(self):
        devices = [
            {"_id": "switch-1", "deviceType": "Managed Switch", "status": "Online"},
            {"_id": "switch-2", "deviceType": "L3 Switch", "status": "Online"},
            {"_id": "router-1", "deviceType": "Router", "status": "Online",
             "credentials": {"sshUsername": "operator"}},
            {"_id": "server-1", "deviceType": "Server", "status": "Online",
             "credentials": {"sshUsername": "operator"}},
        ]

        def discovered(device):
            return {
                "success": True,
                "ip": device["_id"],
                "deviceId": device["_id"],
                "upserted": 2,
            }

        with patch("services.interface_collection.collector.db") as mock_db, patch(
            "services.interface_collection.collector.discover_device_interfaces",
            side_effect=discovered,
        ) as discover:
            mock_db.devices.find.return_value = devices
            summary = discover_all_switch_interfaces()

        mock_db.devices.find.assert_called_once_with({"status": "Online"})
        self.assertEqual({call.args[0]["_id"] for call in discover.call_args_list},
                         {"switch-1", "switch-2"})
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["discovered_devices"], 2)
        self.assertEqual(summary["interface_count"], 4)

    def test_bulk_stats_selector_keeps_existing_broader_eligibility(self):
        self.assertTrue(_is_interface_discovery_candidate({
            "deviceType": "Router", "credentials": {"sshUsername": "operator"},
        }))


if __name__ == "__main__":
    unittest.main()
