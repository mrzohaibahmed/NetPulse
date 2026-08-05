"""
Tests for operational status refresh during Interface Statistics polling.

Run::

    python -m unittest tests.test_operational_status_refresh -v
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bson import ObjectId

from services.interface_collection.snmp import SNMPInterfaceCollector, _snmp_status_to_text
from services.interface_collection.ssh_stats import (
    merge_cisco_counter_tables,
    parse_cisco_counters,
    parse_cisco_status_map,
    parse_cisco_speed_map,
    parse_juniper_statistics,
)
from services.interface_collection.stats_collector import (
    _format_speed_for_inventory,
    _normalize_refresh_status,
    _operational_fields_from_raw,
    _refresh_inventory_operational_state,
)


CISCO_STATUS = """
Port      Name               Status       Vlan       Duplex  Speed Type
Gi1/0/1                      connected    10         a-full  a-1000 10/100/1000BaseTX
Gi1/0/2                      notconnect   1            auto   auto 10/100/1000BaseTX
Gi1/0/3                      disabled     1            auto   auto 10/100/1000BaseTX
Gi1/0/4                      err-disabled 1            auto   auto 10/100/1000BaseTX
"""

CISCO_COUNTERS = """
Port        InOctets    InUcastPkts    InMcastPkts    InBcastPkts
Gi1/0/1     1000        100            20             5
Gi1/0/2     0           0              0              0

Port        OutOctets   OutUcastPkts   OutMcastPkts   OutBcastPkts
Gi1/0/1     500         50             10             2
Gi1/0/2     0           0              0              0
"""


class SnmpStatusMappingTests(unittest.TestCase):
    def test_admin_oper_integer_mapping(self):
        self.assertEqual(_snmp_status_to_text(1, kind="admin"), "up")
        self.assertEqual(_snmp_status_to_text(2, kind="admin"), "down")
        self.assertEqual(_snmp_status_to_text(3, kind="admin"), "testing")
        self.assertEqual(_snmp_status_to_text(1, kind="oper"), "up")
        self.assertEqual(_snmp_status_to_text(2, kind="oper"), "down")
        self.assertEqual(_snmp_status_to_text(7, kind="oper"), "down")
        self.assertIsNone(_snmp_status_to_text(None, kind="oper"))

    def test_merge_tables_includes_admin_oper(self):
        tables = {
            "name": {1: "Gi1/0/1", 2: "Gi1/0/2"},
            "descr": {},
            "admin_status": {1: 1, 2: 2},
            "oper_status": {1: 1, 2: 2},
            "speed": {1: 1_000_000_000, 2: 0},
            "high_speed": {},
            "in_octets": {1: 10, 2: 0},
            "out_octets": {1: 5, 2: 0},
            "in_ucast": {1: 1, 2: 0},
            "out_ucast": {1: 1, 2: 0},
            "in_nucast": {},
            "out_nucast": {},
            "in_discards": {},
            "out_discards": {},
            "in_errors": {},
            "out_errors": {},
            "hc_in_octets": {},
            "hc_out_octets": {},
            "hc_in_ucast": {},
            "hc_out_ucast": {},
            "in_mcast": {},
            "in_bcast": {},
            "out_mcast": {},
            "out_bcast": {},
        }
        collector = object.__new__(SNMPInterfaceCollector)
        collector.credentials = MagicMock(host="10.0.0.1")
        rows = collector._merge_tables(tables)
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(by_name["Gi1/0/1"]["admin_status"], "up")
        self.assertEqual(by_name["Gi1/0/1"]["oper_status"], "up")
        self.assertEqual(by_name["Gi1/0/2"]["admin_status"], "down")
        self.assertEqual(by_name["Gi1/0/2"]["oper_status"], "down")


class SshStatusParseTests(unittest.TestCase):
    def test_parse_cisco_status_map(self):
        status = parse_cisco_status_map(CISCO_STATUS)
        self.assertEqual(status["Gi1/0/1"]["admin_status"], "up")
        self.assertEqual(status["Gi1/0/1"]["oper_status"], "up")
        self.assertEqual(status["Gi1/0/1"]["speed_bps"], 1_000_000_000)
        self.assertEqual(status["Gi1/0/2"]["admin_status"], "up")
        self.assertEqual(status["Gi1/0/2"]["oper_status"], "down")
        self.assertNotIn("speed_bps", status["Gi1/0/2"])
        self.assertEqual(status["Gi1/0/3"]["admin_status"], "down")
        self.assertEqual(status["Gi1/0/3"]["oper_status"], "down")
        self.assertEqual(status["Gi1/0/4"]["admin_status"], "up")
        self.assertEqual(status["Gi1/0/4"]["oper_status"], "down")

    def test_speed_map_still_works(self):
        speeds = parse_cisco_speed_map(CISCO_STATUS)
        self.assertEqual(speeds["Gi1/0/1"], 1_000_000_000)
        self.assertNotIn("Gi1/0/2", speeds)

    def test_merge_attaches_status_fields(self):
        rows = merge_cisco_counter_tables(
            parse_cisco_counters(CISCO_COUNTERS),
            {},
            parse_cisco_status_map(CISCO_STATUS),
        )
        row = next(r for r in rows if r["name"] == "Gi1/0/1")
        self.assertEqual(row["admin_status"], "up")
        self.assertEqual(row["oper_status"], "up")
        self.assertEqual(row["speed_bps"], 1_000_000_000)
        down = next(r for r in rows if r["name"] == "Gi1/0/2")
        self.assertEqual(down["oper_status"], "down")

    def test_merge_accepts_legacy_speed_dict(self):
        rows = merge_cisco_counter_tables(
            parse_cisco_counters(CISCO_COUNTERS),
            {},
            {"Gi1/0/1": 1_000_000_000},
        )
        row = next(r for r in rows if r["name"] == "Gi1/0/1")
        self.assertEqual(row["speed_bps"], 1_000_000_000)
        self.assertNotIn("admin_status", row)

    def test_juniper_link_flags(self):
        output = """
