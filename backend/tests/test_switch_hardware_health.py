from __future__ import annotations

import unittest

from services.switch_hardware.health_evaluator import evaluate_snapshot
from services.switch_hardware.models import build_sensor, empty_cpu, empty_memory, empty_temperature


class SwitchHardwareHealthTests(unittest.TestCase):
    def test_cpu_thresholds(self):
        snapshot = {
            "temperature": empty_temperature(),
            "fans": {"items": []},
            "powerSupplies": {"items": []},
            "cpu": {"utilizationPercent": 96.0},
            "memory": empty_memory(),
            "hardwareAlarms": [],
        }
        result = evaluate_snapshot(snapshot)
        self.assertEqual(result["cpu"]["status"], "critical")
        self.assertEqual(result["overallHealth"], "critical")

    def test_unknown_sensor_not_healthy(self):
        snapshot = {
            "temperature": {
                "status": "unknown",
                "sensors": [build_sensor(name="T1", value=None, status="unknown")],
            },
            "fans": {"items": []},
            "powerSupplies": {"items": []},
            "cpu": empty_cpu(),
            "memory": empty_memory(),
            "hardwareAlarms": [],
        }
        result = evaluate_snapshot(snapshot)
        self.assertEqual(result["temperature"]["status"], "unknown")
        self.assertNotEqual(result["overallHealth"], "healthy")


if __name__ == "__main__":
    unittest.main()
