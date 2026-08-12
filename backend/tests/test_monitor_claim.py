"""
Phase 1 tests for atomic device scan claims (monitor_claim).

Uses an in-memory devices collection that implements find_one_and_update /
update_one under a lock so concurrent claim races are exercised without Mongo.
"""

from __future__ import annotations

import threading
import unittest
from copy import deepcopy
from datetime import timedelta
from unittest.mock import MagicMock, patch

from bson import ObjectId
from pymongo import ReturnDocument

from utils.utc import ensure_utc, utc_now


def _field_matches(doc: dict, key: str, cond) -> bool:
    """Evaluate a single Mongo-like field condition used by claim filters."""
    if isinstance(cond, dict):
        if "$exists" in cond:
            exists = key in doc
            if bool(cond["$exists"]) != exists:
                return False
            # If only $exists, done for this key unless other ops present.
            other = {k: v for k, v in cond.items() if k != "$exists"}
            if not other:
                return True
            if not exists:
                return True
            cond = other
        if not cond:
            return True
        value = doc.get(key)
        if "$lte" in cond:
            if value is None:
                return False
            left = ensure_utc(value)
            right = ensure_utc(cond["$lte"])
            if left is None or right is None or left > right:
                return False
        return True
    # Equality
    return doc.get(key) == cond


def _matches(doc: dict, filt: dict) -> bool:
    """Minimal filter matcher covering claim / release query shapes."""
    for key, cond in filt.items():
        if key == "$and":
            if not all(_matches(doc, part) for part in cond):
                return False
            continue
        if key == "$or":
            if not any(_matches(doc, part) for part in cond):
                return False
            continue
        if not _field_matches(doc, key, cond):
            return False
    return True


class _FakeDevicesCollection:
    """Thread-safe single-document devices collection for claim tests."""

    def __init__(self, doc: dict):
        self._lock = threading.Lock()
        self.doc = deepcopy(doc)

    def find_one_and_update(self, filt, update, return_document=None):
        with self._lock:
            if not _matches(self.doc, filt):
                return None
            for key, value in (update.get("$set") or {}).items():
                self.doc[key] = value
            for key in (update.get("$unset") or {}):
                self.doc.pop(key, None)
            if return_document == ReturnDocument.AFTER:
                return deepcopy(self.doc)
            return deepcopy(self.doc)

    def update_one(self, filt, update):
        with self._lock:
            matched = 1 if _matches(self.doc, filt) else 0
            if matched:
                for key, value in (update.get("$set") or {}).items():
                    self.doc[key] = value
                for key in (update.get("$unset") or {}):
                    self.doc.pop(key, None)
            result = MagicMock()
            result.acknowledged = True
            result.matched_count = matched
            result.modified_count = matched
            result.upserted_id = None
            return result

    def find_one(self, filt=None):
        with self._lock:
            if filt and not _matches(self.doc, filt):
                return None
            return deepcopy(self.doc)


class _FakeDb:
    def __init__(self, devices: _FakeDevicesCollection):
        self.devices = devices


def _base_device(**overrides) -> dict:
    doc = {
        "_id": ObjectId(),
        "hostname": "test-host",
        "ipAddress": "192.168.0.50",
        "monitor": True,
        "status": "Online",
    }
    doc.update(overrides)
    return doc


class TestMonitorRuntimeMode(unittest.TestCase):
    def test_default_is_dispatch(self):
        from services.settings_service import get_monitor_runtime_mode

        with patch(
            "services.settings_service.os.getenv",
            side_effect=lambda key, default=None: (
                None if key == "MONITOR_RUNTIME_MODE" else default
            ),
        ):
            self.assertEqual(get_monitor_runtime_mode(), "dispatch")

    def test_dispatch_accepted(self):
        from services.settings_service import get_monitor_runtime_mode

        with patch(
            "services.settings_service.os.getenv",
            side_effect=lambda key, default=None: (
                "dispatch" if key == "MONITOR_RUNTIME_MODE" else default
            ),
        ):
            self.assertEqual(get_monitor_runtime_mode(), "dispatch")

    def test_legacy_accepted(self):
        from services.settings_service import get_monitor_runtime_mode

        with patch(
            "services.settings_service.os.getenv",
            side_effect=lambda key, default=None: (
                "legacy" if key == "MONITOR_RUNTIME_MODE" else default
            ),
        ):
            self.assertEqual(get_monitor_runtime_mode(), "legacy")

    def test_unknown_falls_back_to_dispatch(self):
        from services.settings_service import get_monitor_runtime_mode

        with patch(
            "services.settings_service.os.getenv",
            side_effect=lambda key, default=None: (
                "experimental" if key == "MONITOR_RUNTIME_MODE" else default
            ),
        ):
            self.assertEqual(get_monitor_runtime_mode(), "dispatch")

