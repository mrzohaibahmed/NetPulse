from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from pymongo.errors import OperationFailure

from services.retention_service import (
    DATA_TTL_TARGETS,
    RetentionTtlSyncError,
    _ensure_ttl_group,
    ensure_retention_ttl_indexes,
    retention_ttl_results_have_errors,
)


class _FakeCollection:
    def __init__(self, indexes: list[dict] | None = None):
        self.indexes = list(indexes or [])
        self.dropped: list[str] = []
        self.created: list[dict] = []
        self.commands: list[dict] = []

    def list_indexes(self):
        return iter(self.indexes)

    def drop_index(self, name: str) -> None:
        self.dropped.append(name)
        self.indexes = [idx for idx in self.indexes if idx.get("name") != name]

    def create_index(self, keys, **kwargs):
        self.created.append({"keys": keys, **kwargs})
        self.indexes.append(
            {
                "name": kwargs["name"],
                "key": dict(keys),
                "expireAfterSeconds": kwargs["expireAfterSeconds"],
            }
        )
        return kwargs["name"]

    def apply_collmod(self, cmd: dict) -> None:
        self.commands.append(cmd)
        key_pattern = cmd["index"]["keyPattern"]
        for idx in self.indexes:
            if idx.get("key") == key_pattern:
                idx["expireAfterSeconds"] = cmd["index"]["expireAfterSeconds"]


class RetentionTtlGroupTests(unittest.TestCase):
    def _run_group(self, coll: _FakeCollection, days: int) -> dict:
        results: dict = {"indexes": {}}
        with patch("services.retention_service.db") as mock_db:
            mock_db.__getitem__.return_value = coll
            mock_db.command = MagicMock(side_effect=coll.apply_collmod)
            _ensure_ttl_group(DATA_TTL_TARGETS[:1], days, results)
        return results

    def test_existing_ttl_correct_is_unchanged(self):
        coll = _FakeCollection(
            [
                {
                    "name": "idx_interface_stats_timestamp_ttl",
                    "key": {"timestamp": 1},
                    "expireAfterSeconds": 604800,
                }
            ]
        )
        results = self._run_group(coll, 7)
        self.assertEqual(results["indexes"]["interface_stats"]["status"], "unchanged")
        self.assertEqual(coll.dropped, [])
        self.assertEqual(coll.created, [])
        self.assertEqual(coll.commands, [])

    def test_existing_ttl_incorrect_uses_collmod_not_drop_or_create(self):
        coll = _FakeCollection(
            [
                {
                    "name": "idx_interface_stats_timestamp_ttl",
                    "key": {"timestamp": 1},
                    "expireAfterSeconds": 604800,
                }
            ]
        )
        results = self._run_group(coll, 1)

        self.assertEqual(results["indexes"]["interface_stats"]["status"], "updated")
        self.assertEqual(coll.dropped, [])
        self.assertEqual(coll.created, [])
        self.assertEqual(len(coll.commands), 1)
        self.assertEqual(coll.commands[0]["collMod"], "interface_stats")
        self.assertEqual(coll.commands[0]["index"]["expireAfterSeconds"], 86400)

    def test_collmod_failure_returns_error_without_drop(self):
        coll = _FakeCollection(
            [
                {
                    "name": "idx_interface_stats_timestamp_ttl",
                    "key": {"timestamp": 1},
                    "expireAfterSeconds": 604800,
                }
            ]
        )

        def _fail_command(cmd):
            coll.commands.append(cmd)
            raise OperationFailure("collMod failed")

        results: dict = {"indexes": {}}
        with patch("services.retention_service.db") as mock_db:
            mock_db.__getitem__.return_value = coll
            mock_db.command = MagicMock(side_effect=_fail_command)
            _ensure_ttl_group(DATA_TTL_TARGETS[:1], 1, results)

        self.assertEqual(results["indexes"]["interface_stats"]["status"], "error")
        self.assertEqual(coll.dropped, [])
        self.assertEqual(coll.created, [])
        self.assertEqual(
            coll.indexes[0]["expireAfterSeconds"],
            604800,
        )

    def test_collmod_success_but_verification_fails_reports_error(self):
        coll = _FakeCollection(
            [
                {
                    "name": "idx_interface_stats_timestamp_ttl",
                    "key": {"timestamp": 1},
                    "expireAfterSeconds": 604800,
                }
            ]
        )

        def _collmod_without_update(cmd):
            coll.commands.append(cmd)
            # Simulate collMod that does not change list_indexes output.

        results: dict = {"indexes": {}}
        with patch("services.retention_service.db") as mock_db:
            mock_db.__getitem__.return_value = coll
            mock_db.command = MagicMock(side_effect=_collmod_without_update)
            _ensure_ttl_group(DATA_TTL_TARGETS[:1], 1, results)

        self.assertEqual(results["indexes"]["interface_stats"]["status"], "error")
        self.assertEqual(coll.dropped, [])
        self.assertEqual(coll.created, [])

    def test_missing_index_creates_and_verifies(self):
        coll = _FakeCollection()
        results = self._run_group(coll, 1)
        self.assertEqual(results["indexes"]["interface_stats"]["status"], "created")
        self.assertEqual(len(coll.created), 1)
        self.assertEqual(coll.dropped, [])
        self.assertEqual(coll.commands, [])

    def test_missing_index_create_failure_reports_error(self):
        coll = _FakeCollection()

        def _fail_create(*args, **kwargs):
            raise OperationFailure("create failed")

        coll.create_index = MagicMock(side_effect=_fail_create)  # type: ignore[method-assign]

        results: dict = {"indexes": {}}
        with patch("services.retention_service.db") as mock_db:
            mock_db.__getitem__.return_value = coll
            mock_db.command = MagicMock()
            _ensure_ttl_group(DATA_TTL_TARGETS[:1], 1, results)

        self.assertEqual(results["indexes"]["interface_stats"]["status"], "error")
        self.assertEqual(coll.dropped, [])

    def test_partial_failure_across_collections(self):
        good = _FakeCollection(
            [
                {
                    "name": "idx_interface_stats_timestamp_ttl",
                    "key": {"timestamp": 1},
                    "expireAfterSeconds": 604800,
                }
            ]
        )
        bad = _FakeCollection(
            [
                {
                    "name": "idx_eligibility_timestamp_ttl",
                    "key": {"timestamp": 1},
                    "expireAfterSeconds": 604800,
                }
            ]
        )

        def _get_collection(name):
            return good if name == "interface_stats" else bad

        def _bad_command(cmd):
            if cmd["collMod"] == "eligibility_results":
                raise OperationFailure("collMod failed")
            db_ref = good if cmd["collMod"] == "interface_stats" else bad
            db_ref.commands.append(cmd)
            if cmd["collMod"] == "interface_stats":
                db_ref.indexes[0]["expireAfterSeconds"] = cmd["index"]["expireAfterSeconds"]

        results: dict = {"indexes": {}}
        with patch("services.retention_service.db") as mock_db:
            mock_db.__getitem__.side_effect = _get_collection
            mock_db.command = MagicMock(side_effect=_bad_command)
            _ensure_ttl_group(DATA_TTL_TARGETS[:2], 1, results)

        self.assertEqual(results["indexes"]["interface_stats"]["status"], "updated")
        self.assertEqual(results["indexes"]["eligibility_results"]["status"], "error")
        self.assertTrue(retention_ttl_results_have_errors(results))


