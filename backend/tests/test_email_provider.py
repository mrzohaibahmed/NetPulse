"""
Tests for the configurable Gmail / Outlook email provider system.

Covers:
  - normalize_provider() alias resolution
  - _resolve_default_provider() backward-compatibility inference
  - send_email() — all four sender/recipient combinations (mocked SMTP)
  - send_email_with_result() — validation errors and friendly SMTP errors
  - Provider presets (PROVIDER_PRESETS constants)
  - SMTP password never exposed in errors or return values
  - Disabled / missing configuration gates

MongoDB is not available in this environment; config.database and
services.settings_service are mocked at the module level so no real
connection is attempted.
"""

from __future__ import annotations

import os
import smtplib
import ssl
import socket
import sys
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Stub out MongoDB before importing any service that touches it.
# This allows the test suite to run without a MongoDB instance.
# ---------------------------------------------------------------------------

_mock_db_module = MagicMock()
_mock_db_module.db = MagicMock()
sys.modules.setdefault("config.database", _mock_db_module)

# Also stub pymongo so it's importable
sys.modules.setdefault("pymongo", MagicMock())

# Provide minimal env vars required by settings_service / utils
os.environ.setdefault("JWT_SECRET", "netpulse-test-jwt-secret-do-not-use-in-production-32c+")
os.environ.setdefault("FLASK_DEBUG", "true")
os.environ.setdefault("NETPULSE_ENV", "development")
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/netpulse_test")
os.environ.setdefault("DATABASE_NAME", "netpulse_test")
os.environ.setdefault(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5000",
)

# ---------------------------------------------------------------------------
# Now we can safely import our modules
# ---------------------------------------------------------------------------