class TestClaimTtl(unittest.TestCase):
    @patch(
        "services.monitor_claim.get_ping_config",
        return_value={
            "interval": 30,
            "timeout_ms": 1000,
            "retries": 2,
            "failure_confirmation_scans": 2,
        },
    )
    def test_ttl_exceeds_worst_logical_ping(self, _cfg):
        from services.monitor_claim import compute_claim_ttl_seconds

        # worst ping = 1.0 * 2 = 2.0s; TTL = max(15, 2+10) = 15
        ttl = compute_claim_ttl_seconds()
        self.assertGreater(ttl, 2.0)
        self.assertEqual(ttl, 15.0)

    @patch(
        "services.monitor_claim.get_ping_config",
        return_value={
            "interval": 30,
            "timeout_ms": 5000,
            "retries": 3,
            "failure_confirmation_scans": 2,
        },
    )
    def test_ttl_grows_with_timeout_retries(self, _cfg):
        from services.monitor_claim import compute_claim_ttl_seconds

        # worst = 5*3=15; TTL = max(15, 15+10) = 25
        ttl = compute_claim_ttl_seconds()
        self.assertEqual(ttl, 25.0)
        self.assertGreater(ttl, 15.0)


class TestClaimDevice(unittest.TestCase):
    def _patch_db(self, doc: dict):
        coll = _FakeDevicesCollection(doc)
        return coll, patch(
            "services.monitor_claim._db",
            return_value=_FakeDb(coll),
        )

    @patch(
        "services.monitor_claim.get_ping_config",
        return_value={
            "interval": 30,
            "timeout_ms": 1000,
            "retries": 2,
            "failure_confirmation_scans": 2,
        },
    )
    def test_due_device_can_be_claimed(self, _cfg):
        from services.monitor_claim import claim_device

        now = utc_now()
        doc = _base_device(nextCheckAt=now - timedelta(seconds=1))
        coll, db_patch = self._patch_db(doc)
        with db_patch:
            claimed = claim_device(doc["_id"], device=doc, now=now)
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed["scanClaimId"], coll.doc["scanClaimId"])
        self.assertIn("scanClaimedAt", claimed)
        self.assertIn("scanClaimExpiresAt", claimed)
        self.assertNotIn("lastCheckedAt", claimed)
        self.assertNotIn("lastPingStartedAt", claimed)

    @patch(
        "services.monitor_claim.get_ping_config",
        return_value={
            "interval": 30,
            "timeout_ms": 1000,
            "retries": 2,
            "failure_confirmation_scans": 2,
        },
    )
    def test_future_next_check_cannot_be_claimed(self, _cfg):
        from services.monitor_claim import claim_device

        now = utc_now()
        doc = _base_device(nextCheckAt=now + timedelta(seconds=20))
        _coll, db_patch = self._patch_db(doc)
        with db_patch:
            claimed = claim_device(doc["_id"], device=doc, now=now)
        self.assertIsNone(claimed)

    @patch(
        "services.monitor_claim.get_ping_config",
        return_value={
            "interval": 30,
            "timeout_ms": 1000,
            "retries": 2,
            "failure_confirmation_scans": 2,
        },
    )
    def test_active_claim_cannot_be_claimed(self, _cfg):
        from services.monitor_claim import claim_device

        now = utc_now()
        doc = _base_device(
            nextCheckAt=now - timedelta(seconds=1),
            scanClaimId="existing",
            scanClaimedAt=now - timedelta(seconds=1),
            scanClaimExpiresAt=now + timedelta(seconds=30),
        )
        _coll, db_patch = self._patch_db(doc)
        with db_patch:
            claimed = claim_device(doc["_id"], device=doc, now=now)
        self.assertIsNone(claimed)

    @patch(
        "services.monitor_claim.get_ping_config",
        return_value={
            "interval": 30,
            "timeout_ms": 1000,
            "retries": 2,
            "failure_confirmation_scans": 2,
        },
    )
    def test_expired_claim_can_be_reclaimed(self, _cfg):
        from services.monitor_claim import claim_device

        now = utc_now()
        doc = _base_device(
            nextCheckAt=now - timedelta(seconds=5),
            scanClaimId="stale",
            scanClaimedAt=now - timedelta(seconds=60),
            scanClaimExpiresAt=now - timedelta(seconds=1),
        )
        coll, db_patch = self._patch_db(doc)
        with db_patch:
            claimed = claim_device(doc["_id"], device=doc, now=now)
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertNotEqual(claimed["scanClaimId"], "stale")
        self.assertEqual(coll.doc["scanClaimId"], claimed["scanClaimId"])

    @patch(
        "services.monitor_claim.get_ping_config",
        return_value={
            "interval": 30,
            "timeout_ms": 1000,
            "retries": 2,
            "failure_confirmation_scans": 2,
        },
    )
    def test_monitor_false_cannot_be_claimed(self, _cfg):
        from services.monitor_claim import claim_device

        now = utc_now()
        doc = _base_device(monitor=False, nextCheckAt=now - timedelta(seconds=1))
        _coll, db_patch = self._patch_db(doc)
        with db_patch:
            claimed = claim_device(doc["_id"], device=doc, now=now)
        self.assertIsNone(claimed)

    @patch(
        "services.monitor_claim.get_ping_config",
        return_value={
            "interval": 30,
            "timeout_ms": 1000,
            "retries": 2,
            "failure_confirmation_scans": 2,
        },
    )
    def test_missing_next_check_at_is_due(self, _cfg):
        from services.monitor_claim import claim_device

        now = utc_now()
        doc = _base_device()  # no nextCheckAt
        self.assertNotIn("nextCheckAt", doc)
        _coll, db_patch = self._patch_db(doc)
        with db_patch:
            claimed = claim_device(doc["_id"], device=doc, now=now)
        self.assertIsNotNone(claimed)

    @patch(
        "services.monitor_claim.get_ping_config",
        return_value={
            "interval": 30,
            "timeout_ms": 1000,
            "retries": 2,
            "failure_confirmation_scans": 2,
        },
    )
    def test_next_check_at_is_claim_time_plus_interval(self, _cfg):
        from services.monitor_claim import claim_device

        now = utc_now()
        doc = _base_device()
        _coll, db_patch = self._patch_db(doc)
        with db_patch:
            claimed = claim_device(doc["_id"], device=doc, now=now)
        self.assertIsNotNone(claimed)
        assert claimed is not None
        next_check = ensure_utc(claimed["nextCheckAt"])
        assert next_check is not None
        delta = (next_check - now).total_seconds()
        self.assertAlmostEqual(delta, 30.0, places=3)

    @patch(
        "services.monitor_claim.get_ping_config",
        return_value={
            "interval": 60,
            "timeout_ms": 1000,
            "retries": 2,
            "failure_confirmation_scans": 2,
        },
    )
    def test_next_check_at_advances_from_previous_deadline(self, _cfg):
        from services.monitor_claim import claim_device

        now = utc_now()
        previous = now - timedelta(seconds=2)
        doc = _base_device(nextCheckAt=previous)
        _coll, db_patch = self._patch_db(doc)
        with db_patch:
            claimed = claim_device(doc["_id"], device=doc, now=now)
        self.assertIsNotNone(claimed)
        assert claimed is not None
        next_check = ensure_utc(claimed["nextCheckAt"])
        assert next_check is not None
        expected = previous + timedelta(seconds=60)
        self.assertEqual(next_check, expected)

    @patch(
        "services.monitor_claim.get_ping_config",
        return_value={
            "interval": 60,
            "timeout_ms": 1000,
            "retries": 2,
            "failure_confirmation_scans": 2,
        },
    )
    def test_overdue_device_clamps_to_claim_now_plus_interval(self, _cfg):
        from services.monitor_claim import claim_device

        now = utc_now()
        # Overdue by > 1 interval: previous + 60 is still in the past.
        previous = now - timedelta(seconds=150)
        doc = _base_device(nextCheckAt=previous)
        _coll, db_patch = self._patch_db(doc)
        with db_patch:
            claimed = claim_device(doc["_id"], device=doc, now=now)
        self.assertIsNotNone(claimed)
        assert claimed is not None
        next_check = ensure_utc(claimed["nextCheckAt"])
        assert next_check is not None
        self.assertAlmostEqual((next_check - now).total_seconds(), 60.0, places=3)

    @patch(
        "services.monitor_claim.get_ping_config",
        return_value={
            "interval": 30,
            "timeout_ms": 1000,
            "retries": 2,
            "failure_confirmation_scans": 2,
        },
    )
    def test_concurrent_claims_produce_exactly_one_winner(self, _cfg):
        from services.monitor_claim import claim_device

        now = utc_now()
        doc = _base_device()
        coll, db_patch = self._patch_db(doc)
        results: list = []
        barrier = threading.Barrier(8)
        results_lock = threading.Lock()

        def worker():
            barrier.wait()
            claimed = claim_device(doc["_id"], device=doc, now=now)
            with results_lock:
                results.append(claimed)

        with db_patch:
            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        winners = [r for r in results if r is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0]["scanClaimId"], coll.doc["scanClaimId"])