class RetentionTtlSettingsSyncTests(unittest.TestCase):
    @patch("services.settings_service.db")
    @patch("services.settings_service.get_settings")
    @patch("services.settings_service.ensure_settings")
    def test_settings_save_raises_when_ttl_sync_fails(
        self, mock_ensure, mock_get_settings, mock_db
    ):
        from services.settings_service import update_settings

        mock_ensure.return_value = {"_id": "global"}
        mock_get_settings.side_effect = [
            {"_id": "global", "dataRetentionDays": 7},
            {"_id": "global", "dataRetentionDays": 1},
        ]
        mock_db.settings.update_one.return_value = None

        failing = {
            "indexes": {
                "interface_stats": {"status": "error", "error": "collMod failed"},
            }
        }
        with patch(
            "services.retention_service.ensure_retention_ttl_indexes",
            return_value=failing,
        ):
            with self.assertRaises(RetentionTtlSyncError):
                update_settings({"dataRetentionDays": 1})

        mock_db.settings.update_one.assert_called_once()

    @patch("services.settings_service.db")
    @patch("services.settings_service.get_settings")
    @patch("services.settings_service.ensure_settings")
    def test_settings_save_succeeds_when_ttl_sync_succeeds(
        self, mock_ensure, mock_get_settings, mock_db
    ):
        from services.settings_service import update_settings

        mock_ensure.return_value = {"_id": "global"}
        mock_get_settings.side_effect = [
            {"_id": "global", "dataRetentionDays": 7},
            {"_id": "global", "dataRetentionDays": 1},
        ]
        mock_db.settings.update_one.return_value = None

        ok = {
            "indexes": {
                "interface_stats": {
                    "status": "updated",
                    "expireAfterSeconds": 86400,
                },
            }
        }
        with patch(
            "services.retention_service.ensure_retention_ttl_indexes",
            return_value=ok,
        ):
            updated = update_settings({"dataRetentionDays": 1})

        self.assertEqual(updated["dataRetentionDays"], 1)


class RetentionTtlRouteTests(unittest.TestCase):
    def test_route_returns_409_on_ttl_sync_failure(self):
        from flask import Flask

        from routes.settings_routes import settings_bp

        app = Flask(__name__)
        app.register_blueprint(settings_bp, url_prefix="/api")

        failing = {
            "pingHistoryRetentionDays": 7,
            "dataRetentionDays": 1,
            "incidentRetentionDays": 365,
            "indexes": {
                "interface_stats": {"status": "error", "error": "collMod failed"},
            },
        }
        with patch(
            "routes.settings_routes.update_settings",
            side_effect=RetentionTtlSyncError(failing),
        ):
            with patch(
                "routes.settings_routes.get_public_settings",
                return_value={"dataRetentionDays": 1},
            ):
                with patch("routes.settings_routes.log_audit"):
                    with patch("utils.auth.get_token_from_request", return_value="token"):
                        with patch(
                            "utils.auth.decode_access_token",
                            return_value={
                                "role": "admin",
                                "sub": "507f1f77bcf86cd799439011",
                            },
                        ):
                            with patch(
                                "config.database.db.users.find_one",
                                return_value={"active": True},
                            ):
                                with app.test_client() as client:
                                    resp = client.put(
                                        "/api/settings",
                                        json={"dataRetentionDays": 1},
                                        headers={"Authorization": "Bearer x"},
                                    )

        self.assertEqual(resp.status_code, 409)
        payload = resp.get_json()
        self.assertFalse(payload["success"])
        self.assertIn("TTL synchronization failed", payload["message"])
        self.assertIn("ttlSync", payload)


if __name__ == "__main__":
    unittest.main()
