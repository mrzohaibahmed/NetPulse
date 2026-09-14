from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bson import ObjectId

from services.switch_hardware.events import detect_hardware_events
from services.switch_hardware.models import build_fan


class SwitchHardwareEventTests(unittest.TestCase):
    @patch("services.switch_hardware.events.db")
    def test_transition_creates_event(self, mock_db):
        mock_db.switch_hardware_events.find_one.return_value = None
        device_id = ObjectId()
        current = {
            "temperature": {"sensors": []},
            "fans": {"items": [build_fan(name="Fan 1", status="critical")]},
            "powerSupplies": {"items": []},
            "cpu": {"status": "unknown"},
            "memory": {"status": "unknown"},
            "hardwareAlarms": [],
        }
        generated = detect_hardware_events(device_id, {}, current)
        self.assertIn("fan_failed", generated)
        mock_db.switch_hardware_events.insert_one.assert_called()

    @patch("services.switch_hardware.events.db")
    def test_repeated_poll_does_not_duplicate(self, mock_db):
        mock_db.switch_hardware_events.find_one.return_value = {"_id": ObjectId()}
        device_id = ObjectId()
        current = {
            "temperature": {"sensors": []},
            "fans": {"items": [build_fan(name="Fan 1", status="critical")]},
            "powerSupplies": {"items": []},
            "cpu": {"status": "unknown"},
            "memory": {"status": "unknown"},
            "hardwareAlarms": [],
        }
        detect_hardware_events(device_id, {}, current)
        mock_db.switch_hardware_events.insert_one.assert_not_called()


if __name__ == "__main__":
    unittest.main()