from services.settings_service import normalize_provider, _resolve_default_provider  # noqa: E402
from services.email_service import (  # noqa: E402
    PROVIDER_PRESETS,
    _classify_smtp_error,
    send_email,
    send_email_with_result,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _smtp_cfg(
    *,
    provider="gmail",
    host="smtp.gmail.com",
    port=587,
    user="sender@gmail.com",
    password="secret",
    from_address="sender@gmail.com",
    to_address="recipient@example.com",
    use_tls=True,
    enabled=True,
    from_name="NetPulse",
):
    return {
        "enabled": enabled,
        "provider": provider,
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "fromAddress": from_address,
        "fromName": from_name,
        "toAddress": to_address,
        "useTls": use_tls,
    }


def _mock_smtp_server():
    """Return a MagicMock that can be used as a context manager for smtplib.SMTP."""
    server = MagicMock()
    server.__enter__ = MagicMock(return_value=server)
    server.__exit__ = MagicMock(return_value=False)
    return server


# ---------------------------------------------------------------------------
# normalize_provider
# ---------------------------------------------------------------------------

class TestNormalizeProvider(unittest.TestCase):

    def test_gmail_variants(self):
        for alias in ("gmail", "Gmail", "GMAIL", "google", "Google"):
            self.assertEqual(normalize_provider(alias), "gmail", alias)

    def test_outlook_variants(self):
        for alias in ("outlook", "Outlook", "microsoft365", "office365", "hotmail"):
            self.assertEqual(normalize_provider(alias), "outlook", alias)

    def test_unknown_falls_back_to_gmail(self):
        self.assertEqual(normalize_provider("yahoo"), "gmail")
        self.assertEqual(normalize_provider(""), "gmail")
        self.assertEqual(normalize_provider("  "), "gmail")

    def test_microsoft_365_with_space(self):
        self.assertEqual(normalize_provider("microsoft 365"), "outlook")

    def test_microsoft_365_without_separators(self):
        self.assertEqual(normalize_provider("microsoft365"), "outlook")


# ---------------------------------------------------------------------------
# Provider presets
# ---------------------------------------------------------------------------

class TestProviderPresets(unittest.TestCase):

    def test_gmail_preset(self):
        p = PROVIDER_PRESETS["gmail"]
        self.assertEqual(p["host"], "smtp.gmail.com")
        self.assertEqual(p["port"], 587)
        self.assertTrue(p["useTls"])

    def test_outlook_preset(self):
        p = PROVIDER_PRESETS["outlook"]
        self.assertEqual(p["host"], "smtp.office365.com")
        self.assertEqual(p["port"], 587)
        self.assertTrue(p["useTls"])

    def test_known_providers_only(self):
        self.assertIn("gmail", PROVIDER_PRESETS)
        self.assertIn("outlook", PROVIDER_PRESETS)


# ---------------------------------------------------------------------------
# _resolve_default_provider (backward-compatibility)
# ---------------------------------------------------------------------------

class TestResolveDefaultProvider(unittest.TestCase):

    def _resolve(self, env: dict):
        with patch.dict("os.environ", env, clear=False):
            return _resolve_default_provider()

    def test_explicit_gmail(self):
        self.assertEqual(self._resolve({"EMAIL_PROVIDER": "gmail"}), "gmail")

    def test_explicit_outlook(self):
        self.assertEqual(self._resolve({"EMAIL_PROVIDER": "outlook"}), "outlook")

    def test_explicit_microsoft365_alias(self):
        self.assertEqual(self._resolve({"EMAIL_PROVIDER": "microsoft365"}), "outlook")

    def test_inferred_gmail_when_absent(self):
        self.assertEqual(
            self._resolve({"EMAIL_PROVIDER": "", "SMTP_HOST": "smtp.gmail.com"}),
            "gmail",
        )

    def test_inferred_outlook_from_office365_host(self):
        self.assertEqual(
            self._resolve({"EMAIL_PROVIDER": "", "SMTP_HOST": "smtp.office365.com"}),
            "outlook",
        )

    def test_default_gmail_when_nothing_set(self):
        self.assertEqual(
            self._resolve({"EMAIL_PROVIDER": "", "SMTP_HOST": ""}),
            "gmail",
        )


# ---------------------------------------------------------------------------
# send_email — all four sender/recipient combinations
# ---------------------------------------------------------------------------

class TestSendEmailProviderMatrix(unittest.TestCase):
    """
    All four provider × recipient combinations must succeed.

    The SMTP provider controls HOW we connect (which server).
    The recipient is ALWAYS independent — any valid email can receive alerts.
    """

    def _run(self, provider, sender, recipient):
        cfg = _smtp_cfg(
            provider=provider,
            host=PROVIDER_PRESETS[provider]["host"],
            port=PROVIDER_PRESETS[provider]["port"],
            user=sender,
            from_address=sender,
            to_address=recipient,
        )
        settings = {"smtp": cfg}
        mock_server = _mock_smtp_server()

        with patch("services.email_service.get_settings", return_value=settings), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x), \
             patch("smtplib.SMTP", return_value=mock_server) as mock_smtp_cls:
            result = send_email("Test", "body text", to_address=recipient)

        # Must succeed
        self.assertTrue(result, f"{provider} → {recipient} returned False")

        # Verify correct SMTP server was used
        mock_smtp_cls.assert_called_once_with(
            PROVIDER_PRESETS[provider]["host"],
            PROVIDER_PRESETS[provider]["port"],
            timeout=30,
        )

        # Verify TLS was used
        mock_server.starttls.assert_called_once()

        # Verify login was called with the sender credentials
        mock_server.login.assert_called_once_with(sender, "secret")

        # Verify the sendmail recipient matches what was requested
        mock_server.sendmail.assert_called_once()
        _, send_args, _ = mock_server.sendmail.mock_calls[0]
        self.assertEqual(send_args[1], [recipient])

    # Test 1: Gmail sender → Gmail recipient
    def test_gmail_sender_to_gmail_recipient(self):
        self._run("gmail", "alerts@gmail.com", "admin@gmail.com")

    # Test 2: Gmail sender → Outlook recipient
    def test_gmail_sender_to_outlook_recipient(self):
        self._run("gmail", "alerts@gmail.com", "networkadmin@outlook.com")

    # Test 3: Outlook sender → Gmail recipient
    def test_outlook_sender_to_gmail_recipient(self):
        self._run("outlook", "monitoring@outlook.com", "admin@gmail.com")

    # Test 4: Outlook sender → Outlook recipient
    def test_outlook_sender_to_outlook_recipient(self):
        self._run("outlook", "monitoring@outlook.com", "networkadmin@outlook.com")


# ---------------------------------------------------------------------------
# send_email — configuration gates
# ---------------------------------------------------------------------------

