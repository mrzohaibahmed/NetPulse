"""Tests for Cisco switch hardware fault dashboard + email alerting."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bson import ObjectId

from services.switch_hardware.alerts import (
    claim_hardware_alert,
    evaluate_hardware_alerts,
    resolve_hardware_alert,
)


def _device(**overrides):
    base = {
        "_id": ObjectId(),
        "hostname": "sw1",
        "ipAddress": "10.0.0.1",
        "deviceType": "Managed Switch",
    }
    base.update(overrides)
    return base


class ClaimHardwareAlertTests(unittest.TestCase):
    @patch("services.switch_hardware.alerts.send_switch_hardware_alert_email", return_value=True)
    @patch("services.switch_hardware.alerts.db")
    def test_new_alert_creates_dashboard_entry_and_sends_email(self, mock_db, mock_email):
        device = _device()
        inserted_id = ObjectId()
        mock_db.alerts.insert_one.return_value = MagicMock(
            acknowledged=True, inserted_id=inserted_id
        )

        result = claim_hardware_alert(
            device,
            title="Temperature Critical",
            message="Temperature Critical detected on sw1",
            severity="CRITICAL",
            key="temperature_critical",
        )

        self.assertTrue(result)
        mock_db.alerts.insert_one.assert_called_once()
        doc = mock_db.alerts.insert_one.call_args[0][0]
        self.assertEqual(doc["alertType"], "Switch Hardware")
        self.assertEqual(doc["category"], "Switch Hardware Monitoring")
        self.assertEqual(doc["hardwareAlertKey"], "temperature_critical")
        self.assertEqual(doc["severity"], "CRITICAL")
        self.assertFalse(doc["emailSent"])  # false at insert time; updated after send

        # Email was sent, and emailSent was flipped to True on the inserted alert.
        mock_email.assert_called_once()
        mock_db.alerts.update_one.assert_called_once_with(
            {"_id": inserted_id}, {"$set": {"emailSent": True}}
        )

    @patch("services.switch_hardware.alerts.send_switch_hardware_alert_email")
    @patch("services.switch_hardware.alerts.db")
    def test_duplicate_active_alert_does_not_resend_email(self, mock_db, mock_email):
        from pymongo.errors import DuplicateKeyError

        device = _device()
        mock_db.alerts.insert_one.side_effect = DuplicateKeyError("duplicate")

        result = claim_hardware_alert(
            device,
            title="Fan Failure",
            message="Fan Failure detected on sw1",
            severity="CRITICAL",
            key="fan_failed",
        )

        self.assertFalse(result)
        mock_email.assert_not_called()
        mock_db.alerts.update_one.assert_called_once()
        active_filter, update = mock_db.alerts.update_one.call_args.args
        self.assertEqual(active_filter["deviceId"], device["_id"])
        self.assertEqual(active_filter["hardwareAlertKey"], "fan_failed")
        self.assertEqual(active_filter["message"], {"$ne": "Fan Failure detected on sw1"})
        self.assertEqual(update["$set"]["message"], "Fan Failure detected on sw1")

    @patch("services.switch_hardware.alerts.send_switch_hardware_alert_email")
    @patch("services.switch_hardware.alerts.db")
    def test_email_failure_still_creates_the_alert(self, mock_db, mock_email):
        """Email delivery is best-effort: a broken SMTP config must not
        prevent the dashboard alert itself from being created."""
        device = _device()
        mock_db.alerts.insert_one.return_value = MagicMock(
            acknowledged=True, inserted_id=ObjectId()
        )
        mock_email.side_effect = Exception("SMTP not configured")

        result = claim_hardware_alert(
            device,
            title="Memory Critical",
            message="Memory Critical detected on sw1",
            severity="CRITICAL",
            key="memory_critical",
        )

        self.assertTrue(result)
        mock_db.alerts.update_one.assert_not_called()  # emailSent never flipped

    @patch("services.switch_hardware.alerts.send_switch_hardware_alert_email", return_value=False)
    @patch("services.switch_hardware.alerts.db")
    def test_email_not_sent_leaves_email_sent_false(self, mock_db, _mock_email):
        """SMTP disabled/unconfigured: send_email returns False (never raises);
        the alert is still created but emailSent is not updated to True."""
        device = _device()
        mock_db.alerts.insert_one.return_value = MagicMock(
            acknowledged=True, inserted_id=ObjectId()
        )

        result = claim_hardware_alert(
            device,
            title="CPU Warning",
            message="CPU Warning detected on sw1",
            severity="WARNING",
            key="cpu_warning",
        )

        self.assertTrue(result)
        mock_db.alerts.update_one.assert_not_called()


class ResolveHardwareAlertTests(unittest.TestCase):
    @patch(
        "services.switch_hardware.alerts.send_switch_hardware_recovery_alert_email",
        return_value=True,
    )
    @patch("services.switch_hardware.alerts.db")
    def test_recovery_resolves_alert_and_sends_recovery_email(self, mock_db, mock_email):
        device = _device()
        alert_id = ObjectId()
        active_alert = {
            "_id": alert_id,
            "deviceId": device["_id"],
            "hardwareAlertKey": "temperature_critical",
            "title": "Temperature Critical",
            "resolved": False,
            "dismissed": False,
        }
        mock_db.alerts.find.return_value = [active_alert]

        resolve_hardware_alert(device, "temperature_critical")

        mock_db.alerts.update_many.assert_called_once()
        filter_arg, update_arg = mock_db.alerts.update_many.call_args[0]
        self.assertEqual(filter_arg["deviceId"], device["_id"])
        self.assertEqual(filter_arg["hardwareAlertKey"], "temperature_critical")
        self.assertTrue(update_arg["$set"]["resolved"])

        mock_email.assert_called_once_with(device, active_alert)
        mock_db.alerts.update_one.assert_called_once_with(
            {"_id": alert_id}, {"$set": {"recoveryEmailSent": True}}
        )

    @patch("services.switch_hardware.alerts.send_switch_hardware_recovery_alert_email")
    @patch("services.switch_hardware.alerts.db")
    def test_no_active_alert_is_a_no_op(self, mock_db, mock_email):
        device = _device()
        mock_db.alerts.find.return_value = []

        resolve_hardware_alert(device, "fan_failed")

        mock_db.alerts.update_many.assert_not_called()
        mock_email.assert_not_called()

    @patch(
        "services.switch_hardware.alerts.send_switch_hardware_recovery_alert_email",
        side_effect=Exception("SMTP down"),
    )
    @patch("services.switch_hardware.alerts.db")
    def test_recovery_email_failure_does_not_raise(self, mock_db, _mock_email):
        device = _device()
        active_alert = {
            "_id": ObjectId(),
            "deviceId": device["_id"],
            "hardwareAlertKey": "psu_failed",
            "resolved": False,
            "dismissed": False,
        }
        mock_db.alerts.find.return_value = [active_alert]

        # Must not raise even though the email call blows up.
        resolve_hardware_alert(device, "psu_failed")

        mock_db.alerts.update_many.assert_called_once()


class EvaluateHardwareAlertsMappingTests(unittest.TestCase):
    """Every documented fault condition (fan, CPU, memory, temperature, PSU,
    hardware alarm) must reach claim_hardware_alert with the right key."""

    @patch("services.switch_hardware.alerts.resolve_hardware_alert")
    @patch("services.switch_hardware.alerts.claim_hardware_alert", return_value=True)
    def test_each_fault_condition_claims_its_own_key(self, mock_claim, _mock_resolve):
        device = _device()
        cases = [
            ({"temperature": {"status": "critical"}}, "temperature_critical"),
            ({"temperature": {"status": "warning"}}, "temperature_warning"),
            ({"fans": {"failedCount": 1}}, "fan_failed"),
            ({"powerSupplies": {"failedCount": 1}}, "psu_failed"),
            ({"cpu": {"status": "critical"}}, "cpu_critical"),
            ({"cpu": {"status": "warning"}}, "cpu_warning"),
            ({"memory": {"status": "critical"}}, "memory_critical"),
            ({"memory": {"status": "warning"}}, "memory_warning"),
            ({"hardwareAlarms": [{"message": "PSU fault"}]}, "hardware_alarm"),
        ]
        for snapshot, expected_key in cases:
            with self.subTest(expected_key=expected_key):
                mock_claim.reset_mock()
                evaluate_hardware_alerts(device, snapshot)
                claimed_keys = {c.kwargs["key"] for c in mock_claim.call_args_list}
                self.assertIn(expected_key, claimed_keys)

    @patch("services.switch_hardware.alerts.resolve_hardware_alert")
    @patch("services.switch_hardware.alerts.claim_hardware_alert", return_value=True)
    def test_healthy_snapshot_resolves_everything_with_full_device(
        self, _mock_claim, mock_resolve
    ):
        device = _device()
        evaluate_hardware_alerts(
            device,
            {
                "temperature": {"status": "healthy"},
                "fans": {"failedCount": 0},
                "powerSupplies": {"failedCount": 0},
                "cpu": {"status": "healthy"},
                "memory": {"status": "healthy"},
                "hardwareAlarms": [],
            },
        )
        # resolve_hardware_alert must receive the full device dict (needed to
        # build a meaningful recovery email), not just an id.
        for call in mock_resolve.call_args_list:
            self.assertEqual(call.args[0], device)

    @patch("services.switch_hardware.alerts.resolve_hardware_alert")
    @patch("services.switch_hardware.alerts.claim_hardware_alert", return_value=True)
    def test_alert_messages_identify_faults_and_readings(self, mock_claim, _mock_resolve):
        device = _device()
        cases = [
            (
                "temperature_critical",
                {"temperature": {"status": "critical", "sensors": [
                    {"name": "Inlet", "status": "critical", "value": 89, "unit": "C", "criticalThreshold": 80},
                    {"name": "Outlet", "status": "healthy", "value": 40, "unit": "C"},
                ]}},
                ["Inlet", "89 °C", "80 °C"],
                ["Outlet"],
            ),
            (
                "fan_failed",
                {"fans": {"failedCount": 1, "items": [
                    {"name": "Fan 2", "status": "critical", "speedRpm": 0},
                    {"name": "Fan 1", "status": "healthy", "speedRpm": 4000},
                ]}},
                ["Fan 2", "0 RPM"],
                ["Fan 1"],
            ),
            (
                "psu_failed",
                {"powerSupplies": {"failedCount": 1, "items": [
                    {"name": "PSU B", "status": "critical", "inputStatus": "offline", "outputStatus": "failed"},
                ]}},
                ["PSU B", "input offline", "output failed"],
                [],
            ),
            (
                "cpu_critical",
                {"cpu": {"status": "critical", "utilizationPercent": 97.5}},
                ["CPU utilization", "97.5%", "threshold 95%"],
                [],
            ),
            (
                "memory_warning",
                {"memory": {"status": "warning", "utilizationPercent": 88}},
                ["Memory utilization", "88%", "threshold 85%"],
                [],
            ),
            (
                "hardware_alarm",
                {"hardwareAlarms": [{"severity": "critical", "message": "PSU fault"}]},
                ["Critical: PSU fault"],
                [],
            ),
        ]
        for key, snapshot, expected, absent in cases:
            with self.subTest(key=key):
                mock_claim.reset_mock()
                evaluate_hardware_alerts(device, snapshot)
                message = next(
                    call.kwargs["message"]
                    for call in mock_claim.call_args_list if call.kwargs["key"] == key
                )
                for detail in expected:
                    self.assertIn(detail, message)
                for detail in absent:
                    self.assertNotIn(detail, message)

    @patch("services.switch_hardware.alerts.resolve_hardware_alert")
    @patch("services.switch_hardware.alerts.claim_hardware_alert", return_value=True)
    def test_missing_component_details_are_not_invented(self, mock_claim, _mock_resolve):
        evaluate_hardware_alerts(_device(), {"fans": {"failedCount": 2}})
        self.assertIn("Failed fans: 2; individual fan details unavailable", mock_claim.call_args.kwargs["message"])


if __name__ == "__main__":
    unittest.main()
