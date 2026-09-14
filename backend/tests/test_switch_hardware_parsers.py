from __future__ import annotations

import unittest

from services.switch_hardware.parsers.cisco_ios import (
    parse_cisco_ios_outputs,
    parse_show_environment,
    parse_show_inventory,
    parse_show_processes_cpu,
    parse_show_processes_memory,
    parse_show_version,
)
from services.switch_hardware.parsers.cisco_nxos import parse_cisco_nxos_outputs


IOS_VERSION = """
Cisco IOS Software, C2960 Software (C2960-LANBASEK9-M), Version 15.2(2)E7, RELEASE SOFTWARE (fc3)
sw1 uptime is 10 days, 4 hours, 20 minutes
System serial number            : FCW2145L0AB
Model number                    : WS-C2960-24TT-L
Last reload reason              : Power-On
"""

IOS_ENV = """
Switch 1 FAN 1 is OK
Switch 1 FAN 2 is OK
Switch 1: TEMPERATURE is OK
Inlet Temperature Value: 32 Celsius
Power Supply 1 is OK
Power Supply 2 is OK
"""

IOS_CPU = """
CPU utilization for five seconds: 12%/0%; one minute: 10%; five minutes: 9%
"""

IOS_MEMORY = """
Processor Pool Total:  100000 Used:   45000
"""


class SwitchHardwareParserTests(unittest.TestCase):
    def test_parse_show_version(self):
        inv = parse_show_version(IOS_VERSION)
        self.assertEqual(inv["hostname"], "sw1")
        self.assertIn("15.2", inv["iosVersion"])
        self.assertEqual(inv["serialNumber"], "FCW2145L0AB")
        self.assertEqual(inv["bootReason"], "Power-On")

    def test_parse_show_inventory(self):
        text = '''
NAME: "1", DESCR: "C2960 Chassis"
PID: WS-C2960-24TT-L   , VID: V02  , SN: FCW2145L0AB
'''
        inv = parse_show_inventory(text)
        self.assertEqual(len(inv["chassis"]), 1)
        self.assertEqual(inv["serialNumber"], "FCW2145L0AB")

    def test_parse_show_environment(self):
        parsed = parse_show_environment(IOS_ENV)
        self.assertTrue(parsed["fans"]["items"])
        self.assertTrue(parsed["powerSupplies"]["items"])
        self.assertTrue(parsed["temperature"]["sensors"])

    def test_parse_cpu_and_memory(self):
        cpu = parse_show_processes_cpu(IOS_CPU)
        self.assertEqual(cpu["utilizationPercent"], 12.0)
        mem = parse_show_processes_memory(IOS_MEMORY)
        self.assertEqual(mem["utilizationPercent"], 45.0)

    def test_partial_output(self):
        parsed = parse_cisco_ios_outputs({"version": IOS_VERSION})
        self.assertIsNotNone(parsed["inventory"]["iosVersion"])
        self.assertEqual(parsed["fans"]["items"], [])

    def test_nxos_version(self):
        text = """
Device name: nx-sw1
system:    version 9.3(5)
Kernel uptime is 2 day(s), 3 hour(s)
"""
        parsed = parse_cisco_nxos_outputs({"version": text})
        self.assertEqual(parsed["inventory"]["hostname"], "nx-sw1")


if __name__ == "__main__":
    unittest.main()
