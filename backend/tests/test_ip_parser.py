import unittest
from utils.ip_parser import parse_single_target, parse_scan_targets

class TestIPParser(unittest.TestCase):
    def test_parse_single_ip(self):
        self.assertEqual(parse_single_target("192.168.1.5"), ["192.168.1.5"])
        self.assertEqual(parse_single_target(" 10.0.0.1  "), ["10.0.0.1"])
        self.assertEqual(parse_single_target("invalid-ip"), [])

    def test_parse_cidr(self):
        # /30 yields 2 host IPs (excluding network and broadcast)
        self.assertEqual(parse_single_target("192.168.1.0/30"), ["192.168.1.1", "192.168.1.2"])
        # /32 yields the host itself
        self.assertEqual(parse_single_target("192.168.1.5/32"), ["192.168.1.5"])
        # Invalid CIDR
        self.assertEqual(parse_single_target("192.168.1.0/99"), [])

    def test_parse_hyphen_range_full_ips(self):
        self.assertEqual(
            parse_single_target("192.168.1.1-192.168.1.3"),
            ["192.168.1.1", "192.168.1.2", "192.168.1.3"]
        )

    def test_parse_hyphen_range_last_octet(self):
        self.assertEqual(
            parse_single_target("192.168.1.5-8"),
            ["192.168.1.5", "192.168.1.6", "192.168.1.7", "192.168.1.8"]
        )

    def test_parse_invalid_hyphen_range(self):
        self.assertEqual(parse_single_target("192.168.1.10-5"), [])
        self.assertEqual(parse_single_target("192.168.1.1-invalid"), [])

    def test_parse_scan_targets_combined(self):
        # Combined test: CIDR, hyphen range, and single IP, comma/space-separated
        targets = "192.168.1.1, 192.168.1.4-6 192.168.1.8/30"
        expected = [
            "192.168.1.1",
            "192.168.1.4", "192.168.1.5", "192.168.1.6",
            "192.168.1.9", "192.168.1.10"
        ]
        self.assertEqual(parse_scan_targets(targets), expected)

    def test_parse_scan_targets_limit(self):
        # A massive range that would exceed 1024 IPs
        large_targets = "10.0.0.0/21" # 2046 hosts
        with self.assertRaises(ValueError) as context:
            parse_scan_targets(large_targets)
        self.assertIn("too many IP addresses", str(context.exception))

if __name__ == "__main__":
    unittest.main()