class TestSendEmailGates(unittest.TestCase):

    def _send(self, **smtp_overrides):
        settings = {"smtp": _smtp_cfg(**smtp_overrides)}
        with patch("services.email_service.get_settings", return_value=settings), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x):
            return send_email("s", "b")

    def test_disabled_returns_false(self):
        self.assertFalse(self._send(enabled=False))

    def test_missing_host_returns_false(self):
        self.assertFalse(self._send(host=""))

    def test_missing_password_returns_false(self):
        self.assertFalse(self._send(password=""))

    def test_missing_from_address_returns_false(self):
        self.assertFalse(self._send(from_address=""))

    def test_missing_recipient_returns_false(self):
        settings = {"smtp": _smtp_cfg(to_address="")}
        with patch("services.email_service.get_settings", return_value=settings), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x):
            result = send_email("s", "b", to_address="")
        self.assertFalse(result)

    def test_recipient_override_replaces_configured_address(self):
        settings = {"smtp": _smtp_cfg(to_address="original@example.com")}
        mock_server = _mock_smtp_server()
        with patch("services.email_service.get_settings", return_value=settings), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x), \
             patch("smtplib.SMTP", return_value=mock_server):
            send_email("s", "b", to_address="override@example.com")
        _, send_args, _ = mock_server.sendmail.mock_calls[0]
        self.assertEqual(send_args[1], ["override@example.com"])


# ---------------------------------------------------------------------------
# send_email — SMTP failure handling
# ---------------------------------------------------------------------------

class TestSendEmailErrorHandling(unittest.TestCase):

    def _send_with_error(self, error, provider="gmail"):
        settings = {"smtp": _smtp_cfg(provider=provider)}
        mock_server = _mock_smtp_server()
        mock_server.login.side_effect = error
        with patch("services.email_service.get_settings", return_value=settings), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x), \
             patch("smtplib.SMTP", return_value=mock_server):
            return send_email("Test", "body")

    def test_auth_error_returns_false(self):
        self.assertFalse(
            self._send_with_error(smtplib.SMTPAuthenticationError(535, b"auth failed"))
        )

    def test_connect_error_returns_false(self):
        self.assertFalse(
            self._send_with_error(smtplib.SMTPConnectError(421, b"down"))
        )

    def test_timeout_returns_false(self):
        self.assertFalse(self._send_with_error(socket.timeout("timed out")))

    def test_invalid_host_returns_false(self):
        self.assertFalse(self._send_with_error(socket.gaierror("name not found")))

    def test_tls_failure_returns_false(self):
        settings = {"smtp": _smtp_cfg()}
        mock_server = _mock_smtp_server()
        mock_server.starttls.side_effect = ssl.SSLError("handshake failed")
        with patch("services.email_service.get_settings", return_value=settings), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x), \
             patch("smtplib.SMTP", return_value=mock_server):
            result = send_email("Test", "body")
        self.assertFalse(result)

    def test_never_raises_on_exception(self):
        """send_email must never propagate exceptions."""
        with patch("services.email_service.get_settings", side_effect=RuntimeError("db down")):
            result = send_email("s", "b")
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# _classify_smtp_error — friendly message content
# ---------------------------------------------------------------------------

class TestClassifySmtpError(unittest.TestCase):

    def test_gmail_auth_error_mentions_app_password(self):
        err = smtplib.SMTPAuthenticationError(535, b"auth failed")
        msg = _classify_smtp_error(err, "gmail")
        self.assertIn("App Password", msg)

    def test_gmail_auth_error_does_not_expose_credentials(self):
        err = smtplib.SMTPAuthenticationError(535, b"auth failed")
        msg = _classify_smtp_error(err, "gmail")
        self.assertNotIn("secret", msg)

    def test_outlook_auth_error_mentions_smtp_auth(self):
        err = smtplib.SMTPAuthenticationError(535, b"auth failed")
        msg = _classify_smtp_error(err, "outlook")
        self.assertIn("SMTP AUTH", msg)

    def test_connect_error_message(self):
        err = smtplib.SMTPConnectError(421, b"down")
        msg = _classify_smtp_error(err, "gmail")
        self.assertIn("connect", msg.lower())

    def test_recipient_refused_message(self):
        err = smtplib.SMTPRecipientsRefused({"bad@x.com": (550, b"no")})
        msg = _classify_smtp_error(err, "gmail")
        self.assertIn("recipient", msg.lower())

    def test_sender_refused_message(self):
        err = smtplib.SMTPSenderRefused(501, b"bad sender", "x")
        msg = _classify_smtp_error(err, "gmail")
        self.assertIn("sender", msg.lower())

    def test_tls_error_message(self):
        err = ssl.SSLError("handshake failed")
        msg = _classify_smtp_error(err, "gmail")
        self.assertIn("TLS", msg)

    def test_timeout_message(self):
        err = socket.timeout("timed out")
        msg = _classify_smtp_error(err, "gmail")
        self.assertIn("timed out", msg.lower())

    def test_dns_error_message(self):
        err = socket.gaierror("name or service not known")
        msg = _classify_smtp_error(err, "gmail")
        self.assertIn("resolve", msg.lower())


# ---------------------------------------------------------------------------
# send_email_with_result — validation gates and error messages
# ---------------------------------------------------------------------------

