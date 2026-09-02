"""
End-to-end tests for critical-offline alert email claim, delivery, and retry.

Mocks only the SMTP transport boundary (smtplib). Exercises the real path:
apply_ping_result → maybe_send_critical_offline_alert → send_critical_offline_alert
→ send_email → smtplib.
"""

from __future__ import annotations

import copy
import threading
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from services.ping_service import STATUS_OFFLINE_CRITICAL, STATUS_ONLINE
from utils.utc import utc_now


def _smtp_settings():
    return {
        "smtp": {
            "enabled": True,
            "provider": "gmail",
            "host": "smtp.gmail.com",
            "port": 587,
            "user": "alerts@example.com",
            "password": "secret",
            "fromAddress": "alerts@example.com",
            "fromName": "NetPulse",
            "toAddress": "noc@example.com",
            "useTls": True,
        }
    }


def _mock_smtp_server():
    server = MagicMock()
    server.__enter__ = MagicMock(return_value=server)
    server.__exit__ = MagicMock(return_value=False)
    return server


def _critical_device(**overrides):
    device_id = ObjectId()
    base = {
        "_id": device_id,
        "hostname": "sw-core-1",
        "ipAddress": "10.0.0.10",
        "critical": True,
        "monitor": True,
        "deviceType": "Switch",
        "status": STATUS_ONLINE,
        "consecutiveFailures": 0,
    }
    base.update(overrides)
    return base


def _failure_ping_result(*, started=None):
    started = started or utc_now()
    return {
        "success": False,
        "status": STATUS_OFFLINE_CRITICAL,
        "responseTime": None,
        "lastSeen": None,
        "message": "Device is unreachable",
        "pingStartedAt": started,
        "pingCompletedAt": started,
    }


class _FakeAlertsCollection:
    """In-memory alerts store with active critical-offline uniqueness."""

    def __init__(self):
        self.docs: list[dict] = []

    def _matches(self, doc: dict, query: dict) -> bool:
        if not query:
            return True
        for key, expected in query.items():
            if key == "$or":
                if not any(self._matches(doc, branch) for branch in expected):
                    return False
                continue
            if key == "$and":
                if not all(self._matches(doc, branch) for branch in expected):
                    return False
                continue
            if isinstance(expected, dict):
                if "$in" in expected:
                    if doc.get(key) not in expected["$in"]:
                        return False
                    continue
                if "$ne" in expected:
                    if doc.get(key) == expected["$ne"]:
                        return False
                    continue
                if "$exists" in expected:
                    exists = key in doc and doc.get(key) is not None
                    if exists != bool(expected["$exists"]):
                        return False
                    continue
                if "$lte" in expected:
                    value = doc.get(key)
                    if value is None or value > expected["$lte"]:
                        return False
                    continue
            elif doc.get(key) != expected:
                return False
        return True

    def _active_critical_conflict(self, doc: dict) -> bool:
        device_id = doc.get("deviceId")
        for existing in self.docs:
            if (
                existing.get("deviceId") == device_id
                and existing.get("status") == STATUS_OFFLINE_CRITICAL
                and not existing.get("resolved")
                and not existing.get("dismissed")
            ):
                return True
        return False

    def insert_one(self, doc: dict):
        payload = copy.deepcopy(doc)
        if self._active_critical_conflict(payload):
            raise DuplicateKeyError("uniq_alerts_active_critical_offline")
        payload.setdefault("_id", ObjectId())
        self.docs.append(payload)
        return SimpleNamespace(inserted_id=payload["_id"], acknowledged=True)

    def find_one(self, query=None, projection=None):
        query = query or {}
        for doc in self.docs:
            if self._matches(doc, query):
                return copy.deepcopy(doc)
        return None

    def find_one_and_update(self, query, update, return_document=None):
        for doc in self.docs:
            if self._matches(doc, query):
                for key, value in update.get("$set", {}).items():
                    doc[key] = value
                return copy.deepcopy(doc)
        return None

    def update_one(self, query, update):
        doc = self.find_one(query)
        if not doc:
            return SimpleNamespace(matched_count=0, modified_count=0)
        for stored in self.docs:
            if stored["_id"] == doc["_id"]:
                for key, value in update.get("$set", {}).items():
                    stored[key] = value
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    def update_many(self, query, update):
        matched = 0
        modified = 0
        for doc in self.docs:
            if self._matches(doc, query):
                matched += 1
                for key, value in update.get("$set", {}).items():
                    if doc.get(key) != value:
                        doc[key] = value
                        modified += 1
        return SimpleNamespace(matched_count=matched, modified_count=modified)

    def count_documents(self, query):
        return sum(1 for doc in self.docs if self._matches(doc, query or {}))


