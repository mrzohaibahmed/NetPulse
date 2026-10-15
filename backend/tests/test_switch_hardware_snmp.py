from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from services.interface_collection.snmp import SNMPCollectorError, SNMPCredentials
from services.switch_hardware.snmp_collector import (
    SwitchHardwareSNMPCollector,
    collect_snmp_hardware,
)


class SwitchHardwareSNMPTests(unittest.TestCase):
    def test_cisco_walks_entity_sensor_scale_and_precision(self):
        collector = object.__new__(SwitchHardwareSNMPCollector)
        collector.platform = "cisco_ios"
        walked = []

        async def walk(oid, max_rows=500):
            walked.append(oid)
            return {}

        async def scalar(_oid):
            return None

        collector._walk_oid = walk
        collector._get_scalar = scalar
        tables = asyncio.run(collector._walk_tables())
        self.assertIn("1.3.6.1.2.1.99.1.1.1.2", walked)
        self.assertIn("1.3.6.1.2.1.99.1.1.1.3", walked)
        self.assertIn("temperature", collector._normalize(tables)["observed"])

    def test_cisco_entity_sensor_applies_mib_scale_and_precision(self):
        collector = object.__new__(SwitchHardwareSNMPCollector)
        collector.platform = "cisco_ios"
        parsed = collector._normalize({
            "sensor_type": {1: "8"},
            "sensor_scale": {1: "9"},
            "sensor_precision": {1: "1"},
            "sensor_value": {1: "415"},
            "sensor_status": {1: "1"},
        })
        self.assertEqual(parsed["temperature"]["sensors"][0]["value"], 41.5)

    def test_failed_oid_walks_do_not_claim_snmp_is_available(self):
        collector = object.__new__(SwitchHardwareSNMPCollector)
        collector.platform = "juniper_junos"
        parsed = collector._normalize({"_successful": set(), "_errors": {"ent_descr": "timeout"}})
        self.assertEqual(parsed["availability"]["snmp"], "unavailable")
        self.assertEqual(parsed["observed"], [])

    @patch("services.switch_hardware.snmp_collector.resolve_snmp_credentials")
    @patch("services.switch_hardware.snmp_collector.SwitchHardwareSNMPCollector")
    def test_snmp_v3_is_rejected_instead_of_silently_using_v2c(self, collector_cls, resolve):
        resolve.return_value = SNMPCredentials(host="192.0.2.1", community="public", version="3")
        with self.assertRaisesRegex(SNMPCollectorError, "SNMPv2c or SSH"):
            collect_snmp_hardware({"vendor": "Juniper", "ipAddress": "192.0.2.1"})
        collector_cls.assert_not_called()

    def test_total_collection_timeout_is_enforced(self):
        collector = object.__new__(SwitchHardwareSNMPCollector)
        collector.overall_timeout = 0.01

        async def slow_walk():
            await asyncio.sleep(1)
            return {}

        collector._walk_tables = slow_walk
        with self.assertRaisesRegex(SNMPCollectorError, "timed out"):
            collector.collect()


if __name__ == "__main__":
    unittest.main()
