"""
Phase-3 cycle lease + risk_latest projection tests.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from bson import ObjectId


class CycleLeaseTests(unittest.TestCase):
    def _fake_db(self):
        store: dict = {}

        class FakeColl:
            def insert_one(self, doc):
                store[doc["_id"]] = dict(doc)
                return MagicMock(inserted_id=doc["_id"])

            def find_one_and_update(self, query, update, sort=None, return_document=None):
                candidates = list(store.values())
                matched = []
                for d in candidates:
                    ok = True
                    for k, v in query.items():
                        if k == "status" and isinstance(v, dict) and "$in" in v:
                            if d.get("status") not in v["$in"]:
                                ok = False
                                break
                        elif k == "leaseExpiresAt" and isinstance(v, dict) and "$lt" in v:
                            exp = d.get("leaseExpiresAt")
                            if exp is None or not (exp < v["$lt"]):
                                ok = False
                                break
                        elif d.get(k) != v:
                            ok = False
                            break
                    if ok:
                        matched.append(d)
                if not matched:
                    return None
                if sort:
                    matched.sort(key=lambda x: x.get("createdAt"))
                doc = matched[0]
                doc.update(update.get("$set", {}))
                store[doc["_id"]] = doc
                return dict(doc)

            def find(self, query):
                out = []
                for d in store.values():
                    ok = True
                    for k, v in query.items():
                        if k == "status" and isinstance(v, dict) and "$in" in v:
                            if d.get("status") not in v["$in"]:
                                ok = False
                                break
                        elif k == "leaseExpiresAt" and isinstance(v, dict) and "$lt" in v:
                            exp = d.get("leaseExpiresAt")
                            if exp is None or not (exp < v["$lt"]):
                                ok = False
                                break
                        elif d.get(k) != v:
                            ok = False
                            break
                    if ok:
                        out.append(d)
                return out

            def update_one(self, query, update):
                doc = store.get(query.get("_id"))
                if doc:
                    doc.update(update.get("$set", {}))
                return MagicMock()

            def find_one(self, query):
                return store.get(query.get("_id"))

            def create_index(self, *a, **k):
                return None

        fake_db = MagicMock()
        fake_db.__getitem__.side_effect = lambda name: FakeColl()
        return fake_db, store

    def test_lease_created_on_begin_and_heartbeat_extends(self):
        from services.storm import pipeline_cycles as pc

        fake_db, store = self._fake_db()
        with patch.object(pc, "_db", return_value=fake_db):
            cycle = pc.begin_stats_cycle(leader="leader-a")
            self.assertEqual(cycle["stageOwner"], "leader-a")
            self.assertIsNotNone(cycle.get("leaseExpiresAt"))
            cid = cycle["_id"]
            before = store[cid]["leaseExpiresAt"]
            updated = pc.heartbeat_cycle_lease(cid, owner="leader-a", stage="stats")
            self.assertIsNotNone(updated)
            self.assertGreaterEqual(store[cid]["leaseExpiresAt"], before)

    def test_valid_lease_cannot_be_stolen_by_wrong_owner_heartbeat(self):
        from services.storm import pipeline_cycles as pc

        fake_db, store = self._fake_db()
        with patch.object(pc, "_db", return_value=fake_db):
            cycle = pc.begin_stats_cycle(leader="leader-a")
            cid = cycle["_id"]
            stolen = pc.heartbeat_cycle_lease(cid, owner="leader-b", stage="stats")
            self.assertIsNone(stolen)
            self.assertEqual(store[cid]["stageOwner"], "leader-a")

    def test_expired_lease_reclaimed_only_to_recovery_required(self):
        from services.storm import pipeline_cycles as pc

        fake_db, store = self._fake_db()
        with patch.object(pc, "_db", return_value=fake_db):
            cycle = pc.begin_stats_cycle(leader="leader-a")
            cid = cycle["_id"]
            store[cid]["leaseExpiresAt"] = datetime.now(timezone.utc) - timedelta(
                minutes=1
            )
            result = pc.reclaim_expired_running_cycles(leader_id="leader-b")
            self.assertEqual(result["reclaimed"], 1)
            self.assertEqual(
                store[cid]["status"], pc.STATUS_FAILED_RECOVERY_REQUIRED
            )
            self.assertTrue(store[cid]["reclaimed"])
            # Must NOT become analysis_complete / confirmation_running etc.
            self.assertNotEqual(store[cid]["status"], pc.STATUS_STATS_COMPLETE)

    def test_unexpired_lease_not_reclaimed(self):
        from services.storm import pipeline_cycles as pc

        fake_db, store = self._fake_db()
        with patch.object(pc, "_db", return_value=fake_db):
            cycle = pc.begin_stats_cycle(leader="leader-a")
            cid = cycle["_id"]
            result = pc.reclaim_expired_running_cycles(leader_id="leader-b")
            self.assertEqual(result["reclaimed"], 0)
            self.assertEqual(store[cid]["status"], pc.STATUS_STATS_RUNNING)

    def test_reclaim_disabled_leaves_running(self):
        from services.storm import pipeline_cycles as pc

        fake_db, store = self._fake_db()
        with (
            patch.object(pc, "_db", return_value=fake_db),
            patch.object(pc, "lease_reclaim_enabled", return_value=False),
        ):
            cycle = pc.begin_stats_cycle(leader="leader-a")
            cid = cycle["_id"]
            store[cid]["leaseExpiresAt"] = datetime.now(timezone.utc) - timedelta(
                minutes=1
            )
            result = pc.reclaim_expired_running_cycles(leader_id="leader-b")
            self.assertTrue(result["disabled"])
            self.assertEqual(store[cid]["status"], pc.STATUS_STATS_RUNNING)


class ConfirmationLeaseMitigationSafetyTests(unittest.TestCase):
    def test_expired_confirmation_lease_does_not_call_mitigation(self):
        """Reclaim marks recovery_required; safety job must not claim it."""
        from services.storm import pipeline_cycles as pc

        store: dict = {}

        class FakeColl:
            def insert_one(self, doc):
                store[doc["_id"]] = dict(doc)
                return MagicMock(inserted_id=doc["_id"])

            def find_one_and_update(self, query, update, sort=None, return_document=None):
                matched = []
                for d in store.values():
                    ok = True
                    for k, v in query.items():
                        if k == "status" and isinstance(v, dict) and "$in" in v:
                            if d.get("status") not in v["$in"]:
                                ok = False
                                break
                        elif k == "leaseExpiresAt" and isinstance(v, dict) and "$lt" in v:
                            exp = d.get("leaseExpiresAt")
                            if exp is None or not (exp < v["$lt"]):
                                ok = False
                                break
                        elif d.get(k) != v:
                            ok = False
                            break
                    if ok:
                        matched.append(d)
                if not matched:
                    return None
                if sort:
                    matched.sort(key=lambda x: x.get("createdAt"))
                doc = matched[0]
                doc.update(update.get("$set", {}))
                store[doc["_id"]] = doc
                return dict(doc)

            def find(self, query):
                out = []
                for d in store.values():
                    ok = True
                    for k, v in query.items():
                        if k == "status" and isinstance(v, dict) and "$in" in v:
                            if d.get("status") not in v["$in"]:
                                ok = False
                                break
                        elif k == "leaseExpiresAt" and isinstance(v, dict) and "$lt" in v:
                            exp = d.get("leaseExpiresAt")
                            if exp is None or not (exp < v["$lt"]):
                                ok = False
                                break
                        elif d.get(k) != v:
                            ok = False
                            break
                    if ok:
                        out.append(d)
                return out

        fake_db = MagicMock()
        fake_db.__getitem__.side_effect = lambda name: FakeColl()

        with patch.object(pc, "_db", return_value=fake_db):
            # Simulate confirmation crash: analysis done then confirmation claimed.
            now = datetime.now(timezone.utc)
            cid = "cycle-confirm-crash"
            store[cid] = {
                "_id": cid,
                "status": pc.STATUS_CONFIRMATION_RUNNING,
                "createdAt": now,
                "stageOwner": "dead-worker",
                "leaseExpiresAt": now - timedelta(minutes=5),
            }
            pc.reclaim_expired_running_cycles(leader_id="new-leader")
            self.assertEqual(
                store[cid]["status"], pc.STATUS_FAILED_RECOVERY_REQUIRED
            )
            # Safety claim must not pick recovery_required cycles.
            claimed = pc.claim_next_for_safety(owner="new-leader")
            self.assertIsNone(claimed)


class RiskLatestProjectionTests(unittest.TestCase):
    def test_upsert_and_rebuild_shape(self):
        from services.storm import risk_latest as rl

        coll_data: dict = {}

        class FakeLatest:
            def update_one(self, query, update, upsert=False):
                key = (str(query["deviceId"]), query["interface"])
                doc = coll_data.get(key, {"deviceId": query["deviceId"], "interface": query["interface"], "recentRows": []})
                doc.update(update.get("$set", {}))
                push = (update.get("$push") or {}).get("recentRows")
                if push:
                    each = push.get("$each") or []
                    pos = push.get("$position", 0)
                    slice_n = push.get("$slice", 12)
                    rows = list(doc.get("recentRows") or [])
                    for item in reversed(each):
                        rows.insert(pos, item)
                    doc["recentRows"] = rows[:slice_n]
                coll_data[key] = doc
                return MagicMock()

            def bulk_write(self, ops, ordered=False):
                for op in ops:
                    self.update_one(op._filter, op._doc, upsert=op._upsert)
                return MagicMock()

            def find(self, query=None, projection=None):
                return list(coll_data.values())

            def estimated_document_count(self):
                return len(coll_data)

            def create_index(self, *a, **k):
                return None

        class FakeHistory:
            def aggregate(self, pipeline, allowDiskUse=False):
                # minimal rebuild feed
                device = ObjectId()
                now = datetime.now(timezone.utc)
                rows = [
                    {
                        "_id": ObjectId(),
                        "deviceId": device,
                        "interface": "Gi1/0/1",
                        "riskScore": 90 - i,
                        "eligible": True,
                        "confidence": 90,
                        "timestamp": now - timedelta(minutes=i),
                        "cycleId": f"c{i}",
                    }
                    for i in range(3)
                ]
                return [
                    {
                        "_id": {"deviceId": device, "interface": "Gi1/0/1"},
                        "rows": rows,
                    }
                ]

        fake_db = MagicMock()
        fake_db.__getitem__.side_effect = lambda name: (
            FakeLatest() if name == rl.COLLECTION else FakeHistory()
        )
        fake_db.storm_risk_latest = FakeLatest()
        # risk_latest uses _db()[COLLECTION] and _db()[HISTORY]
        with patch.object(rl, "_db", return_value=fake_db):
            device = ObjectId()
            doc = {
                "_id": ObjectId(),
                "deviceId": device,
                "interface": "Gi1/0/1",
                "riskScore": 88,
                "eligible": True,
                "confidence": 90,
                "timestamp": datetime.now(timezone.utc),
                "cycleId": "cycle-n",
            }
            rl.upsert_risk_latest_from_history_doc(doc)
            key = (str(device), "Gi1/0/1")
            self.assertIn(key, coll_data)
            self.assertEqual(coll_data[key]["cycleId"], "cycle-n")
            self.assertEqual(len(coll_data[key]["recentRows"]), 1)

            rebuilt = rl.rebuild_risk_latest(recent_limit=12)
            self.assertGreaterEqual(rebuilt["upserted"], 1)

    def test_prefer_cycle_from_recent_rows(self):
        from services.storm.confirmation_prefetch import prefer_cycle_risk_rows

        rows = [
            {"_id": "n1", "cycleId": "N+1", "riskScore": 50},
            {"_id": "n0", "cycleId": "N", "riskScore": 90},
        ]
        out = prefer_cycle_risk_rows(rows, "N")
        self.assertEqual(out[0]["_id"], "n0")
        self.assertEqual(out[0]["riskScore"], 90)

    def test_candidate_population_uses_latest_keys(self):
        from services.storm import risk_latest as rl

        device = ObjectId()

        class FakeLatest:
            def estimated_document_count(self):
                return 2

            def find(self, query=None, projection=None):
                return [
                    {
                        "deviceId": device,
                        "interface": "Gi1/0/1",
                        "hostname": "sw1",
                        "ipAddress": "10.0.0.1",
                    },
                    {
                        "deviceId": device,
                        "interface": "Gi1/0/2",
                        "hostname": "sw1",
                        "ipAddress": "10.0.0.1",
                    },
                ]

        fake_db = MagicMock()
        fake_db.__getitem__.return_value = FakeLatest()
        with patch.object(rl, "_db", return_value=fake_db):
            cands = rl.load_confirmation_candidates_from_latest()
            self.assertEqual(len(cands), 2)
            keys = {(c["_id"]["deviceId"], c["_id"]["interface"]) for c in cands}
            self.assertEqual(keys, {(device, "Gi1/0/1"), (device, "Gi1/0/2")})


class MitigationLockRegressionTests(unittest.TestCase):
    def test_duplicate_mitigation_lock_raises(self):
        from pymongo.errors import DuplicateKeyError
        from services.storm.lock_service import LockService

        coll = MagicMock()
        coll.insert_one.side_effect = [
            None,
            DuplicateKeyError("dup"),
        ]
        coll.delete_one = MagicMock()
        with (
            patch.object(LockService, "mitigation_collection", return_value=coll),
            patch.object(LockService, "get_lock_ttl_seconds", return_value=60),
            patch.object(LockService, "_cleanup_expired_lock_ids"),
        ):
            with self.assertRaises(ValueError):
                LockService.acquire_mitigation_locks(
                    ObjectId(), "Gi1/0/1", owner="sys", execution_id="e1"
                )


if __name__ == "__main__":
    unittest.main()
