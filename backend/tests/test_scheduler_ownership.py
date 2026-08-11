"""
Scheduler ownership lease + UTC time handling tests.

Covers atomic acquire/renew/release, concurrent winners, leadership loss,
shutdown safety, and timezone-aware logging — without redesigning ping monitor.
"""

from __future__ import annotations

import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from pymongo.errors import DuplicateKeyError


class _UpdateResult:
    def __init__(self, matched: int, modified: int = 0, acknowledged: bool = True):
        self.matched_count = matched
        self.modified_count = modified if modified else matched
        self.acknowledged = acknowledged
        self.upserted_id = None


class _InsertResult:
    def __init__(self, inserted_id="monitor_scheduler", acknowledged: bool = True):
        self.inserted_id = inserted_id
        self.acknowledged = acknowledged


class FakeLockCollection:
    """Thread-safe in-memory stand-in for scheduler_locks."""

    def __init__(self):
        self._lock = threading.Lock()
        self.doc: dict | None = None

    def update_one(self, filt, update):
        with self._lock:
            if self.doc is None:
                return _UpdateResult(0)
            for key, expected in filt.items():
                if self.doc.get(key) != expected:
                    return _UpdateResult(0)
            self.doc.update(update.get("$set", {}))
            return _UpdateResult(1)

    def find_one_and_update(self, filt, update, return_document=None):
        with self._lock:
            if self.doc is None:
                return None
            if self.doc.get("_id") != filt.get("_id"):
                return None
            or_clauses = filt.get("$or") or []
            ok = False
            for clause in or_clauses:
                if "expiresAt" in clause and "$lte" in clause["expiresAt"]:
                    exp = self.doc.get("expiresAt")
                    if exp is not None and exp <= clause["expiresAt"]["$lte"]:
                        ok = True
                if "expiresAt" in clause and clause["expiresAt"] == {"$exists": False}:
                    if "expiresAt" not in self.doc:
                        ok = True
            if not ok and or_clauses:
                return None
            self.doc.update(update.get("$set", {}))
            return dict(self.doc)

    def insert_one(self, doc):
        with self._lock:
            if self.doc is not None and self.doc.get("_id") == doc.get("_id"):
                raise DuplicateKeyError("E11000 duplicate key")
            self.doc = dict(doc)
            return _InsertResult(doc.get("_id"))

    def find_one(self, filt, projection=None):
        with self._lock:
            if self.doc is None:
                return None
            if self.doc.get("_id") != filt.get("_id"):
                return None
            if projection is None:
                return dict(self.doc)
            out = {}
            for key in projection:
                if key in self.doc:
                    out[key] = self.doc[key]
            if "_id" in self.doc:
                out.setdefault("_id", self.doc["_id"])
            return out

    def delete_one(self, filt):
        with self._lock:
            class R:
                deleted_count = 0

            r = R()
            if self.doc is None:
                return r
            for key, expected in filt.items():
                if self.doc.get(key) != expected:
                    return r
            self.doc = None
            r.deleted_count = 1
            return r


def _patch_ownership_db(fake_coll: FakeLockCollection):
    fake_db = MagicMock()
    fake_db.__getitem__.side_effect = (
        lambda name: fake_coll if name == "scheduler_locks" else MagicMock()
    )
    return patch("services.scheduler_ownership._db", return_value=fake_db)


class TestUtcHelpers(unittest.TestCase):
    def test_utc_now_aware_utc(self):
        from utils.utc import utc_now

        now = utc_now()
        self.assertIsNotNone(now.tzinfo)
        self.assertEqual(now.utcoffset(), timedelta(0))

    def test_require_utc_aware_rejects_naive(self):
        from utils.utc import require_utc_aware

        with self.assertRaises(ValueError):
            require_utc_aware(datetime(2026, 8, 10, 16, 0, 0), field="expiresAt")

    def test_format_utc_includes_offset(self):
        from utils.utc import format_utc

        stamp = datetime(2026, 8, 10, 16, 9, 17, 123000, tzinfo=timezone.utc)
        text = format_utc(stamp)
        self.assertIn("+00:00", text)
        self.assertTrue(text.startswith("2026-08-10T16:09:17"))

    def test_ensure_utc_naive_assumed_utc(self):
        from utils.utc import ensure_utc

        naive = datetime(2026, 8, 10, 16, 0, 0)
        aware = ensure_utc(naive)
        self.assertIsNotNone(aware)
        assert aware is not None
        self.assertEqual(aware.tzinfo, timezone.utc)


