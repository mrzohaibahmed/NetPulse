"""Unit tests for topology edge status derivation."""

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
)


def _device(device_id: str, status: str) -> dict:
    return {
        "_id": device_id,
        "hostname": f"sw-{device_id}",
        "ipAddress": f"10.0.0.{device_id[-1]}",
        "status": status,
    }


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


class TestDeriveEdgeStatus(unittest.TestCase):
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


class TestApplyEdgeStatuses(unittest.TestCase):
    def test_stale_edge_not_animated(self):
        devices = {
            "a": _device("a", STATUS_ONLINE),
            "b": _device("b", "Offline"),
        }
        edges = [
            {
                "id": "edge_a_b",
                "source": "a",
                "target": "b",
                "animated": True,
            }
        ]
        result = _apply_edge_statuses(edges, devices)
        self.assertEqual(result[0]["status"], "stale")
        self.assertFalse(result[0]["animated"])

    def test_active_edge_keeps_animated(self):
        devices = {
            "a": _device("a", STATUS_ONLINE),
            "b": _device("b", STATUS_ONLINE),
        }
        edges = [
            {
                "id": "edge_a_b",
                "source": "a",
                "target": "b",
                "animated": True,
            }
        ]
        result = _apply_edge_statuses(edges, devices)
        self.assertEqual(result[0]["status"], "active")
        self.assertTrue(result[0]["animated"])

    def test_preserves_existing_edge_fields(self):
        devices = {
            "a": _device("a", STATUS_ONLINE),
            "b": _device("b", STATUS_ONLINE),
        }
        edges = [
            {
                "id": "edge_a_b",
                "source": "a",
                "target": "b",
                "sourcePort": "Gi1/0/1",
                "targetPort": "Gi1/0/2",
                "protocol": "CDP/LLDP",
                "animated": True,
            }
        ]
        result = _apply_edge_statuses(edges, devices)
        self.assertEqual(result[0]["sourcePort"], "Gi1/0/1")
        self.assertEqual(result[0]["targetPort"], "Gi1/0/2")
        self.assertEqual(result[0]["protocol"], "CDP/LLDP")


if __name__ == "__main__":
    unittest.main()