Physical interface: ge-0/0/0, Enabled, Physical link is Up
  Speed: 1000mbps
  Input packets: 10  Output packets: 5
  Input bytes: 100  Output bytes: 50
Physical interface: ge-0/0/1, Disabled, Physical link is Down
  Input packets: 0  Output packets: 0
  Input bytes: 0  Output bytes: 0
"""
        rows = parse_juniper_statistics(output)
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(by_name["ge-0/0/0"]["admin_status"], "up")
        self.assertEqual(by_name["ge-0/0/0"]["oper_status"], "up")
        self.assertEqual(by_name["ge-0/0/1"]["admin_status"], "down")
        self.assertEqual(by_name["ge-0/0/1"]["oper_status"], "down")


class OperationalFieldsTests(unittest.TestCase):
    def test_skips_null_and_unknown(self):
        self.assertIsNone(_normalize_refresh_status(None))
        self.assertIsNone(_normalize_refresh_status("unknown"))
        self.assertEqual(_normalize_refresh_status("up"), "up")
        self.assertEqual(_normalize_refresh_status("connected"), "up")
        self.assertEqual(_normalize_refresh_status("down"), "down")

    def test_operational_fields_from_raw(self):
        fields = _operational_fields_from_raw({
            "admin_status": "up",
            "oper_status": "down",
            "speed_bps": 1_000_000_000,
        })
        self.assertEqual(fields["adminStatus"], "up")
        self.assertEqual(fields["operStatus"], "down")
        self.assertEqual(fields["speedMbps"], 1000)
        self.assertEqual(fields["speed"], "1G")

    def test_does_not_include_empty_status(self):
        fields = _operational_fields_from_raw({
            "admin_status": None,
            "oper_status": "unknown",
            "rx_bytes": 10,
        })
        self.assertEqual(fields, {})

    def test_format_speed(self):
        self.assertEqual(_format_speed_for_inventory(1000), "1G")
        self.assertEqual(_format_speed_for_inventory(100), "100")


class InventoryRefreshTests(unittest.TestCase):
    def test_targeted_set_only_operational_fields(self):
        device_id = ObjectId()
        iface_id = ObjectId()
        fake_db = MagicMock()
        fake_db.interfaces.find.return_value = [
            {"_id": iface_id, "name": "Gi1/0/1", "ifIndex": 1},
        ]
        fake_db.interfaces.update_one.return_value = MagicMock(
            acknowledged=True,
            modified_count=1,
            matched_count=1,
        )

        with patch(
            "services.interface_collection.stats_collector.db",
            fake_db,
        ):
            updated = _refresh_inventory_operational_state(
                device_id,
                [{
                    "name": "GigabitEthernet1/0/1",
                    "admin_status": "up",
                    "oper_status": "down",
                    "speed_bps": 100_000_000,
                }],
            )

        self.assertEqual(updated, 1)
        fake_db.interfaces.update_one.assert_called_once()
        filter_arg, update_arg = fake_db.interfaces.update_one.call_args[0]
        self.assertEqual(filter_arg, {"_id": iface_id})
        set_fields = update_arg["$set"]
        self.assertEqual(set_fields["adminStatus"], "up")
        self.assertEqual(set_fields["operStatus"], "down")
        self.assertEqual(set_fields["speedMbps"], 100)
        self.assertEqual(set_fields["speed"], "100")
        # Discovery-owned metadata / timestamps must not be touched
        for forbidden in (
            "description", "vlan", "portMode", "isAccess", "isTrunk",
            "isUplink", "neighbor", "lastUpdated", "updatedAt", "createdAt",
            "monitoringEnabled", "monitoringMode",
        ):
            self.assertNotIn(forbidden, set_fields)

    def test_skips_null_overwrite(self):
        device_id = ObjectId()
        fake_db = MagicMock()
        fake_db.interfaces.find.return_value = [
            {"_id": ObjectId(), "name": "Gi1/0/1", "ifIndex": 1},
        ]

        with patch(
            "services.interface_collection.stats_collector.db",
            fake_db,
        ):
            updated = _refresh_inventory_operational_state(
                device_id,
                [{"name": "Gi1/0/1", "admin_status": None, "oper_status": None}],
            )

        self.assertEqual(updated, 0)
        fake_db.interfaces.update_one.assert_not_called()

    def test_matches_by_if_index_when_name_missing(self):
        device_id = ObjectId()
        iface_id = ObjectId()
        fake_db = MagicMock()
        fake_db.interfaces.find.return_value = [
            {"_id": iface_id, "name": "Gi1/0/9", "ifIndex": 42},
        ]
        fake_db.interfaces.update_one.return_value = MagicMock(
            acknowledged=True,
            modified_count=1,
            matched_count=1,
        )

        with patch(
            "services.interface_collection.stats_collector.db",
            fake_db,
        ):
            updated = _refresh_inventory_operational_state(
                device_id,
                [{
                    "name": "",
                    "if_index": 42,
                    "admin_status": "up",
                    "oper_status": "up",
                }],
            )

        self.assertEqual(updated, 1)
        filter_arg, _update_arg = fake_db.interfaces.update_one.call_args[0]
        self.assertEqual(filter_arg, {"_id": iface_id})


if __name__ == "__main__":
    unittest.main()
