from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from tests.app_bootstrap import load_test_app
from utils.auth import create_access_token, hash_password

app = load_test_app()


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, field: str, direction: int = 1) -> list[dict]:
        reverse = direction == -1
        return sorted(
            list(self._docs),
            key=lambda doc: str(doc.get(field, "")),
            reverse=reverse,
        )


class _UsersCollection:
    def __init__(self, docs):
        self.docs = [copy.deepcopy(doc) for doc in docs]

    def _matches(self, doc, query):
        if not query:
            return True
        for key, value in query.items():
            if key == "$or":
                if not any(self._matches(doc, branch) for branch in value):
                    return False
                continue
            if isinstance(value, dict):
                if "$ne" in value:
                    if doc.get(key) == value["$ne"]:
                        return False
                    continue
                if "$exists" in value:
                    exists = key in doc
                    if exists != bool(value["$exists"]):
                        return False
                    continue
            if doc.get(key) != value:
                return False
        return True

    def _project(self, doc, projection=None):
        if not projection:
            return copy.deepcopy(doc)
        result = {"_id": doc["_id"]} if projection.get("_id", 1) else {}
        for key, include in projection.items():
            if key == "_id" or not include:
                continue
            if key in doc:
                result[key] = doc[key]
        return result

    def find_one(self, query=None, projection=None):
        for doc in self.docs:
            if self._matches(doc, query or {}):
                return self._project(doc, projection)
        return None

    def find(self, query=None):
        docs = [copy.deepcopy(doc) for doc in self.docs if self._matches(doc, query or {})]
        return _Cursor(docs)

    def count_documents(self, query):
        return sum(1 for doc in self.docs if self._matches(doc, query))

    def insert_one(self, doc):
        if any(existing.get("username") == doc.get("username") for existing in self.docs):
            raise DuplicateKeyError("duplicate username")
        inserted = copy.deepcopy(doc)
        inserted.setdefault("_id", ObjectId())
        self.docs.append(inserted)
        return SimpleNamespace(inserted_id=inserted["_id"])

    def update_one(self, query, update_doc):
        target = self.find_one(query)
        if not target:
            return SimpleNamespace(matched_count=0, modified_count=0)
        for doc in self.docs:
            if doc["_id"] == target["_id"]:
                updates = update_doc.get("$set", {})
                if (
                    "username" in updates
                    and any(
                        existing["_id"] != doc["_id"] and existing.get("username") == updates["username"]
                        for existing in self.docs
                    )
                ):
                    raise DuplicateKeyError("duplicate username")
                doc.update(copy.deepcopy(updates))
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    def delete_one(self, query):
        before = len(self.docs)
        self.docs = [doc for doc in self.docs if not self._matches(doc, query)]
        deleted = before - len(self.docs)
        return SimpleNamespace(deleted_count=deleted)


class _FakeDb:
    def __init__(self, users):
        self.users = _UsersCollection(users)


class UserManagementRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.admin_id = ObjectId()
        self.user_id = ObjectId()
        self.now = ObjectId().generation_time

    def _make_db(self, users=None):
        base_users = users or [
            {
                "_id": self.admin_id,
                "username": "admin",
                "passwordHash": hash_password("StrongAdminPass1"),
                "role": "admin",
                "active": True,
                "mustChangePassword": False,
                "createdAt": self.now,
                "updatedAt": self.now,
            },
            {
                "_id": self.user_id,
                "username": "user1",
                "passwordHash": hash_password("StrongUserPass1"),
                "role": "user",
                "active": True,
                "mustChangePassword": False,
                "createdAt": self.now,
                "updatedAt": self.now,
            },
        ]
        return _FakeDb(base_users)

    def _auth_headers(self, *, role="admin", username="admin", user_id=None):
        token = create_access_token(
            user_id=str(user_id or self.admin_id),
            username=username,
            role=role,
        )
        return {"Authorization": f"Bearer {token}"}

    def _patch_dbs(self, db):
        return (
            patch("routes.auth_routes.db", db),
            patch("config.database.db", db),
            patch("routes.auth_routes.log_audit"),
        )

    def test_non_admin_cannot_list_create_update_or_delete_users(self):
        db = self._make_db()
        patchers = self._patch_dbs(db)
        with patchers[0], patchers[1], patchers[2]:
            headers = self._auth_headers(role="user", username="user1", user_id=self.user_id)
            list_res = self.client.get("/api/users", headers=headers)
            create_res = self.client.post(
                "/api/users",
                json={"username": "newuser", "password": "StrongPass1", "role": "user", "active": True},
                headers=headers,
            )
            update_res = self.client.put(
                f"/api/users/{self.user_id}",
                json={"role": "admin"},
                headers=headers,
            )
            delete_res = self.client.delete(f"/api/users/{self.user_id}", headers=headers)

        self.assertEqual(list_res.status_code, 403)
        self.assertEqual(create_res.status_code, 403)
        self.assertEqual(update_res.status_code, 403)
        self.assertEqual(delete_res.status_code, 403)

    def test_create_user_requires_all_required_fields(self):
        db = self._make_db()
        patchers = self._patch_dbs(db)
        with patchers[0], patchers[1], patchers[2]:
            res = self.client.post(
                "/api/users",
                json={"username": "newuser", "password": "StrongPass1", "role": "user"},
                headers=self._auth_headers(),
            )

        self.assertEqual(res.status_code, 400)
        self.assertIn("Active status is required", res.get_json()["message"])

    def test_create_user_rejects_invalid_role(self):
        db = self._make_db()
        patchers = self._patch_dbs(db)
        with patchers[0], patchers[1], patchers[2]:
            res = self.client.post(
                "/api/users",
                json={"username": "newuser", "password": "StrongPass1", "role": "viewer", "active": True},
                headers=self._auth_headers(),
            )

        self.assertEqual(res.status_code, 400)
        self.assertIn("Invalid role", res.get_json()["message"])

    def test_create_user_rejects_duplicate_username(self):
        db = self._make_db()
        patchers = self._patch_dbs(db)
        with patchers[0], patchers[1], patchers[2]:
            res = self.client.post(
                "/api/users",
                json={"username": "admin", "password": "StrongPass1", "role": "user", "active": True},
                headers=self._auth_headers(),
            )

        self.assertEqual(res.status_code, 409)
        self.assertIn("already taken", res.get_json()["message"])

    def test_disable_last_active_admin_rejected(self):
        db = self._make_db(users=[
            {
                "_id": self.admin_id,
                "username": "admin",
                "passwordHash": hash_password("StrongAdminPass1"),
                "role": "admin",
                "active": True,
                "mustChangePassword": False,
                "createdAt": self.now,
                "updatedAt": self.now,
            }
        ])
        patchers = self._patch_dbs(db)
        with patchers[0], patchers[1], patchers[2]:
            res = self.client.put(
                f"/api/users/{self.admin_id}",
                json={"active": False},
                headers=self._auth_headers(user_id=self.admin_id),
            )

        self.assertEqual(res.status_code, 400)
        self.assertIn("last active administrator", res.get_json()["message"])

    def test_delete_last_active_admin_rejected(self):
        db = self._make_db(users=[
            {
                "_id": self.admin_id,
                "username": "admin",
                "passwordHash": hash_password("StrongAdminPass1"),
                "role": "admin",
                "active": True,
                "mustChangePassword": False,
                "createdAt": self.now,
                "updatedAt": self.now,
            }
        ])
        patchers = self._patch_dbs(db)
        with patchers[0], patchers[1], patchers[2]:
            res = self.client.delete(
                f"/api/users/{self.admin_id}",
                headers=self._auth_headers(user_id=self.admin_id),
            )

        self.assertEqual(res.status_code, 400)
        self.assertIn("last active administrator", res.get_json()["message"])

    def test_demote_last_active_admin_rejected(self):
        db = self._make_db(users=[
            {
                "_id": self.admin_id,
                "username": "admin",
                "passwordHash": hash_password("StrongAdminPass1"),
                "role": "admin",
                "active": True,
                "mustChangePassword": False,
                "createdAt": self.now,
                "updatedAt": self.now,
            }
        ])
        patchers = self._patch_dbs(db)
        with patchers[0], patchers[1], patchers[2]:
            res = self.client.put(
                f"/api/users/{self.admin_id}",
                json={"role": "user"},
                headers=self._auth_headers(user_id=self.admin_id),
            )

        self.assertEqual(res.status_code, 400)
        self.assertIn("last active administrator", res.get_json()["message"])

    @patch("routes.auth_routes.ensure_default_admin")
    @patch("routes.auth_routes.clear_login_failures")
    @patch("routes.auth_routes.record_login_failure")
    @patch("routes.auth_routes.check_login_allowed")
    def test_disabled_user_cannot_log_in(
        self,
        mock_allowed,
        mock_record_failure,
        mock_clear_failures,
        _mock_ensure_default,
    ):
        mock_allowed.return_value = (True, 0)
        mock_record_failure.return_value = {"locked": False}
        db = self._make_db(users=[
            {
                "_id": self.admin_id,
                "username": "admin",
                "passwordHash": hash_password("StrongAdminPass1"),
                "role": "admin",
                "active": False,
                "mustChangePassword": False,
                "createdAt": self.now,
                "updatedAt": self.now,
            }
        ])
        with patch("routes.auth_routes.db", db):
            res = self.client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "StrongAdminPass1"},
            )

        self.assertEqual(res.status_code, 403)
        self.assertIn("disabled", res.get_json()["message"].lower())
        mock_clear_failures.assert_not_called()

    def test_disabled_admin_token_cannot_access_protected_route(self):
        db = self._make_db(users=[
            {
                "_id": self.admin_id,
                "username": "admin",
                "passwordHash": hash_password("StrongAdminPass1"),
                "role": "admin",
                "active": False,
                "mustChangePassword": False,
                "createdAt": self.now,
                "updatedAt": self.now,
            }
        ])
        patchers = self._patch_dbs(db)
        with patchers[0], patchers[1], patchers[2]:
            res = self.client.get(
                "/api/users",
                headers=self._auth_headers(user_id=self.admin_id),
            )

        self.assertEqual(res.status_code, 403)
        self.assertIn("disabled", res.get_json()["message"].lower())


if __name__ == "__main__":
    unittest.main()
