"""Tests for 60s cadence migration and nextCheckAt stagger backfill."""

from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import datetime
from unittest.mock import MagicMock, patch

from bson import ObjectId

from utils.utc import ensure_utc, utc_now


class _FakeDevices:
    def __init__(self, docs: list[dict]):
        self.docs = {d["_id"]: deepcopy(d) for d in docs}

    def find(self, query, projection=None):
        results = []
        for doc in self.docs.values():
            if not doc.get("monitor"):
                continue
            nxt = doc.get("nextCheckAt")
            missing = "nextCheckAt" not in doc or nxt is None
            if missing:
                results.append({"_id": doc["_id"]})
        return results

    def update_one(self, filt, update):
        device_id = filt.get("_id")
        doc = self.docs.get(device_id)
        matched = 0
        modified = 0
        if doc is not None:
            or_parts = filt.get("$or") or []
            ok = True
            if or_parts:
                ok = False
                for part in or_parts:
                    if "nextCheckAt" in part and part["nextCheckAt"] is None:
                        if doc.get("nextCheckAt") is None:
                            ok = True
                            break
                    if part.get("nextCheckAt", {}).get("$exists") is False:
                        if "nextCheckAt" not in doc:
                            ok = True
                            break
            if ok:
                matched = 1
                for key, value in (update.get("$set") or {}).items():
                    if doc.get(key) != value:
                        modified = 1
                    doc[key] = value
        result = MagicMock()
        result.matched_count = matched
        result.modified_count = modified
        return result


class _FakeSettings:
    def __init__(self, doc: dict):
        self.doc = deepcopy(doc)

    def update_one(self, filt, update):
        self.doc.update(update.get("$set") or {})
        result = MagicMock()
        result.matched_count = 1
        result.modified_count = 1
        return result

    def find_one(self, filt=None):
        return deepcopy(self.doc)


class _FakeDb:
    def __init__(self, devices: _FakeDevices, settings: _FakeSettings):
        self.devices = devices
        self.settings = settings


class TestMonitorScheduleMigration(unittest.TestCase):
    def test_legacy_seeds_promoted_once(self):
        from services import monitor_schedule_migration as mig

        settings_doc = {
            "_id": "global",
            "pingInterval": 30,
            "pingConcurrency": 20,
        }
        fake_settings = _FakeSettings(settings_doc)
        fake_db = _FakeDb(_FakeDevices([]), fake_settings)

        with (
            patch.object(mig, "_db", return_value=fake_db),
            patch.object(mig, "get_settings", side_effect=lambda: deepcopy(fake_settings.doc)),
        ):
            first = mig.ensure_monitor_cadence_settings()
            second = mig.ensure_monitor_cadence_settings()

        self.assertFalse(first["skipped"])
        self.assertIn("pingInterval", first["changed"])
        self.assertIn("pingConcurrency", first["changed"])
        self.assertEqual(fake_settings.doc["pingInterval"], 60)
        self.assertEqual(fake_settings.doc["pingConcurrency"], 40)
        self.assertTrue(second["skipped"])

    def test_promotes_intermediate_concurrency_30(self):
        from services import monitor_schedule_migration as mig

        settings_doc = {
            "_id": "global",
            "pingInterval": 60,
            "pingConcurrency": 30,
        }
        fake_settings = _FakeSettings(settings_doc)
        fake_db = _FakeDb(_FakeDevices([]), fake_settings)

        with (
            patch.object(mig, "_db", return_value=fake_db),
            patch.object(mig, "get_settings", side_effect=lambda: deepcopy(fake_settings.doc)),
        ):
            result = mig.ensure_monitor_cadence_settings()

        self.assertFalse(result["skipped"])
        self.assertIn("pingConcurrency", result["changed"])
        self.assertEqual(fake_settings.doc["pingConcurrency"], 40)
    def test_custom_interval_not_overwritten(self):
        from services import monitor_schedule_migration as mig

        settings_doc = {
            "_id": "global",
            "pingInterval": 45,
            "pingConcurrency": 40,
        }
        fake_settings = _FakeSettings(settings_doc)
        fake_db = _FakeDb(_FakeDevices([]), fake_settings)

        with (
            patch.object(mig, "_db", return_value=fake_db),
            patch.object(mig, "get_settings", side_effect=lambda: deepcopy(fake_settings.doc)),
        ):
            result = mig.ensure_monitor_cadence_settings()

        self.assertFalse(result["skipped"])
        self.assertEqual(result["changed"], [])
        self.assertEqual(fake_settings.doc["pingInterval"], 45)
        self.assertEqual(fake_settings.doc["pingConcurrency"], 40)

    def test_backfill_staggers_missing_next_check_at(self):
        from services import monitor_schedule_migration as mig

        devices = [
            {"_id": ObjectId(), "monitor": True},
            {"_id": ObjectId(), "monitor": True, "nextCheckAt": None},
            {
                "_id": ObjectId(),
                "monitor": True,
                "nextCheckAt": utc_now(),
            },
            {"_id": ObjectId(), "monitor": False},
        ]
        fake_devices = _FakeDevices(devices)
        fake_settings = _FakeSettings({"_id": "global", "pingInterval": 60})
        fake_db = _FakeDb(fake_devices, fake_settings)

        with (
            patch.object(mig, "_db", return_value=fake_db),
            patch.object(mig, "get_settings", return_value=fake_settings.doc),
        ):
            summary = mig.backfill_next_check_at(interval_seconds=60)

        self.assertEqual(summary["updated"], 2)
        assigned = []
        for doc in fake_devices.docs.values():
            if doc.get("monitor") and doc.get("nextCheckAt") is not None:
                stamp = ensure_utc(doc["nextCheckAt"])
                self.assertIsInstance(stamp, datetime)
                assigned.append(stamp)

        # Existing nextCheckAt preserved; two new assignments within [0, 60).
        self.assertGreaterEqual(len(assigned), 3)


class TestCreateDeviceSeedsNextCheckAt(unittest.TestCase):
    def test_monitored_device_gets_next_check_at(self):
        from models.device import create_device

        doc = create_device("h1", "10.0.0.1", "switch", monitor=True)
        self.assertIn("nextCheckAt", doc)
        self.assertIsNotNone(doc["nextCheckAt"])

    def test_unmonitored_device_skips_next_check_at(self):
        from models.device import create_device

        doc = create_device("h1", "10.0.0.1", "switch", monitor=False)
        self.assertNotIn("nextCheckAt", doc)


if __name__ == "__main__":
    unittest.main()
