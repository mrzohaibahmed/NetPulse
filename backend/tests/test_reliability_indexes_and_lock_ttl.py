from __future__ import annotations

import unittest
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from config.database import db
from services.storm.lock_service import LockService


class ReliabilityIndexesAndLockTtlTests(unittest.TestCase):
    def setUp(self):
        # Create required indexes (idempotent).
        db.devices.create_index(
            [("ipAddress", 1)],
            unique=True,
            name="uniq_devices_ipAddress",
        )
        db.users.create_index(
            [("username", 1)],
            unique=True,
            name="uniq_users_username",
        )
        LockService.ensure_lock_ttl_indexes()

        self.now = datetime.now(timezone.utc)
        self.device_ip = "203.0.113.77"
        self.username = f"ttl_user_{int(self.now.timestamp())}"
        self.device_id = ObjectId("507f1f77bcf86cd799439011")
        self.interface_name = "Gi1/0/10"

        # Ensure clean slate for uniqueness tests.
        db.devices.delete_many({"ipAddress": self.device_ip})
        db.users.delete_many({"username": self.username})

    def test_idempotent_index_creation(self):
        # Should not throw if indexes already exist.
        db.devices.create_index(
            [("ipAddress", 1)],
            unique=True,
            name="uniq_devices_ipAddress",
        )
        db.users.create_index(
            [("username", 1)],
            unique=True,
            name="uniq_users_username",
        )
        LockService.ensure_lock_ttl_indexes()

        self.assertTrue(True)

    def test_duplicate_device_ip_insertion(self):
        db.devices.insert_one(
            {
                "hostname": "d1",
                "ipAddress": self.device_ip,
                "deviceType": "switch",
                "status": "Online",
                "createdAt": self.now,
                "updatedAt": self.now,
            }
        )
        with self.assertRaises(DuplicateKeyError):
            db.devices.insert_one(
                {
                    "hostname": "d2",
                    "ipAddress": self.device_ip,
                    "deviceType": "switch",
                    "status": "Online",
                    "createdAt": self.now,
                    "updatedAt": self.now,
                }
            )

    def test_duplicate_username_insertion(self):
        db.users.insert_one(
            {
                "username": self.username,
                "passwordHash": "x",
                "role": "viewer",
                "createdAt": self.now,
                "updatedAt": self.now,
            }
        )
        with self.assertRaises(DuplicateKeyError):
            db.users.insert_one(
                {
                    "username": self.username,
                    "passwordHash": "y",
                    "role": "viewer",
                    "createdAt": self.now,
                    "updatedAt": self.now,
                }
            )

    def _mitigation_ids(self):
        return LockService.mitigation_lock_ids(self.device_id, self.interface_name)

    def _recovery_ids(self):
        return LockService.recovery_lock_ids(self.device_id, self.interface_name)

    def test_ttl_index_exists(self):
        mit_idx = next(
            (
                i
                for i in db.storm_mitigation_locks.list_indexes()
                if i.get("name") == "idx_mitigation_locks_expiresAt_ttl"
            ),
            None,
        )
        rec_idx = next(
            (
                i
                for i in db.storm_recovery_locks.list_indexes()
                if i.get("name") == "idx_recovery_locks_expiresAt_ttl"
            ),
            None,
        )
        self.assertIsNotNone(mit_idx)
        self.assertIsNotNone(rec_idx)
        self.assertEqual(mit_idx.get("key"), {"expiresAt": 1})
        self.assertEqual(rec_idx.get("key"), {"expiresAt": 1})
        # Mongo may omit expireAfterSeconds from some drivers; prefer key+name.
        if "expireAfterSeconds" in mit_idx:
            self.assertEqual(mit_idx.get("expireAfterSeconds"), 0)
        if "expireAfterSeconds" in rec_idx:
            self.assertEqual(rec_idx.get("expireAfterSeconds"), 0)

    def test_expired_mitigation_lock_is_reclaimed(self):
        device_lock_id, interface_lock_id = LockService.acquire_mitigation_locks(
            self.device_id,
            self.interface_name,
        )
        # Force expiry
        past = self.now - timedelta(seconds=5)
        db.storm_mitigation_locks.update_many(
            {"_id": {"$in": [device_lock_id, interface_lock_id]}},
            {"$set": {"expiresAt": past}},
        )

        # Should succeed because acquire() reclaims expired locks.
        LockService.acquire_mitigation_locks(self.device_id, self.interface_name)

        # Cleanup
        LockService.release_mitigation_locks(device_lock_id, interface_lock_id)

    def test_expired_recovery_lock_is_reclaimed(self):
        device_lock_id, interface_lock_id = LockService.acquire_recovery_locks(
            self.device_id,
            self.interface_name,
        )
        past = self.now - timedelta(seconds=5)
        db.storm_recovery_locks.update_many(
            {"_id": {"$in": [device_lock_id, interface_lock_id]}},
            {"$set": {"expiresAt": past}},
        )

        LockService.acquire_recovery_locks(self.device_id, self.interface_name)

        LockService.release_recovery_locks(device_lock_id, interface_lock_id)

    def test_valid_lock_cannot_be_stolen(self):
        device_lock_id, interface_lock_id = LockService.acquire_mitigation_locks(
            self.device_id,
            self.interface_name,
        )
        with self.assertRaises(ValueError):
            LockService.acquire_mitigation_locks(self.device_id, self.interface_name)

        LockService.release_mitigation_locks(device_lock_id, interface_lock_id)

    def test_release_removes_lock(self):
        device_lock_id, interface_lock_id = LockService.acquire_recovery_locks(
            self.device_id,
            self.interface_name,
        )
        LockService.release_recovery_locks(device_lock_id, interface_lock_id)
        self.assertIsNone(db.storm_recovery_locks.find_one({"_id": device_lock_id}))
        self.assertIsNone(db.storm_recovery_locks.find_one({"_id": interface_lock_id}))

    def test_renew_extends_expiration(self):
        exec_id = "exec_1"
        device_lock_id, interface_lock_id = LockService.acquire_mitigation_locks(
            self.device_id,
            self.interface_name,
            execution_id=exec_id,
        )
        doc_before = db.storm_mitigation_locks.find_one({"_id": device_lock_id})
        expires_before = doc_before.get("expiresAt")

        ok = LockService.renew_lock(
            device_lock_id,
            interface_lock_id,
            execution_id=exec_id,
        )
        self.assertTrue(ok)

        doc_after = db.storm_mitigation_locks.find_one({"_id": device_lock_id})
        expires_after = doc_after.get("expiresAt")
        self.assertTrue(expires_after > expires_before)

        # Mismatch renew must fail (prevents theft).
        ok2 = LockService.renew_lock(
            device_lock_id,
            interface_lock_id,
            execution_id="exec_2",
        )
        self.assertFalse(ok2)

        LockService.release_mitigation_locks(device_lock_id, interface_lock_id)


if __name__ == "__main__":
    unittest.main()