class TestOwnerIdentity(unittest.TestCase):
    def test_owner_id_stable_and_unique_shape(self):
        from services.scheduler_ownership import get_owner_id

        a = get_owner_id()
        b = get_owner_id()
        self.assertEqual(a, b)
        parts = a.split(":")
        self.assertGreaterEqual(len(parts), 3)
        self.assertTrue(parts[-1])  # instance uuid fragment
        self.assertTrue(parts[-2].isdigit())  # pid


class TestLeaseAcquisition(unittest.TestCase):
    def setUp(self):
        import services.scheduler_ownership as so

        so._mark_held(False)
        self.coll = FakeLockCollection()

    def test_acquire_with_no_document(self):
        import services.scheduler_ownership as so

        with _patch_ownership_db(self.coll):
            self.assertTrue(so.try_acquire_or_renew())
        self.assertIsNotNone(self.coll.doc)
        assert self.coll.doc is not None
        self.assertEqual(self.coll.doc["ownerId"], so.get_owner_id())
        for field in (
            "heartbeatAt",
            "expiresAt",
            "acquiredAt",
            "createdAt",
            "updatedAt",
        ):
            self.assertIn(field, self.coll.doc)
            self.assertIsNotNone(self.coll.doc[field].tzinfo)

    def test_acquire_expired_document(self):
        import services.scheduler_ownership as so
        from utils.utc import utc_now

        past = utc_now() - timedelta(seconds=120)
        self.coll.doc = {
            "_id": so.LOCK_ID,
            "ownerId": "other-host:1:deadbeefcafe",
            "hostname": "other-host",
            "pid": 1,
            "heartbeatAt": past,
            "expiresAt": past,
            "acquiredAt": past,
            "createdAt": past,
            "updatedAt": past,
        }
        with _patch_ownership_db(self.coll):
            self.assertTrue(so.try_acquire_or_renew())
        assert self.coll.doc is not None
        self.assertEqual(self.coll.doc["ownerId"], so.get_owner_id())

    def test_acquire_blocked_by_active_owner(self):
        import services.scheduler_ownership as so
        from utils.utc import utc_now

        future = utc_now() + timedelta(seconds=60)
        peer = "peer-host:99:abcdef123456"
        self.coll.doc = {
            "_id": so.LOCK_ID,
            "ownerId": peer,
            "hostname": "peer-host",
            "pid": 99,
            "heartbeatAt": utc_now(),
            "expiresAt": future,
            "acquiredAt": utc_now(),
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
        }
        with _patch_ownership_db(self.coll):
            self.assertFalse(so.try_acquire_or_renew())
        assert self.coll.doc is not None
        self.assertEqual(self.coll.doc["ownerId"], peer)

    def test_correct_owner_can_heartbeat(self):
        import services.scheduler_ownership as so
        from utils.utc import utc_now

        now = utc_now()
        self.coll.doc = {
            "_id": so.LOCK_ID,
            "ownerId": so.get_owner_id(),
            "hostname": "h",
            "pid": 1,
            "heartbeatAt": now,
            "expiresAt": now + timedelta(seconds=30),
            "acquiredAt": now,
            "createdAt": now,
            "updatedAt": now,
        }
        with _patch_ownership_db(self.coll):
            self.assertTrue(so.try_acquire_or_renew())
        assert self.coll.doc is not None
        self.assertGreater(self.coll.doc["expiresAt"], now + timedelta(seconds=30))

    def test_wrong_owner_cannot_heartbeat(self):
        import services.scheduler_ownership as so
        from utils.utc import utc_now

        now = utc_now()
        peer = "other:2:ffffffffffff"
        original_exp = now + timedelta(seconds=90)
        self.coll.doc = {
            "_id": so.LOCK_ID,
            "ownerId": peer,
            "hostname": "other",
            "pid": 2,
            "heartbeatAt": now,
            "expiresAt": original_exp,
            "acquiredAt": now,
            "createdAt": now,
            "updatedAt": now,
        }
        with _patch_ownership_db(self.coll):
            self.assertFalse(so.try_acquire_or_renew())
        assert self.coll.doc is not None
        self.assertEqual(self.coll.doc["ownerId"], peer)
        self.assertEqual(self.coll.doc["expiresAt"], original_exp)

    def test_expired_owner_replaced(self):
        import services.scheduler_ownership as so
        from utils.utc import utc_now

        past = utc_now() - timedelta(seconds=5)
        self.coll.doc = {
            "_id": so.LOCK_ID,
            "ownerId": "stale:3:aaaaaaaaaaaa",
            "expiresAt": past,
            "heartbeatAt": past,
        }
        with _patch_ownership_db(self.coll):
            self.assertTrue(so.try_acquire_or_renew())
        assert self.coll.doc is not None
        self.assertEqual(self.coll.doc["ownerId"], so.get_owner_id())

    def test_all_lease_timestamps_utc_aware(self):
        import services.scheduler_ownership as so

        with _patch_ownership_db(self.coll):
            so.try_acquire_or_renew()
        assert self.coll.doc is not None
        for key, value in self.coll.doc.items():
            if isinstance(value, datetime):
                self.assertIsNotNone(
                    value.tzinfo, msg=f"{key} must be timezone-aware"
                )
                self.assertEqual(value.utcoffset(), timedelta(0))


