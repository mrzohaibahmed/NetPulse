from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

from routes.settings_routes import settings_bp
from services.history_deletion_service import (
    ALL_HISTORY_COLLECTIONS,
    INCIDENT_HISTORY_COLLECTIONS,
    PING_HISTORY_COLLECTIONS,
    TELEMETRY_HISTORY_COLLECTIONS,
    HistoryDeletionError,
    delete_history,
    resolve_history_deletion_collections,
)


class _FakeDeleteResult:
    def __init__(self, deleted_count: int):
        self.deleted_count = deleted_count


class _FakeCollection:
    def __init__(self, *, fail: bool = False, deleted_count: int = 0):
        self.fail = fail
        self.deleted_count = deleted_count
        self.filters: list[dict] = []
        self.dropped: list[str] = []
        self.created: list[dict] = []
        self.commands: list[dict] = []

    def delete_many(self, query):
        self.filters.append(query)
        if self.fail:
            raise RuntimeError("delete failed")
        return _FakeDeleteResult(self.deleted_count)

    def drop_index(self, name: str) -> None:
        self.dropped.append(name)

    def create_index(self, keys, **kwargs):
        self.created.append({"keys": keys, **kwargs})

    def list_indexes(self):
        return iter([])


class HistoryDeletionServiceTests(unittest.TestCase):
    def test_ping_scope_maps_only_ping_history(self):
        self.assertEqual(resolve_history_deletion_collections("ping"), PING_HISTORY_COLLECTIONS)

    def test_telemetry_scope_maps_five_collections(self):
        self.assertEqual(
            resolve_history_deletion_collections("telemetry"),
            TELEMETRY_HISTORY_COLLECTIONS,
        )

    def test_incidents_scope_maps_two_collections(self):
        self.assertEqual(
            resolve_history_deletion_collections("incidents"),
            INCIDENT_HISTORY_COLLECTIONS,
        )

    def test_all_scope_maps_eight_collections(self):
        self.assertEqual(resolve_history_deletion_collections("all"), ALL_HISTORY_COLLECTIONS)

    def test_invalid_scope_rejected(self):
        with self.assertRaises(ValueError):
            resolve_history_deletion_collections("devices")

    def test_delete_many_called_for_each_collection(self):
        collections = {
            name: _FakeCollection(deleted_count=10) for name in ALL_HISTORY_COLLECTIONS
        }

        with patch("services.history_deletion_service.db") as mock_db:
            mock_db.__getitem__.side_effect = lambda name: collections[name]
            result = delete_history("all")

        self.assertTrue(result["success"])
        self.assertEqual(result["totalDeleted"], 80)
        for name, coll in collections.items():
            self.assertEqual(coll.filters, [{}])
            self.assertEqual(result["deleted"][name], 10)

    def test_zero_record_deletion_returns_success(self):
        coll = _FakeCollection(deleted_count=0)
        with patch("services.history_deletion_service.db") as mock_db:
            mock_db.__getitem__.return_value = coll
            result = delete_history("ping")

        self.assertTrue(result["success"])
        self.assertEqual(result["deleted"], {"pingHistory": 0})
        self.assertEqual(result["totalDeleted"], 0)

    def test_partial_failure_raises_with_counts(self):
        good = _FakeCollection(deleted_count=5)
        bad = _FakeCollection(fail=True)

        def _get_collection(name):
            if name == "interface_stats":
                return good
            if name == "eligibility_results":
                return bad
            return _FakeCollection(deleted_count=1)

        with patch("services.history_deletion_service.db") as mock_db:
            mock_db.__getitem__.side_effect = _get_collection
            with self.assertRaises(HistoryDeletionError) as ctx:
                delete_history("telemetry")

        results = ctx.exception.results
        self.assertEqual(results["result"], "partial_failure")
        self.assertEqual(results["deleted"]["interface_stats"], 5)
        self.assertIn("eligibility_results", results["failed"])
        self.assertGreater(results["totalDeleted"], 0)

    def test_total_failure_raises_without_success(self):
        coll = _FakeCollection(fail=True)
        with patch("services.history_deletion_service.db") as mock_db:
            mock_db.__getitem__.return_value = coll
            with self.assertRaises(HistoryDeletionError) as ctx:
                delete_history("ping")

        results = ctx.exception.results
        self.assertEqual(results["result"], "failure")
        self.assertEqual(results["totalDeleted"], 0)
        self.assertIn("pingHistory", results["failed"])

    def test_delete_does_not_touch_ttl_helpers(self):
        coll = _FakeCollection(deleted_count=3)
        with patch("services.history_deletion_service.db") as mock_db:
            mock_db.__getitem__.return_value = coll
            with patch(
                "services.retention_service.ensure_retention_ttl_indexes"
            ) as mock_ttl:
                delete_history("ping")

        self.assertEqual(coll.dropped, [])
        self.assertEqual(coll.created, [])
        self.assertEqual(coll.commands, [])
        mock_ttl.assert_not_called()


class HistoryDeletionRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(settings_bp, url_prefix="/api")
        self.client = self.app.test_client()

    def _admin_request(self, *, json_body=None, token="token"):
        return self.client.delete(
            "/api/settings/history",
            json=json_body,
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_unauthenticated_request_rejected(self):
        resp = self.client.delete("/api/settings/history", json={"scope": "ping"})
        self.assertEqual(resp.status_code, 401)

    def test_non_admin_request_returns_403(self):
        with patch("utils.auth.get_token_from_request", return_value="token"):
            with patch(
                "utils.auth.decode_access_token",
                return_value={"role": "user", "sub": "507f1f77bcf86cd799439011"},
            ):
                with patch(
                    "config.database.db.users.find_one",
                    return_value={"active": True},
                ):
                    resp = self._admin_request(json_body={"scope": "ping"})
        self.assertEqual(resp.status_code, 403)

    def test_admin_request_succeeds(self):
        ok = {
            "success": True,
            "scope": "ping",
            "deleted": {"pingHistory": 42},
            "totalDeleted": 42,
            "result": "success",
        }
        with patch("utils.auth.get_token_from_request", return_value="token"):
            with patch(
                "utils.auth.decode_access_token",
                return_value={"role": "admin", "sub": "507f1f77bcf86cd799439011"},
            ):
                with patch(
                    "config.database.db.users.find_one",
                    return_value={"active": True},
                ):
                    with patch("routes.settings_routes.delete_history", return_value=ok):
                        with patch("routes.settings_routes.log_audit") as mock_audit:
                            resp = self._admin_request(json_body={"scope": "ping"})

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["totalDeleted"], 42)
        mock_audit.assert_called_once()
        self.assertEqual(mock_audit.call_args.kwargs["action"], "MANUAL_HISTORY_DELETE")
        self.assertEqual(mock_audit.call_args.kwargs["details"]["result"], "success")

    def test_invalid_scope_rejected(self):
        with patch("utils.auth.get_token_from_request", return_value="token"):
            with patch(
                "utils.auth.decode_access_token",
                return_value={"role": "admin", "sub": "507f1f77bcf86cd799439011"},
            ):
                with patch(
                    "config.database.db.users.find_one",
                    return_value={"active": True},
                ):
                    resp = self._admin_request(json_body={"scope": "devices"})
        self.assertEqual(resp.status_code, 400)

    def test_arbitrary_collection_name_rejected(self):
        with patch("utils.auth.get_token_from_request", return_value="token"):
            with patch(
                "utils.auth.decode_access_token",
                return_value={"role": "admin", "sub": "507f1f77bcf86cd799439011"},
            ):
                with patch(
                    "config.database.db.users.find_one",
                    return_value={"active": True},
                ):
                    resp = self._admin_request(
                        json_body={"scope": "ping", "collection": "devices"}
                    )
        self.assertEqual(resp.status_code, 400)

    def test_partial_failure_returns_500_without_success(self):
        failing = HistoryDeletionError(
            {
                "success": False,
                "scope": "telemetry",
                "result": "partial_failure",
                "deleted": {"interface_stats": 10},
                "failed": {"eligibility_results": "delete failed"},
                "totalDeleted": 10,
            }
        )
        with patch("utils.auth.get_token_from_request", return_value="token"):
            with patch(
                "utils.auth.decode_access_token",
                return_value={"role": "admin", "sub": "507f1f77bcf86cd799439011"},
            ):
                with patch(
                    "config.database.db.users.find_one",
                    return_value={"active": True},
                ):
                    with patch(
                        "routes.settings_routes.delete_history",
                        side_effect=failing,
                    ):
                        with patch("routes.settings_routes.log_audit") as mock_audit:
                            resp = self._admin_request(json_body={"scope": "telemetry"})

        self.assertEqual(resp.status_code, 500)
        payload = resp.get_json()
        self.assertFalse(payload["success"])
        self.assertIn("partially failed", payload["message"])
        self.assertIn("failed", payload)
        self.assertNotIn("traceback", str(payload).lower())
        mock_audit.assert_called_once()
        self.assertEqual(
            mock_audit.call_args.kwargs["details"]["result"],
            "partial_failure",
        )

    def test_zero_deleted_success_message(self):
        ok = {
            "success": True,
            "scope": "ping",
            "deleted": {"pingHistory": 0},
            "totalDeleted": 0,
            "result": "success",
        }
        with patch("utils.auth.get_token_from_request", return_value="token"):
            with patch(
                "utils.auth.decode_access_token",
                return_value={"role": "admin", "sub": "507f1f77bcf86cd799439011"},
            ):
                with patch(
                    "config.database.db.users.find_one",
                    return_value={"active": True},
                ):
                    with patch("routes.settings_routes.delete_history", return_value=ok):
                        with patch("routes.settings_routes.log_audit"):
                            resp = self._admin_request(json_body={"scope": "ping"})

        payload = resp.get_json()
        self.assertTrue(payload["success"])
        self.assertIn("No history records were found", payload["message"])


class HistoryDeletionRegressionTests(unittest.TestCase):
    def test_manual_deletion_never_calls_retention_ttl_sync(self):
        coll = _FakeCollection(deleted_count=1)
        with patch("services.history_deletion_service.db") as mock_db:
            mock_db.__getitem__.return_value = coll
            mock_db.command = MagicMock()
            with patch(
                "services.retention_service._modify_ttl_expire_seconds"
            ) as mock_collmod:
                delete_history("all")

        mock_collmod.assert_not_called()
        mock_db.command.assert_not_called()
        self.assertEqual(coll.dropped, [])
        self.assertEqual(coll.created, [])


if __name__ == "__main__":
    unittest.main()
