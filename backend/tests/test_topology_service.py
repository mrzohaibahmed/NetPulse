"""Unit tests for topology edge status derivation and Level 1/Level 2 behavior."""

from __future__ import annotations

import unittest

from services.ping_service import (
    STATUS_NOT_REACHABLE,
    STATUS_OFFLINE_CRITICAL,
    STATUS_ONLINE,
)
from services.topology_service import (
    _apply_edge_statuses,
    _derive_edge_status,
    _is_known_device_online,
    _prune_unconnected_synthetic_nodes,
)


def _device(device_id: str, status: str) -> dict:
    return {
        "_id": device_id,
        "hostname": f"sw-{device_id}",
        "ipAddress": f"10.0.0.{device_id[-1]}",
        "status": status,
    }


def _edge(source: str, target: str, **extra) -> dict:
    payload = {
        "id": f"edge_{source}_{target}",
        "source": source,
        "target": target,
        "sourcePort": "Gi1/0/1",
        "targetPort": "Gi1/0/2",
        "protocol": "CDP/LLDP",
        "animated": True,
    }
    payload.update(extra)
    return payload


class TestIsKnownDeviceOnline(unittest.TestCase):
    def setUp(self):
        self.devices = {
            "a": _device("a", STATUS_ONLINE),
            "b": _device("b", "Offline"),
            "c": _device("c", STATUS_NOT_REACHABLE),
            "d": _device("d", STATUS_OFFLINE_CRITICAL),
            "e": _device("e", "Unknown"),
        }

    def test_online_known_device(self):
        self.assertTrue(_is_known_device_online("a", self.devices))

    def test_offline_known_device(self):
        self.assertFalse(_is_known_device_online("b", self.devices))

    def test_not_reachable_known_device(self):
        self.assertFalse(_is_known_device_online("c", self.devices))

    def test_offline_critical_known_device(self):
        self.assertFalse(_is_known_device_online("d", self.devices))

    def test_unknown_status_known_device(self):
        self.assertFalse(_is_known_device_online("e", self.devices))

    def test_synthetic_neighbor_not_online(self):
        self.assertFalse(_is_known_device_online("neighbor_10.0.0.5", self.devices))

    def test_synthetic_endpoint_not_online(self):
        self.assertFalse(_is_known_device_online("endpoint_a_Gi1/0/1", self.devices))


class TestLevel1EdgeStatus(unittest.TestCase):
    """Level 1: preserve links and mark offline/unresolved endpoints stale."""

    def setUp(self):
        self.devices = {
            "a": _device("a", STATUS_ONLINE),
            "b": _device("b", STATUS_ONLINE),
            "c": _device("c", "Offline"),
            "d": _device("d", STATUS_NOT_REACHABLE),
            "e": _device("e", STATUS_OFFLINE_CRITICAL),
            "f": _device("f", "Unknown"),
        }

    def test_both_online_active(self):
        self.assertEqual(_derive_edge_status("a", "b", self.devices), "active")

    def test_source_offline_stale(self):
        self.assertEqual(_derive_edge_status("c", "b", self.devices), "stale")

    def test_target_offline_stale(self):
        self.assertEqual(_derive_edge_status("a", "c", self.devices), "stale")

    def test_both_offline_stale(self):
        self.assertEqual(_derive_edge_status("c", "c", self.devices), "stale")

    def test_not_reachable_stale(self):
        self.assertEqual(_derive_edge_status("d", "a", self.devices), "stale")

    def test_unknown_stale(self):
        self.assertEqual(_derive_edge_status("f", "a", self.devices), "stale")

    def test_recovery_becomes_active(self):
        devices = dict(self.devices)
        devices["c"] = _device("c", STATUS_ONLINE)
        self.assertEqual(_derive_edge_status("c", "b", devices), "active")

    def test_missing_endpoint_stale(self):
        self.assertEqual(
            _derive_edge_status("a", "neighbor_unknown", self.devices),
            "stale",
        )

    def test_both_missing_endpoints_stale(self):
        self.assertEqual(
            _derive_edge_status("endpoint_x", "neighbor_y", self.devices),
            "stale",
        )

    def test_stale_edge_kept_and_not_animated(self):
        devices = {
            "a": _device("a", STATUS_ONLINE),
            "b": _device("b", "Offline"),
        }
        result = _apply_edge_statuses([_edge("a", "b")], devices, live_only=False)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "stale")
        self.assertFalse(result[0]["animated"])

    def test_active_edge_keeps_animated(self):
        devices = {
            "a": _device("a", STATUS_ONLINE),
            "b": _device("b", STATUS_ONLINE),
        }
        result = _apply_edge_statuses([_edge("a", "b")], devices, live_only=False)
        self.assertEqual(result[0]["status"], "active")
        self.assertTrue(result[0]["animated"])

    def test_preserves_existing_edge_fields(self):
        devices = {
            "a": _device("a", STATUS_ONLINE),
            "b": _device("b", STATUS_ONLINE),
        }
        result = _apply_edge_statuses([_edge("a", "b")], devices, live_only=False)
        self.assertEqual(result[0]["sourcePort"], "Gi1/0/1")
        self.assertEqual(result[0]["targetPort"], "Gi1/0/2")
        self.assertEqual(result[0]["protocol"], "CDP/LLDP")


