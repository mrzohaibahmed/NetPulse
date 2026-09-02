"""Tests for ISP offline/recovery alerting."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bson import ObjectId

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_mock_db_module = MagicMock()
_mock_db_module.db = MagicMock()
_mock_db_module.MAX_SCAN_THREADS = 5
sys.modules.setdefault("config.database", _mock_db_module)

from models.isp_connection import STATUS_OFFLINE, STATUS_ONLINE  # noqa: E402
from services.alert_service import CRITICAL_OFFLINE_ALERT_THRESHOLD  # noqa: E402


def _sample_isp(**overrides):
    base = {
        "_id": "isp-1",
        "name": "Multinet",
        "target": "8.8.8.8",
        "location": "Mill",
        "monitor": True,
        "status": STATUS_ONLINE,
        "consecutiveFailures": 0,
    }
    base.update(overrides)
    return base


class IspAlertServiceTests(unittest.TestCase):
    def setUp(self):
        self.mock_db = _mock_db_module.db
        self.mock_db.reset_mock()
        self.alerts = MagicMock()
        self.mock_db.alerts = self.alerts

    @patch("services.isp_alert_service.publish")
    @patch("services.isp_alert_service.send_isp_offline_alert", return_value=True)
    def test_below_threshold_no_alert_or_email(self, mock_email, _publish):
        from services.isp_alert_service import maybe_send_isp_offline_alert

        for failures in (1, 2):
            with self.subTest(failures=failures):
                self.alerts.find_one.return_value = None
                result = maybe_send_isp_offline_alert(
                    _sample_isp(),
                    consecutive_failures=failures,
                )
                self.assertFalse(result)
                mock_email.assert_not_called()
                self.alerts.insert_one.assert_not_called()

    @patch("services.isp_alert_service.publish")
    @patch("services.isp_alert_service.send_isp_offline_alert", return_value=True)
    @patch("services.isp_alert_service.with_mongo_retry", side_effect=lambda fn, **_: fn())
    @patch("services.isp_alert_service.assert_insert_acknowledged")
    def test_threshold_reached_creates_alert_and_email(
        self,
        _assert_insert,
        _retry,
        mock_email,
        _publish,
    ):
        from services.isp_alert_service import maybe_send_isp_offline_alert

        self.alerts.find_one.return_value = None
        inserted_id = ObjectId()
        self.alerts.insert_one.return_value = MagicMock(inserted_id=inserted_id)

        result = maybe_send_isp_offline_alert(
            _sample_isp(),
            consecutive_failures=CRITICAL_OFFLINE_ALERT_THRESHOLD,
        )

        self.assertTrue(result)
        mock_email.assert_called_once()
        self.alerts.insert_one.assert_called_once()
        doc = self.alerts.insert_one.call_args[0][0]
        self.assertEqual(doc["alertType"], "ISP Offline")
        self.assertEqual(doc["ispId"], "isp-1")
        self.assertEqual(doc["severity"], "CRITICAL")

    @patch("services.isp_alert_service.publish")
    @patch("services.isp_alert_service.send_isp_offline_alert", return_value=True)
    @patch("services.isp_alert_service.with_mongo_retry", side_effect=lambda fn, **_: fn())
    @patch("services.isp_alert_service.assert_insert_acknowledged")
    def test_continued_outage_does_not_duplicate(
        self,
        _assert_insert,
        _retry,
        mock_email,
        _publish,
    ):
        from services.isp_alert_service import maybe_send_isp_offline_alert

        self.alerts.find_one.return_value = {"_id": ObjectId()}
        for failures in (3, 4, 5):
            with self.subTest(failures=failures):
                result = maybe_send_isp_offline_alert(
                    _sample_isp(),
                    consecutive_failures=failures,
                )
                self.assertFalse(result)
        mock_email.assert_not_called()
        self.alerts.insert_one.assert_not_called()

    @patch("services.isp_alert_service.publish")
    @patch("services.isp_alert_service.send_isp_recovery_alert", return_value=True)
    @patch("services.isp_alert_service.with_mongo_retry", side_effect=lambda fn, **_: fn())
    @patch("services.isp_alert_service.assert_update_acknowledged")
    def test_recovery_resolves_alert_and_sends_email(
        self,
        _assert_update,
        _retry,
        mock_recovery_email,
        _publish,
    ):
        from services.isp_alert_service import resolve_isp_offline_alerts

        alert_id = ObjectId()
        self.alerts.find.return_value = [
            {
                "_id": alert_id,
                "ispId": "isp-1",
                "alertType": "ISP Offline",
            }
        ]
        self.alerts.update_many.return_value = MagicMock(modified_count=1)

        modified = resolve_isp_offline_alerts(_sample_isp(status=STATUS_ONLINE))

        self.assertEqual(modified, 1)
        mock_recovery_email.assert_called_once()
        self.alerts.update_one.assert_called_with(
            {"_id": alert_id},
            {"$set": {"recoveryEmailSent": True}},
        )

    @patch("services.isp_alert_service.send_isp_recovery_alert")
    def test_healthy_isp_sends_no_recovery_email(self, mock_recovery_email):
        from services.isp_alert_service import resolve_isp_offline_alerts

        self.alerts.find.return_value = []
        modified = resolve_isp_offline_alerts(_sample_isp(status=STATUS_ONLINE))
        self.assertEqual(modified, 0)
        mock_recovery_email.assert_not_called()

    @patch("services.isp_alert_service.publish")
    @patch("services.isp_alert_service.send_isp_offline_alert", return_value=True)
    @patch("services.isp_alert_service.with_mongo_retry", side_effect=lambda fn, **_: fn())
    @patch("services.isp_alert_service.assert_insert_acknowledged")
    def test_new_outage_after_recovery_creates_second_incident(
        self,
        _assert_insert,
        _retry,
        mock_email,
        _publish,
    ):
        from services.isp_alert_service import maybe_send_isp_offline_alert

        self.alerts.find_one.side_effect = [None, None]
        self.alerts.insert_one.side_effect = [
            MagicMock(inserted_id=ObjectId()),
            MagicMock(inserted_id=ObjectId()),
        ]

        self.assertTrue(
            maybe_send_isp_offline_alert(
                _sample_isp(),
                consecutive_failures=CRITICAL_OFFLINE_ALERT_THRESHOLD,
            )
        )
        self.assertTrue(
            maybe_send_isp_offline_alert(
                _sample_isp(),
                consecutive_failures=CRITICAL_OFFLINE_ALERT_THRESHOLD,
            )
        )
        self.assertEqual(mock_email.call_count, 2)
        self.assertEqual(self.alerts.insert_one.call_count, 2)

    @patch("services.isp_alert_service.publish")
    @patch("services.isp_alert_service.send_isp_offline_alert", return_value=True)
    @patch("services.isp_alert_service.with_mongo_retry", side_effect=lambda fn, **_: fn())
    @patch("services.isp_alert_service.assert_insert_acknowledged")
    def test_multiple_isps_are_independent(
        self,
        _assert_insert,
        _retry,
        mock_email,
        _publish,
    ):
        from services.isp_alert_service import maybe_send_isp_offline_alert

        self.alerts.find_one.return_value = None
        self.alerts.insert_one.return_value = MagicMock(inserted_id=ObjectId())

        self.assertTrue(
            maybe_send_isp_offline_alert(
                _sample_isp(_id="isp-1", name="Multinet"),
                consecutive_failures=CRITICAL_OFFLINE_ALERT_THRESHOLD,
            )
        )
        self.assertFalse(
            maybe_send_isp_offline_alert(
                _sample_isp(_id="isp-2", name="CyberNet"),
                consecutive_failures=1,
            )
        )
        self.assertEqual(mock_email.call_count, 1)
        self.assertEqual(self.alerts.insert_one.call_count, 1)

    @patch("services.isp_alert_service.send_isp_offline_alert")
    def test_unconfigured_isp_target_skips_alert(self, mock_email):
        from services.isp_alert_service import maybe_send_isp_offline_alert

        result = maybe_send_isp_offline_alert(
            _sample_isp(target=""),
            consecutive_failures=CRITICAL_OFFLINE_ALERT_THRESHOLD,
        )
        self.assertFalse(result)
        mock_email.assert_not_called()
        self.alerts.insert_one.assert_not_called()

    @patch("services.isp_alert_service.publish")
    @patch("services.isp_alert_service.send_isp_offline_alert", side_effect=RuntimeError("smtp down"))
    @patch("services.isp_alert_service.with_mongo_retry", side_effect=lambda fn, **_: fn())
    @patch("services.isp_alert_service.assert_insert_acknowledged")
    def test_email_failure_still_creates_alert(
        self,
        _assert_insert,
        _retry,
        _mock_email,
        _publish,
    ):
        from services.isp_alert_service import maybe_send_isp_offline_alert

        self.alerts.find_one.return_value = None
        self.alerts.insert_one.return_value = MagicMock(inserted_id=ObjectId())

        result = maybe_send_isp_offline_alert(
            _sample_isp(),
            consecutive_failures=CRITICAL_OFFLINE_ALERT_THRESHOLD,
        )

        self.assertTrue(result)
        self.alerts.insert_one.assert_called_once()
        doc = self.alerts.insert_one.call_args[0][0]
        self.assertFalse(doc["emailSent"])

    @patch("services.isp_alert_service.publish")
    @patch("services.isp_alert_service.send_isp_offline_alert", return_value=True)
    def test_restart_safety_active_incident_prevents_duplicate(self, mock_email, _publish):
        from services.isp_alert_service import maybe_send_isp_offline_alert

        self.alerts.find_one.return_value = {"_id": ObjectId()}
        result = maybe_send_isp_offline_alert(
            _sample_isp(status=STATUS_OFFLINE),
            consecutive_failures=10,
        )
        self.assertFalse(result)
        mock_email.assert_not_called()
        self.alerts.insert_one.assert_not_called()


class IspMonitorAlertIntegrationTests(unittest.TestCase):
    @patch("services.isp_alert_service.resolve_isp_offline_alerts")
    @patch("services.isp_alert_service.maybe_send_isp_offline_alert")
    @patch("services.isp_monitor_service.get_failure_confirmation_scans", return_value=2)
    @patch("services.isp_monitor_service._db")
    def test_apply_success_triggers_recovery_handler(
        self,
        mock_db,
        _threshold,
        mock_offline,
        mock_resolve,
    ):
        from services.isp_monitor_service import apply_isp_ping_result

        isp = _sample_isp()
        updated = {**isp, "status": STATUS_ONLINE, "consecutiveFailures": 0}
        coll = MagicMock()
        coll.find_one_and_update.return_value = updated
        mock_db.return_value.ispConnections = coll

        apply_isp_ping_result(
            isp,
            {"success": True, "responseTime": 10.0, "pingStartedAt": None, "pingCompletedAt": None},
            attempt_id="attempt-1",
        )

        mock_resolve.assert_called_once()
        mock_offline.assert_not_called()

    @patch("services.isp_alert_service.resolve_isp_offline_alerts")
    @patch("services.isp_alert_service.maybe_send_isp_offline_alert")
    @patch("services.isp_monitor_service.get_failure_confirmation_scans", return_value=2)
    @patch("services.isp_monitor_service._db")
    def test_apply_failure_triggers_offline_handler(
        self,
        mock_db,
        _threshold,
        mock_offline,
        mock_resolve,
    ):
        from services.isp_monitor_service import apply_isp_ping_result

        isp = _sample_isp()
        updated = {**isp, "status": STATUS_OFFLINE, "consecutiveFailures": 3}
        coll = MagicMock()
        coll.find_one_and_update.return_value = updated
        mock_db.return_value.ispConnections = coll

        apply_isp_ping_result(
            isp,
            {"success": False, "pingStartedAt": None, "pingCompletedAt": None},
            attempt_id="attempt-1",
        )

        mock_offline.assert_called_once()
        mock_resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
