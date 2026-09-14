from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import scheduler as sched


class SwitchHardwareSchedulerTests(unittest.TestCase):
    @patch("services.switch_hardware.config.poll_interval_seconds", return_value=60)
    @patch("services.switch_hardware.config.ssh_interval_seconds", return_value=600)
    @patch("services.switch_hardware.config.inventory_interval_seconds", return_value=3600)
    def test_jobs_always_registered_for_runtime_toggle(self, *_mocks):
        mock_scheduler = MagicMock()
        with patch.object(sched, "scheduler", mock_scheduler):
            sched._start_switch_hardware_jobs()
        self.assertEqual(mock_scheduler.add_job.call_count, 3)


if __name__ == "__main__":
    unittest.main()
