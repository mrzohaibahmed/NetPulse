"""
Integration test: device delete cascade removes all deviceId-referenced docs.

Requires a live MongoDB (same as test_reliability_indexes_and_lock_ttl.py).
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from bson import ObjectId

from config.database import db
from services.device_cleanup import DEVICE_ID_COLLECTIONS, cascade_delete_device


class DeviceCascadeDeleteTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.device_id = ObjectId()
        self.ip = f"198.51.100.{self.device_id.binary[-1] % 200}"
        self.interface = "Gi1/0/99"

        # Clean any leftover from a previous interrupted run.
        self._purge_all()

        db.devices.insert_one({
            "_id": self.device_id,
            "hostname": "cascade-test-switch",
            "ipAddress": self.ip,
            "deviceType": "Switch",
            "critical": False,
            "monitor": True,
            "status": "Online",
            "createdAt": self.now,
            "updatedAt": self.now,
        })

        # Seed one document per cascade target collection.
        db.pingHistory.insert_one({
            "deviceId": self.device_id,
            "status": "Online",
            "timestamp": self.now,
        })
        db.interfaces.insert_one({
            "deviceId": self.device_id,
            "name": self.interface,
            "adminStatus": "up",
            "operStatus": "up",
        })
        db.interface_stats.insert_one({
            "deviceId": self.device_id,
            "interfaceName": self.interface,
            "timestamp": self.now,
        })
        db.eligibility_results.insert_one({
            "deviceId": self.device_id,
            "interface": self.interface,
            "eligible": True,
            "timestamp": self.now,
        })
        db.storm_risk_history.insert_one({
            "deviceId": self.device_id,
            "interface": self.interface,
            "riskScore": 10.0,
            "timestamp": self.now,
        })
        db.storm_confirmation_history.insert_one({
            "deviceId": self.device_id,
            "interface": self.interface,
            "confirmed": False,
            "timestamp": self.now,
        })
        db.storm_safety_history.insert_one({
            "deviceId": self.device_id,
            "interface": self.interface,
            "safe": True,
            "timestamp": self.now,
        })
        db.storm_incidents.insert_one({
            "incidentId": f"storm-test-{self.device_id}",
            "deviceId": self.device_id,
            "interface": self.interface,
            "status": "OPEN",
            "createdAt": self.now,
        })
        db.storm_mitigation_history.insert_one({
            "incidentId": f"storm-test-{self.device_id}",
            "deviceId": self.device_id,
            "interface": self.interface,
            "status": "SUCCESS",
            "timestamp": self.now,
        })
        db.storm_recovery_history.insert_one({
            "incidentId": f"storm-test-{self.device_id}",
            "deviceId": self.device_id,
            "interface": self.interface,
            "recoveryStatus": "RECOVERED",
            "timestamp": self.now,
        })
        db.storm_mitigation_locks.insert_one({
            "_id": f"device:{self.device_id}",
            "deviceId": self.device_id,
            "createdAt": self.now,
            "expiresAt": self.now,
        })
        db.storm_recovery_locks.insert_one({
            "_id": f"recovery:{self.device_id}",
            "deviceId": self.device_id,
            "createdAt": self.now,
            "expiresAt": self.now,
        })
        db.alerts.insert_one({
            "deviceId": self.device_id,
            "hostname": "cascade-test-switch",
            "ipAddress": self.ip,
            "message": "test alert",
            "createdAt": self.now,
        })

    def tearDown(self):
        self._purge_all()

    def _purge_all(self):
        db.devices.delete_many({"_id": self.device_id})
        filt = {"deviceId": {"$in": [self.device_id, str(self.device_id)]}}
        for name in DEVICE_ID_COLLECTIONS:
            db[name].delete_many(filt)

    def test_cascade_removes_all_device_references(self):
        result = cascade_delete_device(self.device_id)

        self.assertGreaterEqual(result["deviceDeleted"], 1)
        self.assertIsNone(db.devices.find_one({"_id": self.device_id}))

        filt = {"deviceId": {"$in": [self.device_id, str(self.device_id)]}}
        remaining = {}
        for name in DEVICE_ID_COLLECTIONS:
            count = db[name].count_documents(filt)
            if count:
                remaining[name] = count

        self.assertEqual(
            remaining,
            {},
            f"Orphan documents remain after cascade delete: {remaining}",
        )

        # Every seeded collection should report at least one deletion.
        for name in DEVICE_ID_COLLECTIONS:
            self.assertGreaterEqual(
                result["relatedDeleted"].get(name, 0),
                1,
                f"Expected deletions from {name}",
            )


if __name__ == "__main__":
    unittest.main()