class TestSendEmailWithResult(unittest.TestCase):

    def _call(self, **smtp_overrides):
        settings = {"smtp": _smtp_cfg(**smtp_overrides)}
        with patch("services.email_service.get_settings", return_value=settings), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x):
            return send_email_with_result("Test", "body")

    def test_success_returns_true_and_empty_message(self):
        settings = {"smtp": _smtp_cfg()}
        mock_server = _mock_smtp_server()
        with patch("services.email_service.get_settings", return_value=settings), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x), \
             patch("smtplib.SMTP", return_value=mock_server):
            ok, msg = send_email_with_result("Test", "body")
        self.assertTrue(ok)
        self.assertEqual(msg, "")

    def test_disabled_returns_false_with_message(self):
        ok, msg = self._call(enabled=False)
        self.assertFalse(ok)
        self.assertIn("disabled", msg.lower())

    def test_missing_host_returns_message(self):
        ok, msg = self._call(host="")
        self.assertFalse(ok)
        self.assertIn("host", msg.lower())

    def test_missing_user_returns_message(self):
        ok, msg = self._call(user="")
        self.assertFalse(ok)
        self.assertIn("username", msg.lower())

    def test_missing_password_returns_message(self):
        ok, msg = self._call(password="")
        self.assertFalse(ok)
        self.assertIn("password", msg.lower())

    def test_missing_from_returns_message(self):
        ok, msg = self._call(from_address="")
        self.assertFalse(ok)
        self.assertIn("sender", msg.lower())

    def test_missing_recipient_returns_message(self):
        settings = {"smtp": _smtp_cfg(to_address="")}
        with patch("services.email_service.get_settings", return_value=settings), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x):
            ok, msg = send_email_with_result("Test", "body", to_address="")
        self.assertFalse(ok)
        self.assertIn("recipient", msg.lower())

    def test_smtp_auth_error_gmail_friendly_message(self):
        settings = {"smtp": _smtp_cfg(provider="gmail")}
        mock_server = _mock_smtp_server()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"auth failed")
        with patch("services.email_service.get_settings", return_value=settings), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x), \
             patch("smtplib.SMTP", return_value=mock_server):
            ok, msg = send_email_with_result("Test", "body")
        self.assertFalse(ok)
        self.assertIn("authentication", msg.lower())

    def test_smtp_auth_error_outlook_mentions_smtp_auth(self):
        settings = {"smtp": _smtp_cfg(provider="outlook", host="smtp.office365.com")}
        mock_server = _mock_smtp_server()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"auth failed")
        with patch("services.email_service.get_settings", return_value=settings), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x), \
             patch("smtplib.SMTP", return_value=mock_server):
            ok, msg = send_email_with_result("Test", "body")
        self.assertFalse(ok)
        self.assertIn("SMTP AUTH", msg)

    def test_error_message_never_contains_smtp_password(self):
        """Friendly error messages must never leak the SMTP password."""
        settings = {"smtp": _smtp_cfg(password="MySecretPassword123")}
        mock_server = _mock_smtp_server()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"auth failed")
        with patch("services.email_service.get_settings", return_value=settings), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x), \
             patch("smtplib.SMTP", return_value=mock_server):
            ok, msg = send_email_with_result("Test", "body")
        self.assertFalse(ok)
        self.assertNotIn("MySecretPassword123", msg)


# ---------------------------------------------------------------------------
# Backward-compatibility: existing Gmail configs without 'provider' key
# ---------------------------------------------------------------------------