class TestLevel2LiveFiltering(unittest.TestCase):
    """Level 2: include only Online↔Online inventory links in the live response."""

    def setUp(self):
        self.devices = {
            "a": _device("a", STATUS_ONLINE),
            "b": _device("b", STATUS_ONLINE),
            "c": _device("c", "Offline"),
            "d": _device("d", STATUS_NOT_REACHABLE),
            "e": _device("e", STATUS_OFFLINE_CRITICAL),
            "f": _device("f", "Unknown"),
        }

    def test_both_online_included(self):
        result = _apply_edge_statuses([_edge("a", "b")], self.devices, live_only=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "active")

    def test_source_offline_excluded(self):
        result = _apply_edge_statuses([_edge("c", "b")], self.devices, live_only=True)
        self.assertEqual(result, [])

    def test_target_offline_excluded(self):
        result = _apply_edge_statuses([_edge("a", "c")], self.devices, live_only=True)
        self.assertEqual(result, [])

    def test_both_offline_excluded(self):
        result = _apply_edge_statuses([_edge("c", "c")], self.devices, live_only=True)
        self.assertEqual(result, [])

    def test_not_reachable_excluded(self):
        result = _apply_edge_statuses([_edge("d", "a")], self.devices, live_only=True)
        self.assertEqual(result, [])

    def test_offline_critical_excluded(self):
        result = _apply_edge_statuses([_edge("e", "a")], self.devices, live_only=True)
        self.assertEqual(result, [])

    def test_unknown_excluded(self):
        result = _apply_edge_statuses([_edge("f", "a")], self.devices, live_only=True)
        self.assertEqual(result, [])

    def test_recovery_includes_edge_again(self):
        devices = dict(self.devices)
        excluded = _apply_edge_statuses([_edge("c", "b")], devices, live_only=True)
        self.assertEqual(excluded, [])
        devices["c"] = _device("c", STATUS_ONLINE)
        included = _apply_edge_statuses([_edge("c", "b")], devices, live_only=True)
        self.assertEqual(len(included), 1)
        self.assertEqual(included[0]["status"], "active")

    def test_missing_endpoint_excluded(self):
        result = _apply_edge_statuses(
            [_edge("a", "neighbor_unknown")],
            self.devices,
            live_only=True,
        )
        self.assertEqual(result, [])

    def test_synthetic_endpoint_not_active(self):
        result = _apply_edge_statuses(
            [_edge("a", "endpoint_a_Gi1/0/1")],
            self.devices,
            live_only=True,
        )
        self.assertEqual(result, [])

    def test_active_fields_unchanged(self):
        result = _apply_edge_statuses([_edge("a", "b")], self.devices, live_only=True)
        self.assertEqual(result[0]["sourcePort"], "Gi1/0/1")
        self.assertEqual(result[0]["targetPort"], "Gi1/0/2")
        self.assertEqual(result[0]["protocol"], "CDP/LLDP")
        self.assertTrue(result[0]["animated"])

    def test_level1_and_level2_share_status_source(self):
        """Same device map drives both Level 1 stale marking and Level 2 filtering."""
        devices = {
            "a": _device("a", STATUS_ONLINE),
            "b": _device("b", "Offline"),
        }
        level1 = _apply_edge_statuses([_edge("a", "b")], devices, live_only=False)
        level2 = _apply_edge_statuses([_edge("a", "b")], devices, live_only=True)
        self.assertEqual(level1[0]["status"], "stale")
        self.assertEqual(level2, [])
        self.assertFalse(_is_known_device_online("b", devices))


class TestPruneSyntheticNodes(unittest.TestCase):
    def test_prunes_orphan_synthetic_nodes(self):
        nodes = [
            {"id": "a", "label": "sw-a"},
            {"id": "neighbor_x", "label": "orphan"},
            {"id": "endpoint_a_Gi1", "label": "port"},
            {"id": "b", "label": "sw-b"},
        ]
        edges = [{"source": "a", "target": "b"}]
        pruned = _prune_unconnected_synthetic_nodes(nodes, edges)
        ids = {n["id"] for n in pruned}
        self.assertEqual(ids, {"a", "b"})

    def test_keeps_connected_synthetic_nodes(self):
        nodes = [
            {"id": "a", "label": "sw-a"},
            {"id": "neighbor_x", "label": "nbr"},
        ]
        edges = [{"source": "a", "target": "neighbor_x"}]
        pruned = _prune_unconnected_synthetic_nodes(nodes, edges)
        ids = {n["id"] for n in pruned}
        self.assertEqual(ids, {"a", "neighbor_x"})


if __name__ == "__main__":
    unittest.main()
