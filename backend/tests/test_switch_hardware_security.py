from __future__ import annotations

import unittest

import importlib.util
import sys
from pathlib import Path

# Import ssh_capture without loading storm package (avoids MongoDB at import time).
_path = Path(__file__).resolve().parents[1] / "services" / "storm" / "diagnostics" / "ssh_capture.py"
_spec = importlib.util.spec_from_file_location("ssh_capture_isolated", _path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ssh_capture_isolated"] = _mod
assert _spec.loader is not None
_spec.loader.exec_module(_mod)
assert_read_only_command = _mod.assert_read_only_command
DiagnosticsSSHError = _mod.DiagnosticsSSHError


class SwitchHardwareSecurityTests(unittest.TestCase):
    def test_destructive_command_rejected(self):
        with self.assertRaises(DiagnosticsSSHError):
            assert_read_only_command("configure terminal")

    def test_read_only_show_allowed(self):
        self.assertEqual(
            assert_read_only_command("show environment"),
            "show environment",
        )

    def test_non_show_rejected(self):
        with self.assertRaises(DiagnosticsSSHError):
            assert_read_only_command("ping 1.1.1.1")


if __name__ == "__main__":
    unittest.main()