class TestBackwardCompatibility(unittest.TestCase):

    def test_legacy_smtp_dict_without_provider_key_uses_gmail(self):
        """Existing MongoDB documents without 'provider' field must still work."""
        settings = {
            "smtp": {
                "enabled": True,
                # No "provider" key — simulates a pre-upgrade document
                "host": "smtp.gmail.com",
                "port": 587,
                "user": "legacy@gmail.com",
                "password": "secret",
                "fromAddress": "legacy@gmail.com",
                "fromName": "NetPulse",
                "toAddress": "admin@example.com",
                "useTls": True,
            }
        }
        mock_server = _mock_smtp_server()
        with patch("services.email_service.get_settings", return_value=settings), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x), \
             patch("smtplib.SMTP", return_value=mock_server) as mock_smtp_cls:
            result = send_email("Legacy test", "body")

        self.assertTrue(result)
        # Must connect to the configured host (Gmail) not a hardcoded override
        mock_smtp_cls.assert_called_once_with("smtp.gmail.com", 587, timeout=30)

    def test_legacy_smtp_dict_without_from_name_still_sends(self):
        """fromName is optional — existing docs without it must not fail."""
        settings = {
            "smtp": {
                "enabled": True,
                "host": "smtp.gmail.com",
                "port": 587,
                "user": "legacy@gmail.com",
                "password": "secret",
                "fromAddress": "legacy@gmail.com",
                # No "fromName"
                "toAddress": "admin@example.com",
                "useTls": True,
            }
        }
        mock_server = _mock_smtp_server()
        with patch("services.email_service.get_settings", return_value=settings), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x), \
             patch("smtplib.SMTP", return_value=mock_server):
            result = send_email("Legacy test", "body")

        self.assertTrue(result)

    def test_outlook_smtp_host_without_provider_field_sends(self):
        """Outlook configs stored without 'provider' key must still connect."""
        settings = {
            "smtp": {
                "enabled": True,
                "host": "smtp.office365.com",
                "port": 587,
                "user": "alerts@outlook.com",
                "password": "secret",
                "fromAddress": "alerts@outlook.com",
                "fromName": "NetPulse",
                "toAddress": "admin@gmail.com",
                "useTls": True,
            }
        }
        mock_server = _mock_smtp_server()
        with patch("services.email_service.get_settings", return_value=settings), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x), \
             patch("smtplib.SMTP", return_value=mock_server) as mock_smtp_cls:
            result = send_email("Legacy test", "body")

        self.assertTrue(result)
        mock_smtp_cls.assert_called_once_with("smtp.office365.com", 587, timeout=30)


# ---------------------------------------------------------------------------
# Security: SMTP password must never be exposed
# ---------------------------------------------------------------------------

class TestPasswordNotExposed(unittest.TestCase):

    FAKE_PASSWORD = "SuperSecretSMTPPassword_xyz!"

    def test_classify_smtp_error_does_not_contain_password(self):
        err = smtplib.SMTPAuthenticationError(535, b"auth failed")
        for provider in ("gmail", "outlook"):
            msg = _classify_smtp_error(err, provider)
            self.assertNotIn(self.FAKE_PASSWORD, msg)

    def test_send_email_with_result_does_not_expose_password(self):
        settings = {"smtp": _smtp_cfg(password=self.FAKE_PASSWORD)}
        mock_server = _mock_smtp_server()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"auth")
        with patch("services.email_service.get_settings", return_value=settings), \
             patch("services.email_service.decrypt_secret", side_effect=lambda x: x), \
             patch("smtplib.SMTP", return_value=mock_server):
            ok, msg = send_email_with_result("Test", "body")
        self.assertNotIn(self.FAKE_PASSWORD, msg)

    def test_get_public_settings_returns_password_set_not_password(self):
        """get_public_settings() must return passwordSet:bool, not the raw password."""
        from services.settings_service import get_public_settings  # noqa: PLC0415

        raw_doc = {
            "_id": "global",
            "smtp": {
                "enabled": True,
                "provider": "gmail",
                "host": "smtp.gmail.com",
                "port": 587,
                "user": "u@gmail.com",
                "password": "encrypted_blob_here",
                "fromAddress": "u@gmail.com",
                "fromName": "NetPulse",
                "toAddress": "admin@example.com",
                "useTls": True,
            },
            "pingInterval": 60,
            "pingTimeoutMs": 1000,
            "pingRetries": 3,
            "pingFailureConfirmationScans": 2,
            "pingConcurrency": 40,
            "dataRetentionDays": 90,
            "incidentRetentionDays": 365,
            "mitigationMode": "manual",
            "autoRecovery": True,
            "cooldownMinutes": 5,
            "stabilizationSeconds": 60,
            "maximumRecoveryAttempts": 3,
            "reMitigationThreshold": 60,
            "requiredConfirmations": 4,
            "stormNotifications": {
                "enabled": True,
                "shutdownEmails": True,
                "recoveryEmails": True,
                "failureEmails": True,
                "toAddress": "",
            },
            "updatedAt": None,
        }

        with patch("services.settings_service.get_settings", return_value=raw_doc):
            public = get_public_settings()

        smtp = public["smtp"]
        # Must have passwordSet flag
        self.assertIn("passwordSet", smtp)
        self.assertTrue(smtp["passwordSet"])
        # Must NOT have the raw password
        self.assertNotIn("password", smtp)
        # Must have provider field
        self.assertIn("provider", smtp)
        self.assertEqual(smtp["provider"], "gmail")
        # Must have fromName
        self.assertIn("fromName", smtp)


if __name__ == "__main__":
    unittest.main()
