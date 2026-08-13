"""
Phase-2 confirmation data-access / concurrency tests.

Decision formulas are unchanged — these tests prove prefetch + batching
preserve logical inputs and that workers never share an interface.
"""

from __future__ import annotations

import threading
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from bson import ObjectId

from services.storm.confirmation import ConfirmationEngine, evaluate_all_confirmations
from services.storm.confirmation_prefetch import (
    detect_poll_failure_from_maps,
    prefer_cycle_risk_rows,
)
from services.storm.confirmation_rules import (
    STATE_CONFIRMED,
    STATE_NOT_CONFIRMED,
    ConfirmationConfig,
)


class PollFailureMapEquivalenceTests(unittest.TestCase):
    def test_maps_match_detect_poll_failure_rules(self):
        now = datetime.now(timezone.utc)
        latest_stat = {"timestamp": now}
        risk_rows = [
            {"confidence": 0, "timestamp": now},
            {"confidence": 80, "timestamp": now},
        ]
        failed, reason = detect_poll_failure_from_maps(
            ObjectId(),
            "Gi1/0/1",
            stale_seconds=180,
            latest_risk=risk_rows[0],
            risk_rows=risk_rows,
            device_status="Online",
            interface_exists=True,
            latest_stat=latest_stat,
        )
        self.assertTrue(failed)
        self.assertIn("confidence", (reason or "").lower())

        ok, reason2 = detect_poll_failure_from_maps(
            ObjectId(),
            "Gi1/0/1",
            stale_seconds=180,
            latest_risk={"confidence": 90},
            risk_rows=[{"confidence": 90}],
            device_status="Online",
            interface_exists=True,
            latest_stat=latest_stat,
        )
        self.assertFalse(ok)
        self.assertIsNone(reason2)

    def test_prefer_cycle_risk_rows(self):
        rows = [
            {"_id": "a", "cycleId": "c1", "riskScore": 10},
            {"_id": "b", "cycleId": "c0", "riskScore": 90},
        ]
        out = prefer_cycle_risk_rows(rows, "c0")
        self.assertEqual(out[0]["_id"], "b")
        self.assertEqual(len(out), 2)


class BulkConfirmationGoldenTests(unittest.TestCase):
    def setUp(self):
        self.device_id = ObjectId()
        self.config = ConfirmationConfig(
            confirmation_enabled=True,
            required_confirmations=2,
            risk_threshold=75.0,
            reset_on_poll_failure=True,
            reset_on_ineligible=True,
            reset_on_low_risk=True,
            poll_stale_seconds=180,
        )
        self.engine = ConfirmationEngine(config=self.config)
        self._source_gate = patch(
            "services.storm.storm_source_selector.confirmation_allowed_for_source",
            return_value=(True, "", None),
        )
        self._source_gate.start()
        self.addCleanup(self._source_gate.stop)

    def test_prefetched_inputs_match_engine_decision(self):
        now = datetime.now(timezone.utc)
        risk_rows = [
            {"riskScore": 90, "eligible": True, "timestamp": now, "confidence": 90},
            {"riskScore": 88, "eligible": True, "timestamp": now, "confidence": 90},
        ]
        result = self.engine.evaluate(
            self.device_id,
            "Gi1/0/10",
            eligible=True,
            risk_rows=risk_rows,
            poll_failed=False,
            previous_confirmation={},
            persist=False,
        )
        self.assertTrue(result.confirmed)
        self.assertEqual(result.state, STATE_CONFIRMED)

    def test_freeze_path_uses_bulk_prefetch_and_insert_many(self):
        candidates = [
            {
                "_id": {"deviceId": self.device_id, "interface": "Gi1/0/1"},
                "hostname": "sw1",
                "ipAddress": "10.0.0.1",
            },
            {
                "_id": {"deviceId": self.device_id, "interface": "Gi1/0/2"},
                "hostname": "sw1",
                "ipAddress": "10.0.0.1",
            },
        ]
        now = datetime.now(timezone.utc)
        risk_doc = {
            "riskScore": 10,
            "eligible": True,
            "confidence": 90,
            "timestamp": now,
            "_id": ObjectId(),
        }
        key1 = (str(self.device_id), "Gi1/0/1")
        key2 = (str(self.device_id), "Gi1/0/2")

        fake_db = MagicMock()
        fake_db.storm_risk_history.aggregate.return_value = candidates
        fake_db.__getitem__.return_value.insert_many = MagicMock()

        with (
            patch("services.storm.confirmation._db", return_value=fake_db),
            patch(
                "services.storm.confirmation.get_confirmation_config",
                return_value=self.config,
            ),
            patch(
                "services.storm.confirmation_prefetch.bulk_latest_eligibility_map",
                return_value={key1: True, key2: False},
            ),
            patch(
                "services.storm.confirmation_prefetch.bulk_latest_confirmation_map",
                return_value={},
            ),
            patch(
                "services.storm.confirmation_prefetch.bulk_recent_risk_rows_map",
                return_value=(
                    {key1: [risk_doc], key2: [risk_doc]},
                    {
                        "riskLatestHit": True,
                        "riskLatestFallback": False,
                        "riskLookupDurationMs": 1,
                        "source": "risk_latest",
                    },
                ),
            ),
            patch(
                "services.storm.confirmation_prefetch.bulk_device_status_map",
                return_value={str(self.device_id): "Online"},
            ),
            patch(
                "services.storm.confirmation_prefetch.bulk_interface_exists_set",
                return_value={key1, key2},
            ),
            patch(
                "services.storm.confirmation_prefetch.bulk_latest_stats_map",
                return_value={key1: {"timestamp": now}, key2: {"timestamp": now}},
            ),
            patch.dict("os.environ", {"STORM_CONFIRMATION_WORKERS": "1"}),
        ):
            out = evaluate_all_confirmations(
                freeze_latest_inputs=True,
                cycle_id="cycle-test",
                workers=1,
            )

        self.assertEqual(out["total"], 2)
        self.assertEqual(out["errors"], 0)
        fake_db.__getitem__.return_value.insert_many.assert_called_once()
        docs = fake_db.__getitem__.return_value.insert_many.call_args[0][0]
        self.assertEqual(len(docs), 2)
        self.assertTrue(all(d.get("cycleId") == "cycle-test" for d in docs))
        # Ineligible interface must reset / not-confirm (eligibility False)
        states = {d["interface"]: d["state"] for d in docs}
        self.assertEqual(states["Gi1/0/2"], STATE_NOT_CONFIRMED)


