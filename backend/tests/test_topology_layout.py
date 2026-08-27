"""Topology canvas layout persistence (positions only — not discovery)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault(
    "JWT_SECRET",
    "netpulse-test-jwt-secret-do-not-use-in-production-32c+",
)
os.environ.setdefault("FLASK_DEBUG", "true")
os.environ.setdefault("NETPULSE_ENV", "development")
os.environ.setdefault(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5000",
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_mock_db_module = MagicMock()
_mock_db_module.db = MagicMock()
sys.modules["config.database"] = _mock_db_module

from bson import ObjectId
from flask import Flask

from routes.topology import (
    api_get_topology_layout,
    api_put_topology_layout,
    topology_bp,
)
from services import topology_layout_service as layout_svc


class TopologyLayoutServiceTests(unittest.TestCase):
    def test_validate_layout_payload_accepts_nodes_and_edges(self) -> None:
        nodes, edges = layout_svc.validate_layout_payload({
            "nodes": [
                {"id": "a", "position": {"x": 10, "y": 20}},
                {"id": "b", "position": {"x": 30.5, "y": -4}},
            ],
            "edges": [
                {"id": "e1", "source": "a", "target": "b"},
            ],
        })
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0]["position"]["x"], 10)
        self.assertEqual(edges[0]["source"], "a")

    def test_validate_layout_payload_rejects_bad_position(self) -> None:
        with self.assertRaises(ValueError):
            layout_svc.validate_layout_payload({
                "nodes": [{"id": "a", "position": {"x": "nope", "y": 1}}],
            })

    def test_get_layout_returns_none_when_missing(self) -> None:
        fake_col = MagicMock()
        fake_col.find_one.return_value = None
        with patch.object(layout_svc, "_collection", return_value=fake_col):
            self.assertIsNone(layout_svc.get_layout("full"))

    def test_save_layout_upserts_document(self) -> None:
        fake_col = MagicMock()
        with patch.object(layout_svc, "_collection", return_value=fake_col):
            result = layout_svc.save_layout(
                "full",
                [{"id": "sw1", "position": {"x": 1, "y": 2}}],
                [],
            )
        fake_col.replace_one.assert_called_once()
        args, kwargs = fake_col.replace_one.call_args
        self.assertEqual(args[0], {"_id": "full"})
        self.assertTrue(kwargs.get("upsert"))
        self.assertEqual(result["viewKey"], "full")
        self.assertEqual(result["nodes"][0]["id"], "sw1")


class TopologyLayoutRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask(__name__)
        self.app.register_blueprint(topology_bp, url_prefix="/api/topology")

    def test_get_layout_returns_null_when_empty(self) -> None:
        fake_db = MagicMock()
        fake_db.users.find_one.return_value = {"active": True}
        with self.app.test_request_context("/api/topology/layout?view=full", method="GET"):
            with (
                patch("utils.auth.get_token_from_request", return_value="token"),
                patch(
                    "utils.auth.decode_access_token",
                    return_value={
                        "role": "admin",
                        "sub": str(ObjectId()),
                        "username": "admin",
                    },
                ),
                patch("config.database.db", fake_db),
                patch("routes.topology.get_layout", return_value=None),
            ):
                response, status = api_get_topology_layout()
        self.assertEqual(status, 200)
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertIsNone(body["layout"])

    def test_put_layout_persists_and_returns_layout(self) -> None:
        fake_db = MagicMock()
        fake_db.users.find_one.return_value = {"active": True}
        saved = {
            "viewKey": "full",
            "nodes": [{"id": "n1", "position": {"x": 5, "y": 6}}],
            "edges": [],
            "updatedAt": "2026-01-01T00:00:00Z",
        }
        with self.app.test_request_context(
            "/api/topology/layout",
            method="PUT",
            json={
                "viewKey": "full",
                "nodes": [{"id": "n1", "position": {"x": 5, "y": 6}}],
                "edges": [],
            },
        ):
            with (
                patch("utils.auth.get_token_from_request", return_value="token"),
                patch(
                    "utils.auth.decode_access_token",
                    return_value={
                        "role": "user",
                        "sub": str(ObjectId()),
                        "username": "user1",
                    },
                ),
                patch("config.database.db", fake_db),
                patch("routes.topology.save_layout", return_value=saved) as save_fn,
            ):
                response, status = api_put_topology_layout()
        self.assertEqual(status, 200)
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["layout"]["viewKey"], "full")
        save_fn.assert_called_once()

    def test_put_layout_requires_auth(self) -> None:
        with self.app.test_request_context(
            "/api/topology/layout",
            method="PUT",
            json={"viewKey": "full", "nodes": []},
        ):
            with patch("utils.auth.get_token_from_request", return_value=None):
                response, status = api_put_topology_layout()
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
