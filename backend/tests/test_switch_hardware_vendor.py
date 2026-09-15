from __future__ import annotations

import unittest

from services.switch_hardware.vendor_detection import (
    get_ineligibility_reason,
    is_cisco_device,
    is_eligible_switch,
)


class SwitchHardwareVendorTests(unittest.TestCase):
    def test_cisco_vendor_switch_is_eligible(self):
        device = {
            "monitor": True,
            "vendor": "Cisco Systems",
            "deviceType": "Managed Switch",
        }
        self.assertTrue(is_cisco_device(device))
        self.assertTrue(is_eligible_switch(device))
        self.assertIsNone(get_ineligibility_reason(device))

    def test_blank_vendor_with_credentials_is_provisionally_eligible(self):
        device = {
            "monitor": True,
            "vendor": "",
            "deviceType": "switch",
            "credentials": {"sshUsername": "admin", "sshPassword": "x"},
        }
        self.assertFalse(is_cisco_device(device))
        self.assertTrue(is_eligible_switch(device))

    def test_blank_vendor_without_credentials_is_not_eligible(self):
        device = {
            "monitor": True,
            "vendor": None,
            "deviceType": "Managed Switch",
        }
        self.assertFalse(is_eligible_switch(device))
        reason = get_ineligibility_reason(device) or ""
        self.assertIn("credentials", reason.lower())

    def test_explicit_non_cisco_is_rejected(self):
        device = {
            "monitor": True,
            "vendor": "Juniper",
            "deviceType": "switch",
            "credentials": {"sshUsername": "admin", "sshPassword": "x"},
        }
        self.assertFalse(is_eligible_switch(device))
        self.assertEqual(get_ineligibility_reason(device), "Device vendor is not Cisco")

    def test_named_non_cisco_without_hint_list_gets_guidance(self):
        device = {
            "monitor": True,
            "vendor": "Acme Networks",
            "deviceType": "switch",
            "credentials": {"sshUsername": "admin", "sshPassword": "x"},
        }
        self.assertFalse(is_eligible_switch(device))
        reason = get_ineligibility_reason(device) or ""
        self.assertIn("Acme Networks", reason)
        self.assertIn("Cisco", reason)

    def test_monitor_false_is_rejected(self):
        device = {
            "monitor": False,
            "vendor": "Cisco",
            "deviceType": "switch",
        }
        self.assertFalse(is_eligible_switch(device))
        self.assertIn("monitoring is disabled", get_ineligibility_reason(device) or "")

    def test_ssh_vendor_cisco_ios_counts_as_cisco(self):
        device = {
            "monitor": True,
            "vendor": "",
            "deviceType": "switch",
            "credentials": {"sshVendor": "cisco_ios"},
        }
        self.assertTrue(is_cisco_device(device))
        self.assertTrue(is_eligible_switch(device))


if __name__ == "__main__":
    unittest.main()