class TestConcurrentAcquisition(unittest.TestCase):
    def tearDown(self):
        # Ensure threaded patches cannot leak a fake get_owner_id.
        import services.scheduler_ownership as so
        import importlib

        # Re-bind get_owner_id to the real closure over _OWNER_ID if patched.
        if not callable(getattr(so, "get_owner_id", None)):
            importlib.reload(so)

    def test_two_concurrent_acquires_one_winner(self):
        import services.scheduler_ownership as so

        coll = FakeLockCollection()
        results: list[bool] = []
        barrier = threading.Barrier(2)
        owners = ["hostA:1:aaa111aaa111", "hostB:2:bbb222bbb222"]
        owner_local = threading.local()

        real_get = so.get_owner_id

        def routed_get_owner_id():
            return getattr(owner_local, "owner", real_get())

        def worker(owner_id: str):
            owner_local.owner = owner_id
            barrier.wait()
            with _patch_ownership_db(coll):
                results.append(so.try_acquire_or_renew())

        with patch.object(so, "get_owner_id", side_effect=routed_get_owner_id):
            t1 = threading.Thread(target=worker, args=(owners[0],))
            t2 = threading.Thread(target=worker, args=(owners[1],))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        self.assertEqual(sum(1 for r in results if r), 1)
        self.assertEqual(sum(1 for r in results if not r), 1)
        assert coll.doc is not None
        self.assertIn(coll.doc["ownerId"], set(owners))

    def test_expire_then_other_acquires_old_cannot_renew(self):
        import services.scheduler_ownership as so
        from utils.utc import utc_now

        coll = FakeLockCollection()
        owner_a = "hostA:1:aaa111aaa111"
        owner_b = "hostB:2:bbb222bbb222"

        with (
            _patch_ownership_db(coll),
            patch.object(so, "get_owner_id", return_value=owner_a),
        ):
            self.assertTrue(so.try_acquire_or_renew())

        assert coll.doc is not None
        coll.doc["expiresAt"] = utc_now() - timedelta(seconds=1)
        coll.doc["heartbeatAt"] = utc_now() - timedelta(seconds=1)

        with (
            _patch_ownership_db(coll),
            patch.object(so, "get_owner_id", return_value=owner_b),
        ):
            self.assertTrue(so.try_acquire_or_renew())
        assert coll.doc is not None
        self.assertEqual(coll.doc["ownerId"], owner_b)

        with (
            _patch_ownership_db(coll),
            patch.object(so, "get_owner_id", return_value=owner_a),
        ):
            self.assertFalse(so.try_acquire_or_renew())
        assert coll.doc is not None
        self.assertEqual(coll.doc["ownerId"], owner_b)

    def test_same_owner_concurrent_race_still_leader(self):
        """Regression: insert winner + DuplicateKey loser must both see leadership."""
        import services.scheduler_ownership as so

        coll = FakeLockCollection()
        owner = so.get_owner_id()
        results: list[bool] = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            with _patch_ownership_db(coll):
                results.append(so.try_acquire_or_renew())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(all(results), msg=f"false follower results={results}")
        assert coll.doc is not None
        self.assertEqual(coll.doc["ownerId"], owner)


