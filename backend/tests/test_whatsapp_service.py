"""
Unit tests for WhatsApp Cloud API alert notifications.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from pymongo.errors import DuplicateKeyError

_mock_db_module = MagicMock()
_mock_db_module.db = MagicMock()
sys.modules.setdefault("config.database", _mock_db_module)

_mock_pymongo = MagicMock()
_mock_pymongo_errors = MagicMock()
_mock_pymongo_errors.DuplicateKeyError = type("DuplicateKeyError", (Exception,), {})
_mock_pymongo.errors = _mock_pymongo_errors
sys.modules.setdefault("pymongo", _mock_pymongo)
sys.modules.setdefault("pymongo.errors", _mock_pymongo_errors)

os.environ.setdefault("JWT_SECRET", "netpulse-test-jwt-secret-do-not-use-in-production-32c+")
os.environ.setdefault("FLASK_DEBUG", "true")
os.environ.setdefault("NETPULSE_ENV", "development")
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/netpulse_test")
os.environ.setdefault("DATABASE_NAME", "netpulse_test")

from services import whatsapp_service  # noqa: E402


def _device():
    return {
        "hostname": "Core-SW-01",
        "ipAddress": "192.168.1.1",
        "deviceType": "Switch",
    }


class WhatsAppDisabledTests(unittest.TestCase):
    @patch.dict(
        os.environ,
        {"WHATSAPP_ALERTS_ENABLED": "false"},
        clear=False,
    )
    def test_disabled_sends_no_request(self):
        with patch("services.whatsapp_service._send_template_to_recipient") as mock_send:
            with patch("config.whatsapp.WHATSAPP_ALERTS_ENABLED", False):
                ok = whatsapp_service.send_critical_offline_whatsapp_alert(_device())
        self.assertFalse(ok)
        mock_send.assert_not_called()


class WhatsAppMissingConfigTests(unittest.TestCase):
    def setUp(self):
        whatsapp_service._config_error_logged = False

    @patch.dict(
        os.environ,
        {
            "WHATSAPP_ALERTS_ENABLED": "true",
            "WHATSAPP_ACCESS_TOKEN": "",
            "WHATSAPP_PHONE_NUMBER_ID": "",
            "WHATSAPP_RECIPIENT_NUMBERS": "",
        },
        clear=False,
    )
    def test_missing_credentials_skipped(self):
        with patch("config.whatsapp.WHATSAPP_ALERTS_ENABLED", True), patch(
            "config.whatsapp.WHATSAPP_ACCESS_TOKEN", ""
        ), patch("config.whatsapp.WHATSAPP_PHONE_NUMBER_ID", ""), patch(
            "config.whatsapp.WHATSAPP_RECIPIENT_NUMBERS", ""
        ), patch(
            "services.whatsapp_service._send_template_to_recipient"
        ) as mock_send:
            ok = whatsapp_service.send_critical_offline_whatsapp_alert(_device())
        self.assertFalse(ok)
        mock_send.assert_not_called()


class WhatsAppEnabledTests(unittest.TestCase):
    def setUp(self):
        whatsapp_service._config_error_logged = False

    def _enable_config(self):
        return patch.multiple(
            "config.whatsapp",
            WHATSAPP_ALERTS_ENABLED=True,
            WHATSAPP_ACCESS_TOKEN="token",
            WHATSAPP_PHONE_NUMBER_ID="12345",
            WHATSAPP_API_VERSION="v21.0",
            WHATSAPP_RECIPIENT_NUMBERS="923001234567,923111234567",
            WHATSAPP_CRITICAL_ALERT_TEMPLATE="netpulse_critical_alert",
            WHATSAPP_RECOVERY_ALERT_TEMPLATE="netpulse_device_recovery",
            WHATSAPP_TEMPLATE_LANGUAGE="en",
            WHATSAPP_REQUEST_TIMEOUT_SECONDS=10,
            WHATSAPP_CRITICAL_ALERTS_ENABLED=True,
            WHATSAPP_RECOVERY_ALERTS_ENABLED=True,
        )

    def test_critical_alert_calls_service_for_each_recipient(self):
        with self._enable_config(), patch(
            "services.whatsapp_service._send_template_to_recipient",
            return_value=(True, ""),
        ) as mock_send:
            ok = whatsapp_service.send_critical_offline_whatsapp_alert(_device())
        self.assertTrue(ok)
        self.assertEqual(mock_send.call_count, 2)
        args = mock_send.call_args_list[0].args
        self.assertEqual(args[0], "923001234567")
        self.assertEqual(args[1], "netpulse_critical_alert")
        self.assertEqual(args[2][0], "Core-SW-01")
        self.assertEqual(args[2][1], "192.168.1.1")
        self.assertEqual(args[2][2], "OFFLINE")
        self.assertEqual(args[2][3], "CRITICAL")

    def test_recovery_alert_uses_recovery_template(self):
        with self._enable_config(), patch(
            "services.whatsapp_service._send_template_to_recipient",
            return_value=(True, ""),
        ) as mock_send:
            ok = whatsapp_service.send_device_recovery_whatsapp_alert(_device())
        self.assertTrue(ok)
        self.assertEqual(mock_send.call_count, 2)
        self.assertEqual(mock_send.call_args_list[0].args[1], "netpulse_device_recovery")
        self.assertEqual(mock_send.call_args_list[0].args[2][2], "ONLINE")

    def test_api_failure_does_not_raise(self):
        with self._enable_config(), patch(
            "services.whatsapp_service._send_template_to_recipient",
            return_value=(False, "HTTP 401"),
        ):
            ok = whatsapp_service.send_critical_offline_whatsapp_alert(_device())
        self.assertFalse(ok)

    def test_parse_recipients_strips_formatting(self):
        recipients = whatsapp_service._parse_recipients("+92 300 1234567, +923111234567")
        self.assertEqual(recipients, ["923001234567", "923111234567"])

    def test_mask_recipient_hides_full_number(self):
        masked = whatsapp_service._mask_recipient("923001234567")
        self.assertEqual(masked, "...4567")
        self.assertNotIn("923001234567", masked)

    def test_build_template_payload_structure(self):
        cfg = whatsapp_service._whatsapp_settings()
        payload = whatsapp_service._build_template_payload(
            "923001234567",
            "netpulse_critical_alert",
            ["Core-SW-01", "192.168.1.1", "OFFLINE", "CRITICAL", "2026-08-29 12:15:00 UTC"],
            cfg=cfg,
        )
        self.assertEqual(payload["messaging_product"], "whatsapp")
        self.assertEqual(payload["to"], "923001234567")
        self.assertEqual(payload["type"], "template")
        self.assertEqual(payload["template"]["name"], "netpulse_critical_alert")
        self.assertEqual(len(payload["template"]["components"][0]["parameters"]), 5)

    @patch("urllib.request.urlopen")
    def test_http_request_uses_bearer_auth(self, mock_urlopen):
        response = MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        mock_urlopen.return_value = response

        cfg = {
            "accessToken": "secret-token",
            "phoneNumberId": "12345",
            "apiVersion": "v21.0",
            "templateLanguage": "en",
            "timeoutSeconds": 10,
        }
        sent, _ = whatsapp_service._send_template_to_recipient(
            "923001234567",
            "netpulse_critical_alert",
            ["a", "b", "c", "d", "e"],
            cfg=cfg,
            device_name="Core-SW-01",
            alert_kind="critical",
        )
        self.assertTrue(sent)
        request = mock_urlopen.call_args.args[0]
        self.assertIn("Bearer secret-token", request.headers["Authorization"])
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["template"]["name"], "netpulse_critical_alert")

    def test_public_status_never_exposes_token(self):
        with self._enable_config():
            status = whatsapp_service.get_public_whatsapp_status()
        self.assertTrue(status["enabled"])
        self.assertTrue(status["configured"])
        self.assertEqual(status["recipientCount"], 2)
        self.assertNotIn("token", json.dumps(status).lower())
        self.assertNotIn("access", json.dumps(status).lower())


class WhatsAppAlertIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import services.alert_service  # noqa: F401, PLC0415

    @patch("services.alert_service.send_critical_offline_whatsapp_alert", return_value=True)
    @patch("services.alert_service.send_critical_offline_alert", return_value=True)
    @patch("services.alert_service.db")
    def test_critical_offline_triggers_whatsapp_after_insert(
        self, mock_db, _email, mock_whatsapp
    ):
        from bson import ObjectId

        from services.alert_service import maybe_send_critical_offline_alert

        device = {
            "_id": ObjectId(),
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "critical": True,
            "monitor": True,
        }
        insert_result = MagicMock()
        insert_result.acknowledged = True
        insert_result.inserted_id = ObjectId()
        mock_db.alerts.insert_one.return_value = insert_result
        mock_db.alerts.find_one.return_value = None

        ok = maybe_send_critical_offline_alert(
            device,
            "Online",
            "Offline (Critical)",
            consecutive_failures=3,
        )
        self.assertTrue(ok)
        mock_whatsapp.assert_called_once_with(device, scan_type="Automatic")

    @patch("services.alert_service.send_critical_offline_whatsapp_alert")
    @patch("services.alert_service.send_critical_offline_alert", return_value=True)
    @patch("services.alert_service.db")
    def test_duplicate_event_skips_whatsapp(self, mock_db, _email, mock_whatsapp):
        from bson import ObjectId

        from services.alert_service import maybe_send_critical_offline_alert

        mock_db.alerts.insert_one.side_effect = DuplicateKeyError("uniq")
        mock_db.alerts.find_one.return_value = {
            "_id": ObjectId(),
            "emailSent": True,
            "resolved": False,
            "dismissed": False,
            "status": "Offline (Critical)",
        }
        ok = maybe_send_critical_offline_alert(
            {
                "_id": ObjectId(),
                "hostname": "sw1",
                "ipAddress": "10.0.0.1",
                "critical": True,
                "monitor": True,
            },
            "Online",
            "Offline (Critical)",
            consecutive_failures=3,
        )
        self.assertFalse(ok)
        mock_whatsapp.assert_not_called()

    @patch("services.alert_service.send_device_recovery_whatsapp_alert", return_value=True)
    @patch("services.alert_service.db")
    def test_recovery_triggers_whatsapp(self, mock_db, mock_whatsapp):
        from bson import ObjectId

        from services.alert_service import resolve_critical_offline_alerts

        device = {
            "_id": ObjectId(),
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
        }
        result = MagicMock()
        result.modified_count = 1
        mock_db.alerts.update_many.return_value = result

        count = resolve_critical_offline_alerts(device)
        self.assertEqual(count, 1)
        mock_whatsapp.assert_called_once_with(device)


if __name__ == "__main__":
    unittest.main()