class CriticalOfflineEmailFlowTests(unittest.TestCase):
    def setUp(self):
        self.alerts = _FakeAlertsCollection()
        self.fake_db = SimpleNamespace(alerts=self.alerts)

    def _patch_alert_db(self):
        return patch("services.alert_service.db", self.fake_db)

    def _apply_failures(self, device, count: int):
        from services.monitor_service import apply_ping_result

        started = utc_now()
        failures = int(device.get("consecutiveFailures") or 0)
        for _ in range(count):
            failures += 1
            updated = {
                **device,
                "consecutiveFailures": failures,
                "status": (
                    STATUS_OFFLINE_CRITICAL if failures >= 2 else STATUS_ONLINE
                ),
                "lastPingAttemptId": f"attempt-{failures}",
                "lastPingStartedAt": started,
            }
            coll = MagicMock()
            coll.find_one_and_update.return_value = updated
            coll.find_one.return_value = None
            fake_devices_db = SimpleNamespace(devices=coll)
            with patch("services.monitor_service._db", return_value=fake_devices_db), \
                 patch("services.monitor_service.save_ping_history"), \
                 patch("services.monitor_service.get_failure_confirmation_scans", return_value=2), \
                 self._patch_alert_db(), \
                 patch("services.alert_service.send_critical_offline_whatsapp_alert"), \
                 patch("services.email_service.get_settings", return_value=_smtp_settings()), \
                 patch("services.email_service.decrypt_secret", side_effect=lambda x: x):
                apply_ping_result(
                    device,
                    _failure_ping_result(started=started),
                    scan_type="Automatic",
                    attempt_id=f"attempt-{failures}",
                )
            device = {**device, **updated}
        return device

    @patch("smtplib.SMTP")
    def test_critical_ping_failure_reaches_smtp_once(self, mock_smtp_cls):
        mock_server = _mock_smtp_server()
        mock_smtp_cls.return_value = mock_server
        device = _critical_device()

        with patch("services.monitor_service.save_ping_history"), \
             patch("services.monitor_service.get_failure_confirmation_scans", return_value=2), \
             self._patch_alert_db(), \
             patch("services.alert_service.send_critical_offline_whatsapp_alert"), \
             patch("services.email_service.get_settings", return_value=_smtp_settings()), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x):
            self._apply_failures(device, 3)

        self.assertEqual(self.alerts.count_documents({}), 1)
        alert = self.alerts.docs[0]
        self.assertTrue(alert.get("emailSent"))
        mock_smtp_cls.assert_called_once()
        mock_server.sendmail.assert_called_once()

    @patch("smtplib.SMTP")
    def test_smtp_failure_then_retry_success(self, mock_smtp_cls):
        mock_server = _mock_smtp_server()
        mock_server.login.side_effect = [Exception("smtp down"), None]
        mock_smtp_cls.return_value = mock_server
        device = _critical_device()

        with patch(
            "services.alert_service.CRITICAL_OFFLINE_EMAIL_RETRY_COOLDOWN_SECONDS",
            0,
        ), patch("services.monitor_service.save_ping_history"), \
             patch("services.monitor_service.get_failure_confirmation_scans", return_value=2), \
             self._patch_alert_db(), \
             patch("services.alert_service.send_critical_offline_whatsapp_alert"), \
             patch("services.email_service.get_settings", return_value=_smtp_settings()), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x):
            device = self._apply_failures(device, 3)
            alert = self.alerts.docs[0]
            self.assertFalse(alert.get("emailSent"))
            self.assertEqual(mock_smtp_cls.call_count, 1)

            alert["emailLastAttemptAt"] = utc_now() - timedelta(seconds=120)
            device = self._apply_failures(device, 1)

        self.assertEqual(self.alerts.count_documents({}), 1)
        self.assertTrue(self.alerts.docs[0].get("emailSent"))
        self.assertEqual(mock_smtp_cls.call_count, 2)

    @patch("smtplib.SMTP")
    def test_no_retry_after_successful_email(self, mock_smtp_cls):
        mock_smtp_cls.return_value = _mock_smtp_server()
        device = _critical_device()

        with patch("services.monitor_service.save_ping_history"), \
             patch("services.monitor_service.get_failure_confirmation_scans", return_value=2), \
             self._patch_alert_db(), \
             patch("services.alert_service.send_critical_offline_whatsapp_alert"), \
             patch("services.email_service.get_settings", return_value=_smtp_settings()), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x):
            device = self._apply_failures(device, 3)
            self._apply_failures(device, 1)

        self.assertEqual(mock_smtp_cls.call_count, 1)

    @patch("smtplib.SMTP")
    def test_concurrent_claim_produces_single_alert_and_email(self, mock_smtp_cls):
        mock_smtp_cls.return_value = _mock_smtp_server()
        device = _critical_device()
        barrier = threading.Barrier(2)
        results: list[bool] = []

        def _worker():
            from services.alert_service import maybe_send_critical_offline_alert

            barrier.wait()
            with patch("services.alert_service.db", self.fake_db), \
                 patch("services.alert_service.send_critical_offline_whatsapp_alert"), \
                 patch("services.email_service.get_settings", return_value=_smtp_settings()), \
                 patch("services.email_service.decrypt_secret", side_effect=lambda x: x):
                results.append(
                    maybe_send_critical_offline_alert(
                        device,
                        STATUS_ONLINE,
                        STATUS_OFFLINE_CRITICAL,
                        consecutive_failures=3,
                    )
                )

        threads = [threading.Thread(target=_worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(self.alerts.count_documents({}), 1)
        self.assertEqual(mock_smtp_cls.call_count, 1)
        self.assertTrue(any(results))

    @patch("smtplib.SMTP")
    def test_failed_email_does_not_create_duplicate_alert(self, mock_smtp_cls):
        mock_server = _mock_smtp_server()
        mock_server.sendmail.side_effect = [Exception("smtp fail"), None]
        mock_smtp_cls.return_value = mock_server
        device = _critical_device()

        with patch(
            "services.alert_service.CRITICAL_OFFLINE_EMAIL_RETRY_COOLDOWN_SECONDS",
            0,
        ), patch("services.monitor_service.save_ping_history"), \
             patch("services.monitor_service.get_failure_confirmation_scans", return_value=2), \
             self._patch_alert_db(), \
             patch("services.alert_service.send_critical_offline_whatsapp_alert"), \
             patch("services.email_service.get_settings", return_value=_smtp_settings()), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x):
            device = self._apply_failures(device, 3)
            self.alerts.docs[0]["emailLastAttemptAt"] = utc_now() - timedelta(seconds=120)
            device = self._apply_failures(device, 1)

        self.assertEqual(self.alerts.count_documents({}), 1)
        self.assertTrue(self.alerts.docs[0].get("emailSent"))
        self.assertEqual(mock_smtp_cls.call_count, 2)

    @patch("smtplib.SMTP")
    def test_recovery_stops_email_retry(self, mock_smtp_cls):
        mock_server = _mock_smtp_server()
        mock_server.login.side_effect = Exception("smtp down")
        mock_smtp_cls.return_value = mock_server
        device = _critical_device(consecutiveFailures=3, status=STATUS_OFFLINE_CRITICAL)

        with patch(
            "services.alert_service.CRITICAL_OFFLINE_EMAIL_RETRY_COOLDOWN_SECONDS",
            0,
        ), self._patch_alert_db(), \
             patch("services.alert_service.send_critical_offline_whatsapp_alert"), \
             patch("services.email_service.get_settings", return_value=_smtp_settings()), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x):
            from services.alert_service import (
                _try_claim_email_retry,
                maybe_send_critical_offline_alert,
                resolve_critical_offline_alerts,
            )

            maybe_send_critical_offline_alert(
                device,
                STATUS_ONLINE,
                STATUS_OFFLINE_CRITICAL,
                consecutive_failures=3,
            )
            alert_id = self.alerts.docs[0]["_id"]
            self.assertFalse(self.alerts.docs[0].get("emailSent"))
            initial_calls = mock_smtp_cls.call_count

            resolve_critical_offline_alerts({**device, "status": STATUS_ONLINE})
            self.assertTrue(self.alerts.docs[0].get("resolved"))
            self.assertFalse(_try_claim_email_retry(alert_id))

        self.assertEqual(mock_smtp_cls.call_count, initial_calls)

    @patch("smtplib.SMTP")
    def test_second_outage_can_email_again(self, mock_smtp_cls):
        mock_smtp_cls.return_value = _mock_smtp_server()
        device = _critical_device()

        with patch("services.monitor_service.save_ping_history"), \
             patch("services.monitor_service.get_failure_confirmation_scans", return_value=2), \
             self._patch_alert_db(), \
             patch("services.alert_service.send_critical_offline_whatsapp_alert"), \
             patch("services.email_service.get_settings", return_value=_smtp_settings()), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x):
            device = self._apply_failures(device, 3)
            from services.alert_service import resolve_critical_offline_alerts

            resolve_critical_offline_alerts(device)
            device = {**device, "status": STATUS_ONLINE, "consecutiveFailures": 0}
            device = self._apply_failures(device, 3)

        self.assertEqual(self.alerts.count_documents({"resolved": True}), 1)
        self.assertEqual(
            self.alerts.count_documents(
                {
                    "status": STATUS_OFFLINE_CRITICAL,
                    "resolved": False,
                    "dismissed": False,
                }
            ),
            1,
        )
        self.assertEqual(mock_smtp_cls.call_count, 2)

    @patch("smtplib.SMTP")
    def test_smtp_failure_does_not_break_monitoring(self, mock_smtp_cls):
        mock_smtp_cls.side_effect = Exception("smtp unavailable")
        device = _critical_device()
        started = utc_now()

        updated = {
            **device,
            "consecutiveFailures": 3,
            "status": STATUS_OFFLINE_CRITICAL,
            "lastPingAttemptId": "attempt-3",
            "lastPingStartedAt": started,
        }
        coll = MagicMock()
        coll.find_one_and_update.return_value = updated
        coll.find_one.return_value = None
        fake_devices_db = SimpleNamespace(devices=coll)

        from services.monitor_service import apply_ping_result

        with patch("services.monitor_service._db", return_value=fake_devices_db), \
             patch("services.monitor_service.save_ping_history"), \
             patch("services.monitor_service.get_failure_confirmation_scans", return_value=2), \
             self._patch_alert_db(), \
             patch("services.alert_service.send_critical_offline_whatsapp_alert"), \
             patch("services.email_service.get_settings", return_value=_smtp_settings()), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x):
            status = apply_ping_result(
                device,
                _failure_ping_result(started=started),
                attempt_id="attempt-3",
            )

        self.assertEqual(status, STATUS_ONLINE)
        self.assertEqual(self.alerts.count_documents({}), 1)
        alert = self.alerts.docs[0]
        self.assertFalse(alert.get("emailSent"))
        self.assertFalse(alert.get("resolved"))
        self.assertEqual(updated["consecutiveFailures"], 3)
        self.assertEqual(updated["status"], STATUS_OFFLINE_CRITICAL)


if __name__ == "__main__":
    unittest.main()