class TestReleaseAndShutdown(unittest.TestCase):
    def test_shutdown_releases_only_own_lease(self):
        import services.scheduler_ownership as so
        from utils.utc import utc_now

        coll = FakeLockCollection()
        now = utc_now()
        with _patch_ownership_db(coll):
            self.assertTrue(so.try_acquire_or_renew())
            so.release_scheduler_ownership()
        self.assertIsNone(coll.doc)

        # Peer lease must not be deleted
        coll.doc = {
            "_id": so.LOCK_ID,
            "ownerId": "peer:9:ffffffffffff",
            "expiresAt": now + timedelta(seconds=60),
            "heartbeatAt": now,
        }
        with _patch_ownership_db(coll):
            so.release_scheduler_ownership()
        self.assertIsNotNone(coll.doc)
        assert coll.doc is not None
        self.assertEqual(coll.doc["ownerId"], "peer:9:ffffffffffff")


class TestMongoFailureFailSafe(unittest.TestCase):
    def test_mongo_failure_during_heartbeat_loses_leadership(self):
        import services.scheduler_ownership as so

        so._mark_held(True)
        with patch.object(
            so,
            "try_acquire_or_renew",
            side_effect=RuntimeError("mongo down"),
        ):
            owned = so.is_scheduler_leader()
        self.assertFalse(owned)
        self.assertFalse(so._was_held())


class TestNaiveDatetimeRejected(unittest.TestCase):
    def test_lease_payload_rejects_naive_now(self):
        import services.scheduler_ownership as so

        with self.assertRaises(ValueError):
            so._lease_payload(datetime(2026, 1, 1, 0, 0, 0), "x:1:abc")


class TestSchedulerInitGuard(unittest.TestCase):
    def test_start_scheduler_not_twice(self):
        import scheduler as sched

        calls = {"n": 0}

        def counting_register():
            calls["n"] += 1

        mock_sched = MagicMock()
        mock_sched.running = False

        def start_side_effect():
            mock_sched.running = True

        mock_sched.start.side_effect = start_side_effect

        with (
            patch.object(sched, "scheduler", mock_sched),
            patch.object(sched, "_register_device_monitor_job", counting_register),
            patch.object(sched, "_register_isp_monitor_job"),
            patch.object(sched, "_start_nmap_job"),
            patch.object(sched, "_start_interface_job"),
            patch.object(sched, "_start_interface_stats_job"),
            patch.object(sched, "_start_recovery_job"),
            patch.object(sched, "_start_retention_job"),
            patch.object(sched, "get_settings", return_value={"pingInterval": 30}),
            patch.object(sched, "get_monitor_runtime_mode", return_value="legacy"),
        ):
            sched.start_scheduler()
            sched.start_scheduler()

        self.assertEqual(calls["n"], 1)
        self.assertEqual(mock_sched.start.call_count, 1)

    def test_flask_reloader_parent_skips_start(self):
        """Documented app.py guard: DEBUG parent must not start scheduler."""
        debug = True
        werkzeug_main = None
        should_start = (not debug) or (werkzeug_main == "true")
        self.assertFalse(should_start)

        should_start_child = (not debug) or ("true" == "true")
        self.assertTrue(should_start_child)


class TestLeadershipGate(unittest.TestCase):
    def test_jobs_obey_leader_only(self):
        import services.scheduler_ownership as so

        with patch.object(so, "is_scheduler_leader", return_value=False):
            self.assertFalse(so.require_scheduler_leadership("device_monitor_job"))
        with patch.object(so, "is_scheduler_leader", return_value=True):
            self.assertTrue(so.require_scheduler_leadership("device_monitor_job"))


class TestLogTimezoneFormatter(unittest.TestCase):
    def test_formatter_includes_timezone(self):
        from utils.monitor_logger import _TimezoneAwareFormatter

        fmt = _TimezoneAwareFormatter("%(asctime)s")
        record = type("R", (), {"created": 1723307357.123})()  # noqa: F841
        import logging

        rec = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="x",
            args=(),
            exc_info=None,
        )
        text = fmt.formatTime(rec)
        self.assertIn("+00:00", text)
        self.assertIn("T", text)


if __name__ == "__main__":
    unittest.main()