class ConfirmationConcurrencySafetyTests(unittest.TestCase):
    def test_same_interface_never_runs_concurrently(self):
        """Workers process different interfaces; per-interface lock is implicit by partitioning."""
        active: dict[str, int] = {}
        lock = threading.Lock()
        max_seen = {"n": 0}

        def fake_evaluate(device_id, interface, **kwargs):
            with lock:
                active[interface] = active.get(interface, 0) + 1
                max_seen["n"] = max(max_seen["n"], active[interface])
            time.sleep(0.02)
            with lock:
                active[interface] -= 1
            from services.storm.models import ConfirmationResult  # noqa: PLC0415

            return ConfirmationResult(
                confirmed=False,
                state=STATE_NOT_CONFIRMED,
                current_risk=0.0,
                highest_risk=0.0,
                average_risk=0.0,
                consecutive_high_samples=0,
                required_samples=2,
                reason="ok",
                timestamp=datetime.now(timezone.utc),
                device_id=str(device_id),
                interface=interface,
            )

        device_id = ObjectId()
        candidates = [
            {
                "_id": {"deviceId": device_id, "interface": f"Gi1/0/{i}"},
                "hostname": "sw1",
                "ipAddress": "10.0.0.1",
            }
            for i in range(1, 9)
        ]
        now = datetime.now(timezone.utc)
        keys = [(str(device_id), f"Gi1/0/{i}") for i in range(1, 9)]
        risk_map = {
            k: [{"riskScore": 1, "eligible": True, "confidence": 90, "timestamp": now, "_id": ObjectId()}]
            for k in keys
        }

        fake_db = MagicMock()
        fake_db.storm_risk_history.aggregate.return_value = candidates
        fake_db.__getitem__.return_value.insert_many = MagicMock()

        config = ConfirmationConfig(
            confirmation_enabled=True,
            required_confirmations=2,
            risk_threshold=75.0,
            reset_on_poll_failure=True,
            reset_on_ineligible=True,
            reset_on_low_risk=True,
            poll_stale_seconds=180,
        )
        engine = ConfirmationEngine(config=config)
        engine.evaluate = fake_evaluate  # type: ignore[method-assign]

        with (
            patch("services.storm.confirmation._db", return_value=fake_db),
            patch(
                "services.storm.confirmation.get_confirmation_config",
                return_value=config,
            ),
            patch(
                "services.storm.confirmation.get_confirmation_engine",
                return_value=engine,
            ),
            patch(
                "services.storm.confirmation_prefetch.bulk_latest_eligibility_map",
                return_value={k: True for k in keys},
            ),
            patch(
                "services.storm.confirmation_prefetch.bulk_latest_confirmation_map",
                return_value={},
            ),
            patch(
                "services.storm.confirmation_prefetch.bulk_recent_risk_rows_map",
                return_value=(
                    risk_map,
                    {
                        "riskLatestHit": True,
                        "riskLatestFallback": False,
                        "riskLookupDurationMs": 1,
                        "source": "risk_latest",
                    },
                ),
            ),
            patch(
                "services.storm.confirmation_prefetch.bulk_device_status_map",
                return_value={str(device_id): "Online"},
            ),
            patch(
                "services.storm.confirmation_prefetch.bulk_interface_exists_set",
                return_value=set(keys),
            ),
            patch(
                "services.storm.confirmation_prefetch.bulk_latest_stats_map",
                return_value={k: {"timestamp": now} for k in keys},
            ),
        ):
            out = evaluate_all_confirmations(
                freeze_latest_inputs=True,
                cycle_id="c-conc",
                workers=4,
            )

        self.assertEqual(out["total"], 8)
        self.assertEqual(out["errors"], 0)
        self.assertEqual(max_seen["n"], 1)
        self.assertEqual(out["workers"], 4)


if __name__ == "__main__":
    unittest.main()
