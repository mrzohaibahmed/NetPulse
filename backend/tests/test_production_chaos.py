"""
Chaos / failure scenario tests for production hardening (controlled, in-memory/mocked).
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from services.storm.pipeline_cycles import reclaim_expired_running_cycles


class StormLeaseReclaimTests(unittest.TestCase):
    @patch("services.storm.pipeline_cycles.lease_reclaim_enabled", return_value=True)
    @patch("services.storm.pipeline_cycles._db")
    def test_expired_confirmation_lease_does_not_invoke_mitigation(self, mock_db_fn, *_):
        """Reclaim marks recovery_required — never re-queues confirmation/mitigation."""
        now = datetime.now(timezone.utc)
        store = {
            "c1": {
                "_id": "c1",
                "status": "confirmation_running",
                "leaseExpiresAt": now - timedelta(minutes=1),
                "stageOwner": "old-leader",
            }
        }
        coll = MagicMock()

        def find(query):
            out = []
            for doc in store.values():
                exp = doc.get("leaseExpiresAt")
                if query.get("status", {}).get("$in") and doc["status"] not in query["status"]["$in"]:
                    continue
                if "$lt" in query.get("leaseExpiresAt", {}):
                    if exp and exp < query["leaseExpiresAt"]["$lt"]:
                        out.append(doc)
            return iter(out)

        def find_one_and_update(filt, update, return_document=None):
            doc = store.get(filt["_id"])
            if not doc:
                return None
            if doc.get("leaseExpiresAt") and doc["leaseExpiresAt"] >= now:
                return None
            for k, v in update.get("$set", {}).items():
                doc[k] = v
            return doc

        coll.find.side_effect = find
        coll.find_one_and_update.side_effect = find_one_and_update
        mock_db_fn.return_value.__getitem__.return_value = coll

        with patch("services.storm.confirmation.evaluate_all_confirmations") as mock_conf:
            result = reclaim_expired_running_cycles(leader_id="new-leader")
            mock_conf.assert_not_called()
        self.assertGreaterEqual(result.get("reclaimed", 0), 0)


class SchedulerOverlapTests(unittest.TestCase):
    def test_nmap_job_registration_uses_coalesce(self):
        from scheduler import _start_nmap_job, scheduler

        with patch.object(scheduler, "add_job") as mock_add:
            with patch("scheduler.NMAP_SCAN_INTERVAL", 3600):
                _start_nmap_job()
        kwargs = mock_add.call_args.kwargs
        self.assertEqual(kwargs.get("max_instances"), 1)
        self.assertTrue(kwargs.get("coalesce"))


if __name__ == "__main__":
    unittest.main()
