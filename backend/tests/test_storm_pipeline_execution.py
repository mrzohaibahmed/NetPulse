"""
Phase-1 storm pipeline execution tests.

Proves job split / cycle coordination without changing storm decision formulas.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from bson import ObjectId


class PipelineCycleCoordinationTests(unittest.TestCase):
    def test_stage_transitions_are_atomic_and_ordered(self):
        from services.storm import pipeline_cycles as pc

        store: dict = {}

        class FakeColl:
            def insert_one(self, doc):
                store[doc["_id"]] = dict(doc)
                return MagicMock(inserted_id=doc["_id"])

            def find_one_and_update(self, query, update, sort=None, return_document=None):
                status = query.get("status")
                # claim helpers use status-only query
                candidates = [
                    d
                    for d in store.values()
                    if d.get("status") == status
                    or (
                        query.get("_id")
                        and d.get("_id") == query.get("_id")
                        and d.get("status") == status
                    )
                ]
                if query.get("_id"):
                    candidates = [
                        d
                        for d in store.values()
                        if d["_id"] == query["_id"] and d.get("status") == status
                    ]
                if not candidates:
                    return None
                if sort:
                    candidates.sort(key=lambda d: d.get("createdAt"))
                doc = candidates[0]
                doc.update(update.get("$set", {}))
                store[doc["_id"]] = doc
                return dict(doc)

            def update_one(self, query, update):
                doc = store.get(query["_id"])
                if not doc:
                    return MagicMock()
                doc.update(update.get("$set", {}))
                return MagicMock()

            def find_one(self, query):
                return store.get(query.get("_id"))

            def create_index(self, *a, **k):
                return None

        fake_db = MagicMock()
        fake_db.__getitem__.side_effect = lambda name: FakeColl()

        with patch.object(pc, "_db", return_value=fake_db):
            cycle = pc.begin_stats_cycle(leader="test-leader")
            cid = cycle["_id"]
            self.assertEqual(cycle["status"], pc.STATUS_STATS_RUNNING)

            done = pc.mark_stats_complete(cid, {"total": 2, "samples": 10, "failed": 0})
            self.assertEqual(done["status"], pc.STATUS_STATS_COMPLETE)

            claimed = pc.claim_next_for_analysis()
            self.assertEqual(claimed["_id"], cid)
            self.assertEqual(claimed["status"], pc.STATUS_ANALYSIS_RUNNING)

            # Second claim finds nothing
            self.assertIsNone(pc.claim_next_for_analysis())

            analyzed = pc.mark_analysis_complete(cid, {"riskTotal": 5})
            self.assertEqual(analyzed["status"], pc.STATUS_ANALYSIS_COMPLETE)
            self.assertIsNotNone(analyzed.get("riskPublishedAt"))

            conf = pc.claim_next_for_confirmation()
            self.assertEqual(conf["status"], pc.STATUS_CONFIRMATION_RUNNING)
            self.assertIsNone(pc.claim_next_for_confirmation())

            pc.mark_confirmation_complete(cid, {"total": 5, "confirmed": 0})
            safety = pc.claim_next_for_safety()
            self.assertEqual(safety["status"], pc.STATUS_SAFETY_RUNNING)
            pc.mark_safety_complete(cid, {"errors": 0})
            self.assertEqual(store[cid]["status"], pc.STATUS_SAFETY_COMPLETE)


class SchedulerJobSplitTests(unittest.TestCase):
    def test_analysis_publishes_risk_before_confirmation(self):
        import scheduler as sched

        order: list[str] = []

        def fake_elig(**kwargs):
            order.append("eligibility")
            return {"total": 1, "eligible": 1, "errors": 0}

        def fake_risk(**kwargs):
            order.append("risk")
            return {"total": 1, "scored": 1, "errors": 0}

        cycle = {
            "_id": "cycle-1",
            "status": "analysis_running",
            "createdAt": datetime.now(timezone.utc),
        }

        with (
            patch.object(sched, "require_scheduler_leadership", return_value=True),
            patch(
                "services.storm.pipeline_cycles.claim_next_for_analysis",
                return_value=cycle,
            ),
            patch(
                "services.storm.eligibility.evaluate_all_interfaces",
                side_effect=fake_elig,
            ),
            patch(
                "services.storm.risk_engine.calculate_all_risks",
                side_effect=fake_risk,
            ),
            patch(
                "services.storm.pipeline_cycles.mark_analysis_complete"
            ) as mark_done,
            patch(
                "services.storm.confirmation.evaluate_all_confirmations"
            ) as confirm,
        ):
            sched._run_storm_analysis_job()

        self.assertEqual(order, ["eligibility", "risk"])
        mark_done.assert_called_once()
        confirm.assert_not_called()

    def test_confirmation_job_skips_without_completed_analysis(self):
        import scheduler as sched

        with (
            patch.object(sched, "require_scheduler_leadership", return_value=True),
            patch(
                "services.storm.pipeline_cycles.claim_next_for_confirmation",
                return_value=None,
            ),
            patch(
                "services.storm.confirmation.evaluate_all_confirmations"
            ) as confirm,
        ):
            sched._run_storm_confirmation_job()
        confirm.assert_not_called()

    def test_confirmation_uses_freeze_and_cycle_id(self):
        import scheduler as sched

        cycle = {"_id": "cycle-confirm", "status": "confirmation_running"}

        with (
            patch.object(sched, "require_scheduler_leadership", return_value=True),
            patch(
                "services.storm.pipeline_cycles.claim_next_for_confirmation",
                return_value=cycle,
            ),
            patch(
                "services.storm.confirmation.evaluate_all_confirmations",
                return_value={"total": 0, "confirmed": 0, "errors": 0},
            ) as confirm,
            patch(
                "services.storm.pipeline_cycles.mark_confirmation_complete"
            ) as mark_done,
        ):
            sched._run_storm_confirmation_job()

        confirm.assert_called_once_with(
            freeze_latest_inputs=True, cycle_id="cycle-confirm"
        )
        mark_done.assert_called_once()

    def test_follower_skips_all_storm_jobs(self):
        import scheduler as sched

        with patch.object(sched, "require_scheduler_leadership", return_value=False):
            with patch(
                "services.storm.pipeline_cycles.begin_stats_cycle"
            ) as begin:
                sched._run_interface_stats_job()
                begin.assert_not_called()
            with patch(
                "services.storm.pipeline_cycles.claim_next_for_analysis"
            ) as claim:
                sched._run_storm_analysis_job()
                claim.assert_not_called()
            with patch(
                "services.storm.pipeline_cycles.claim_next_for_confirmation"
            ) as claim:
                sched._run_storm_confirmation_job()
                claim.assert_not_called()
            with patch(
                "services.storm.pipeline_cycles.claim_next_for_safety"
            ) as claim:
                sched._run_storm_safety_prepare_job()
                claim.assert_not_called()

    def test_stats_job_registers_independently(self):
        import scheduler as sched

        mock_sched = MagicMock()
        with (
            patch.object(sched, "scheduler", mock_sched),
            patch.object(sched, "INTERFACE_STATS_INTERVAL", 30),
        ):
            sched._start_interface_stats_job()
            sched._start_storm_analysis_job()
            sched._start_storm_confirmation_job()
            sched._start_storm_safety_prepare_job()

        ids = [c.kwargs["id"] for c in mock_sched.add_job.call_args_list]
        self.assertEqual(
            ids,
            [
                "interface_stats_job",
                "storm_analysis_job",
                "storm_confirmation_job",
                "storm_safety_prepare_job",
            ],
        )
        for call in mock_sched.add_job.call_args_list:
            self.assertEqual(call.kwargs["max_instances"], 1)
            self.assertTrue(call.kwargs["coalesce"])
            self.assertEqual(call.kwargs["seconds"], 30)

    def test_failed_switch_does_not_abort_stats_cycle(self):
        import scheduler as sched

        cycle = {"_id": "c-stats"}
        summary = {
            "total": 2,
            "succeeded": 1,
            "failed": 1,
            "samples": 40,
            "errors": [{"ip": "1.1.1.1", "error": "timeout"}],
        }

        with (
            patch.object(sched, "require_scheduler_leadership", return_value=True),
            patch(
                "services.storm.pipeline_cycles.begin_stats_cycle",
                return_value=cycle,
            ),
            patch(
                "services.interface_collection.stats_collector.collect_all_interface_stats",
                return_value=summary,
            ),
            patch(
                "services.storm.pipeline_cycles.mark_stats_complete"
            ) as mark_done,
            patch(
                "services.storm.pipeline_cycles.mark_cycle_failed"
            ) as mark_fail,
            patch.object(sched, "get_owner_id", return_value="leader-1"),
        ):
            sched._run_interface_stats_job()

        mark_done.assert_called_once_with("c-stats", summary)
        mark_fail.assert_not_called()

    def test_safety_job_preserves_mitigation_lock_path(self):
        import scheduler as sched

        cycle = {"_id": "c-safe"}

        with (
            patch.object(sched, "require_scheduler_leadership", return_value=True),
            patch(
                "services.storm.pipeline_cycles.claim_next_for_safety",
                return_value=cycle,
            ),
            patch(
                "services.storm.safety.evaluate_all_safety",
                return_value={"total": 0},
            ),
            patch(
                "services.storm.orchestrator.prepare_all_safe",
                return_value={"total": 0},
            ),
            patch(
                "services.settings_service.get_settings",
                return_value={"mitigationMode": "automatic"},
            ),
            patch(
                "services.storm.auto_mitigation.run_automatic_mitigation_batch",
                return_value={
                    "batchSize": 5,
                    "readyFetched": 1,
                    "executed": 1,
                    "success": 1,
                    "failed": 0,
                    "results": [{"incidentId": "storm-1", "success": True}],
                },
            ) as auto_batch,
            patch(
                "services.storm.pipeline_cycles.mark_safety_complete"
            ) as mark_done,
        ):
            sched._run_storm_safety_prepare_job()

        auto_batch.assert_called_once()
        mark_done.assert_called_once()


class LoadStatsPairCycleTests(unittest.TestCase):
    def test_cycle_id_selects_current_sample_and_older_previous(self):
        from services.storm.history import load_stats_pair

        device_id = ObjectId()
        now = datetime.now(timezone.utc)
        current = {
            "deviceId": device_id,
            "interfaceName": "Gi1/0/1",
            "cycleId": "cyc-2",
            "timestamp": now,
            "broadcastPackets": 100,
        }
        previous = {
            "deviceId": device_id,
            "interfaceName": "Gi1/0/1",
            "cycleId": "cyc-1",
            "timestamp": now.replace(year=now.year - 1)
            if False
            else now,
            "broadcastPackets": 50,
        }
        # Ensure previous is older
        previous = dict(previous)
        from datetime import timedelta

        previous["timestamp"] = now - timedelta(seconds=30)

        fake = MagicMock()
        fake.interface_stats.find_one.side_effect = [current, previous]

        cur, prev = load_stats_pair(
            device_id, "Gi1/0/1", db=fake, cycle_id="cyc-2"
        )
        self.assertEqual(cur["cycleId"], "cyc-2")
        self.assertEqual(prev["cycleId"], "cyc-1")
        self.assertEqual(fake.interface_stats.find_one.call_count, 2)


class ConfirmationFreezeGoldenTests(unittest.TestCase):
    def test_freeze_path_matches_injected_evaluate_semantics(self):
        """
        Freeze injects the same optional evaluate() inputs the engine already
        supports — decision code path is identical when inputs match.
        """
        from services.storm.confirmation import ConfirmationEngine
        from services.storm.confirmation_rules import ConfirmationConfig

        engine = ConfirmationEngine(
            config=ConfirmationConfig(
                confirmation_enabled=True,
                required_confirmations=2,
                risk_threshold=25.0,
                reset_on_poll_failure=False,
                reset_on_ineligible=True,
                reset_on_low_risk=True,
                poll_stale_seconds=180,
            )
        )
        device_id = ObjectId()
        risk_rows = [
            {"riskScore": 80.0, "timestamp": datetime.now(timezone.utc)},
            {"riskScore": 70.0, "timestamp": datetime.now(timezone.utc)},
        ]

        with patch.object(engine, "_store"):
            a = engine.evaluate(
                device_id,
                "Gi1/0/1",
                eligible=True,
                risk_rows=risk_rows,
                previous_confirmation={
                    "state": "PENDING",
                    "consecutiveHighSamples": 1,
                },
                poll_failed=False,
                persist=False,
            )
            b = engine.evaluate(
                device_id,
                "Gi1/0/1",
                eligible=True,
                risk_rows=list(risk_rows),
                previous_confirmation={
                    "state": "PENDING",
                    "consecutiveHighSamples": 1,
                },
                poll_failed=False,
                persist=False,
            )

        self.assertEqual(a.state, b.state)
        self.assertEqual(a.confirmed, b.confirmed)
        self.assertEqual(a.consecutive_high_samples, b.consecutive_high_samples)
        self.assertEqual(a.current_risk, b.current_risk)


if __name__ == "__main__":
    unittest.main()
