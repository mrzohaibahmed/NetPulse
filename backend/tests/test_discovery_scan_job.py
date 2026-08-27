"""Async network scan job: 202 start, progress polling, failure, duplicate guard."""

from __future__ import annotations

import os
import sys
import time
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
_mock_db_module.MAX_SCAN_THREADS = 5
sys.modules["config.database"] = _mock_db_module

from bson import ObjectId
from flask import Flask

from services import discovery_service as ds
from routes.discovery_routes import discovery_bp, scan_networks, scan_progress


def _reset_scan_state() -> None:
    with ds._scan_progress_lock:
        ds._scan_progress.clear()
        ds._active_network_scan_id = None


class StartNetworkScanJobTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_scan_state()

    def tearDown(self) -> None:
        _reset_scan_state()

    def test_start_job_returns_scan_id_and_runs_discover_ips(self) -> None:
        done = {"called": False}

        def fake_discover(ips, scan_id=None):
            done["called"] = True
            done["ips"] = list(ips)
            done["scan_id"] = scan_id
            # Mirror real progress lifecycle lightly for summary attachment.
            ds.begin_scan_progress(scan_id, len(ips))
            ds.finish_scan_progress(scan_id, status="complete")
            return [
                {
                    "ipAddress": ips[0],
                    "status": "Online",
                    "saved": True,
                }
            ]

        with patch.object(ds, "discover_ips", side_effect=fake_discover):
            scan_id = ds.start_network_scan_job(["10.0.0.1"])
            self.assertTrue(ds.is_valid_scan_id(scan_id))

            deadline = time.time() + 5
            while time.time() < deadline and not done["called"]:
                time.sleep(0.05)

            self.assertTrue(done["called"])
            self.assertEqual(done["ips"], ["10.0.0.1"])
            self.assertEqual(done["scan_id"], scan_id)

            deadline = time.time() + 5
            progress = None
            while time.time() < deadline:
                progress = ds.get_scan_progress(scan_id)
                if (
                    progress
                    and progress.get("status") == "complete"
                    and progress.get("summary")
                ):
                    break
                time.sleep(0.05)

            self.assertIsNotNone(progress)
            self.assertEqual(progress["status"], "complete")
            self.assertEqual(progress["summary"]["online"], 1)
            self.assertEqual(progress["summary"]["newlySaved"], 1)
            self.assertNotIn("devices", progress)
            self.assertIsNone(ds.get_active_network_scan_id())

    def test_duplicate_active_scan_raises_conflict(self) -> None:
        with ds._scan_progress_lock:
            sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            ds._active_network_scan_id = sid
            ds._scan_progress[sid] = {
                "status": "running",
                "total": 10,
                "completed": 1,
                "online": 0,
                "newlySaved": 0,
                "startedMonotonic": time.monotonic(),
                "elapsedSeconds": 0.0,
                "error": None,
                "summary": None,
            }

        with self.assertRaises(ds.ActiveNetworkScanError) as ctx:
            ds.start_network_scan_job(["10.0.0.2"])
        self.assertEqual(ctx.exception.scan_id, sid)

    def test_background_exception_marks_failed_and_releases_lock(self) -> None:
        def boom(ips, scan_id=None):
            raise RuntimeError("simulated discover failure with secret path C:\\secrets")

        with patch.object(ds, "discover_ips", side_effect=boom):
            scan_id = ds.start_network_scan_job(["10.0.0.3"])

            deadline = time.time() + 5
            progress = None
            while time.time() < deadline:
                progress = ds.get_scan_progress(scan_id)
                if progress and progress.get("status") == "failed":
                    break
                time.sleep(0.05)

            self.assertIsNotNone(progress)
            self.assertEqual(progress["status"], "failed")
            self.assertTrue(progress.get("error"))
            self.assertNotIn("simulated discover failure", progress["error"])
            self.assertNotIn("C:\\secrets", progress["error"])
            self.assertIsNone(ds.get_active_network_scan_id())


class ScanNetworksRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_scan_state()
        self.app = Flask(__name__)
        self.app.register_blueprint(discovery_bp, url_prefix="/api")
        self.network_id = ObjectId()

    def tearDown(self) -> None:
        _reset_scan_state()

    def test_scan_networks_returns_202_with_scan_id(self) -> None:
        fake_db = MagicMock()
        fake_db.networks.find.return_value = [
            {
                "_id": self.network_id,
                "name": "Lab",
                "enabled": True,
                "scanTargets": "10.1.1.1",
                "cidr": "10.1.1.0/24",
            }
        ]
        fake_db.users.find_one.return_value = {"active": True}

        def fake_discover(ips, scan_id=None):
            ds.begin_scan_progress(scan_id, len(ips))
            ds.finish_scan_progress(scan_id, status="complete")
            return [{"ipAddress": "10.1.1.1", "status": "Offline", "saved": False}]

        with self.app.test_request_context(
            "/api/discovery/scan-networks",
            method="POST",
            json={"scanAllEnabled": True},
        ):
            with (
                patch("routes.discovery_routes.db", fake_db),
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
                patch.object(ds, "discover_ips", side_effect=fake_discover),
                patch(
                    "routes.discovery_routes.parse_scan_targets",
                    return_value=["10.1.1.1"],
                ),
            ):
                response, status_code = scan_networks()

        self.assertEqual(status_code, 202)
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertTrue(ds.is_valid_scan_id(body["scanId"]))
        self.assertEqual(body["status"], "running")

        deadline = time.time() + 5
        progress = None
        while time.time() < deadline:
            progress = ds.get_scan_progress(body["scanId"])
            if progress and progress.get("status") == "complete":
                break
            time.sleep(0.05)
        self.assertIsNotNone(progress)
        self.assertEqual(progress["status"], "complete")

    def test_scan_networks_conflict_returns_409_with_existing_scan_id(self) -> None:
        existing = "11111111-2222-3333-4444-555555555555"
        with ds._scan_progress_lock:
            ds._active_network_scan_id = existing
            ds._scan_progress[existing] = {
                "status": "running",
                "total": 5,
                "completed": 1,
                "online": 0,
                "newlySaved": 0,
                "startedMonotonic": time.monotonic(),
                "elapsedSeconds": 0.1,
                "error": None,
                "summary": None,
            }

        fake_db = MagicMock()
        fake_db.networks.find.return_value = [
            {
                "_id": self.network_id,
                "enabled": True,
                "scanTargets": "10.1.1.1",
            }
        ]
        fake_db.users.find_one.return_value = {"active": True}

        with self.app.test_request_context(
            "/api/discovery/scan-networks",
            method="POST",
            json={"scanAllEnabled": True},
        ):
            with (
                patch("routes.discovery_routes.db", fake_db),
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
                patch(
                    "routes.discovery_routes.parse_scan_targets",
                    return_value=["10.1.1.1"],
                ),
            ):
                response, status_code = scan_networks()

        self.assertEqual(status_code, 409)
        body = response.get_json()
        self.assertEqual(body["scanId"], existing)
        self.assertEqual(body["code"], "scan_in_progress")

    def test_scan_networks_requires_admin(self) -> None:
        fake_db = MagicMock()
        fake_db.users.find_one.return_value = {"active": True}

        with self.app.test_request_context(
            "/api/discovery/scan-networks",
            method="POST",
            json={"scanAllEnabled": True},
        ):
            with (
                patch("routes.discovery_routes.db", fake_db),
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
            ):
                response, status_code = scan_networks()

        self.assertEqual(status_code, 403)

    def test_progress_endpoint_requires_admin(self) -> None:
        fake_db = MagicMock()
        fake_db.users.find_one.return_value = {"active": True}
        scan_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        with self.app.test_request_context(
            f"/api/discovery/scan-progress/{scan_id}",
            method="GET",
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
            ):
                response, status_code = scan_progress(scan_id)

        self.assertEqual(status_code, 403)

    def test_progress_returns_running_state(self) -> None:
        scan_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        ds.begin_scan_progress(scan_id, 4)
        fake_db = MagicMock()
        fake_db.users.find_one.return_value = {"active": True}

        with self.app.test_request_context(
            f"/api/discovery/scan-progress/{scan_id}",
            method="GET",
        ):
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
            ):
                response, status_code = scan_progress(scan_id)

        self.assertEqual(status_code, 200)
        body = response.get_json()
        self.assertEqual(body["progress"]["status"], "running")
        self.assertEqual(body["progress"]["total"], 4)


if __name__ == "__main__":
    unittest.main()