class TestComputeNextCheckAt(unittest.TestCase):
    def test_missing_previous_uses_claim_now(self):
        from services.monitor_claim import compute_next_check_at

        now = utc_now()
        nxt = compute_next_check_at(
            claim_now=now,
            previous_next_check_at=None,
            interval_seconds=60,
        )
        self.assertEqual(nxt, now + timedelta(seconds=60))

    def test_deadline_progression(self):
        from services.monitor_claim import compute_next_check_at

        now = utc_now()
        previous = now - timedelta(seconds=1)
        nxt = compute_next_check_at(
            claim_now=now,
            previous_next_check_at=previous,
            interval_seconds=60,
        )
        self.assertEqual(nxt, previous + timedelta(seconds=60))

    def test_substantial_overdue_clamps(self):
        from services.monitor_claim import compute_next_check_at

        now = utc_now()
        previous = now - timedelta(seconds=200)
        nxt = compute_next_check_at(
            claim_now=now,
            previous_next_check_at=previous,
            interval_seconds=60,
        )
        self.assertEqual(nxt, now + timedelta(seconds=60))


class TestReleaseClaim(unittest.TestCase):
    def _patch_db(self, doc: dict):
        coll = _FakeDevicesCollection(doc)
        return coll, patch(
            "services.monitor_claim._db",
            return_value=_FakeDb(coll),
        )

    def test_release_succeeds_with_matching_claim_id(self):
        from services.monitor_claim import release_device_claim

        now = utc_now()
        doc = _base_device(
            scanClaimId="abc123",
            scanClaimedAt=now,
            scanClaimExpiresAt=now + timedelta(seconds=15),
            nextCheckAt=now + timedelta(seconds=30),
        )
        coll, db_patch = self._patch_db(doc)
        with db_patch:
            ok = release_device_claim(doc["_id"], "abc123")
        self.assertTrue(ok)
        self.assertNotIn("scanClaimId", coll.doc)
        self.assertNotIn("scanClaimedAt", coll.doc)
        self.assertNotIn("scanClaimExpiresAt", coll.doc)
        # Scheduling field preserved
        self.assertIn("nextCheckAt", coll.doc)

    def test_release_noop_with_wrong_claim_id(self):
        from services.monitor_claim import release_device_claim

        now = utc_now()
        doc = _base_device(
            scanClaimId="abc123",
            scanClaimedAt=now,
            scanClaimExpiresAt=now + timedelta(seconds=15),
            nextCheckAt=now + timedelta(seconds=30),
        )
        coll, db_patch = self._patch_db(doc)
        with db_patch:
            ok = release_device_claim(doc["_id"], "other-id")
        self.assertFalse(ok)
        self.assertEqual(coll.doc["scanClaimId"], "abc123")
        self.assertIn("scanClaimExpiresAt", coll.doc)


if __name__ == "__main__":
    unittest.main()
