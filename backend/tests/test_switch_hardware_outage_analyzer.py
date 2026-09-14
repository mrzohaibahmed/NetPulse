from __future__ import annotations

import unittest

from services.switch_hardware.outage_analyzer import analyze_outage, is_offline_status


class SwitchHardwareOutageAnalyzerTests(unittest.TestCase):
    def test_unknown_without_evidence(self):
        result = analyze_outage(
            last_known_hardware={},
            recovery_hardware={},
            log_evidence=[],
            ping_evidence={"previousStatus": "Online", "newStatus": "Not Reachable"},
        )
        self.assertEqual(result["rootCause"], "unknown")
        self.assertFalse(result["confirmed"])

    def test_thermal_requires_strong_evidence(self):
        result = analyze_outage(
            last_known_hardware={"temperature": {"status": "critical"}},
            recovery_hardware={},
            log_evidence=[],
            ping_evidence=None,
        )
        self.assertNotEqual(result["rootCause"], "thermal_shutdown")

    def test_thermal_confirmed_with_log(self):
        result = analyze_outage(
            last_known_hardware={},
            recovery_hardware={"inventory": {"bootReason": "thermal shutdown"}},
            log_evidence=[{"message": "%ENVIRONMENT-2-THERMAL_SHUTDOWN: Thermal shutdown"}],
            ping_evidence=None,
        )
        self.assertEqual(result["rootCause"], "thermal_shutdown")
        self.assertIn(result["confidence"], ("confirmed", "suspected"))

    def test_offline_status_helper(self):
        self.assertTrue(is_offline_status("Not Reachable"))
        self.assertFalse(is_offline_status("Online"))


if __name__ == "__main__":
    unittest.main()
