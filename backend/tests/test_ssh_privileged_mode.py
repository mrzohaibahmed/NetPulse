"""Tests for SSH privileged-mode detection and enforcement."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from models.device import normalize_device_credentials
from services.interface_collection.ssh_collector import (
    SSHCollectorError,
    SSHCredentials,
    SSHInterfaceCollector,
    _enable_output_indicates_failure,
    _extract_prompt_line,
    _is_privileged_prompt_line,
    _is_user_prompt_line,
)
from services.storm.mitigation.ssh_executor import SSHMitigationExecutor


def _creds(**overrides) -> SSHCredentials:
    base = {
        "host": "10.0.0.1",
        "username": "admin",
        "password": "login-pass",
        "secret": "",
        "vendor": "cisco_ios",
    }
    base.update(overrides)
    return SSHCredentials(**base)


def _collector(*, require_privileged: bool = False, secret: str = "") -> SSHInterfaceCollector:
    with patch("services.interface_collection.ssh_collector._ensure_paramiko"):
        return SSHInterfaceCollector(
            _creds(secret=secret),
            require_privileged=require_privileged,
        )


class TestPromptDetection:
    def test_privileged_prompt_recognized(self):
        assert _is_privileged_prompt_line(_extract_prompt_line("Switch#"))
        assert _is_privileged_prompt_line(_extract_prompt_line("\r\nSwitch#"))
        assert _is_privileged_prompt_line("Switch#")

    def test_user_prompt_recognized(self):
        assert _is_user_prompt_line(_extract_prompt_line("Switch>"))
        assert _is_user_prompt_line(_extract_prompt_line("\r\nSwitch>"))
        assert _is_user_prompt_line("Switch>")

    def test_hash_in_output_not_treated_as_prompt(self):
        assert _extract_prompt_line("Description: interface #1") is None

    def test_password_prompt_not_privileged(self):
        assert not _is_privileged_prompt_line("Password:")
        assert _extract_prompt_line("Password:") is None

    def test_enable_failure_markers(self):
        assert _enable_output_indicates_failure("% Bad passwords")
        assert _enable_output_indicates_failure("% Access denied")
        assert not _enable_output_indicates_failure("Switch#")


class TestPrivilegedSessionPreparation:
    def test_already_privileged_skips_enable(self):
        collector = _collector(require_privileged=True, secret="enable-pass")
        collector._shell = MagicMock()
        collector._read_session_prompt = MagicMock(return_value="Switch#")

        collector._ensure_privileged_session()

        assert collector._privileged_confirmed is True
        collector._shell.send.assert_not_called()

    def test_user_mode_with_valid_secret_enters_privileged(self):
        collector = _collector(require_privileged=True, secret="enable-pass")
        collector._shell = MagicMock()
        collector._read_session_prompt = MagicMock(
            side_effect=["Switch>", "Switch#"],
        )
        collector._enter_enable = MagicMock(return_value="Password:\nSwitch#")

        collector._ensure_privileged_session()

        collector._enter_enable.assert_called_once_with("enable-pass")
        assert collector._privileged_confirmed is True

    def test_user_mode_without_secret_fails_when_required(self):
        collector = _collector(require_privileged=True, secret="")
        collector._shell = MagicMock()
        collector._read_session_prompt = MagicMock(return_value="Switch>")

        with pytest.raises(SSHCollectorError, match="enable password not configured"):
            collector._ensure_privileged_session()

        collector._shell.send.assert_not_called()

    def test_user_mode_without_secret_allows_discovery(self):
        collector = _collector(require_privileged=False, secret="")
        collector._shell = MagicMock()
        collector._read_session_prompt = MagicMock(return_value="Switch>")

        collector._ensure_privileged_session()

        assert collector._privileged_confirmed is False

    def test_invalid_enable_secret_fails_when_required(self):
        collector = _collector(require_privileged=True, secret="wrong")
        collector._shell = MagicMock()
        collector._read_session_prompt = MagicMock(return_value="Switch>")
        collector._enter_enable = MagicMock(return_value="% Bad passwords")

        with pytest.raises(SSHCollectorError, match="enable password rejected"):
            collector._ensure_privileged_session()

    def test_invalid_enable_secret_does_not_run_configure(self):
        device = {
            "ipAddress": "10.0.0.1",
            "credentials": {
                "sshUsername": "admin",
                "sshPassword": "login-pass",
                "sshVendor": "cisco_ios",
            },
        }
        mock_collector = MagicMock(spec=SSHInterfaceCollector)
        mock_collector.run_command = MagicMock()

        with patch(
            "services.storm.mitigation.ssh_executor.SSHInterfaceCollector",
            return_value=mock_collector,
        ):
            mock_collector.connect.side_effect = SSHCollectorError(
                "Unable to enter privileged mode on 10.0.0.1: enable password not configured."
            )
            executor = SSHMitigationExecutor(device)
            with pytest.raises(RuntimeError, match="SSH reachability check failed"):
                executor.connect()

        mock_collector.run_command.assert_not_called()

    def test_mitigation_executor_asserts_before_config_commands(self):
        device = {
            "ipAddress": "10.0.0.1",
            "credentials": {
                "sshUsername": "admin",
                "sshPassword": "login-pass",
                "sshVendor": "cisco_ios",
            },
        }
        mock_collector = MagicMock(spec=SSHInterfaceCollector)
        mock_collector.assert_privileged_mode.side_effect = SSHCollectorError(
            "Unable to enter privileged mode on 10.0.0.1: enable password missing or rejected."
        )

        with patch(
            "services.storm.mitigation.ssh_executor.SSHInterfaceCollector",
            return_value=mock_collector,
        ):
            executor = SSHMitigationExecutor(device)
            executor.collector = mock_collector
            with pytest.raises(SSHCollectorError, match="enable password missing or rejected"):
                executor.execute_commands(
                    ["configure terminal", "interface Gi1/0/1", "shutdown", "end"],
                    "Gi1/0/1",
                )

        mock_collector.run_command.assert_not_called()


class TestCredentialPersistence:
    @patch("models.device.encrypt_secret", side_effect=lambda value: f"enc:{value}")
    def test_normalize_device_credentials_accepts_ssh_secret(self, _mock_encrypt):
        result = normalize_device_credentials(
            {
                "sshUsername": "admin",
                "sshPassword": "login-pass",
                "sshSecret": "enable-pass",
            }
        )

        assert result is not None
        assert result["sshUsername"] == "admin"
        assert result["sshPassword"] == "enc:login-pass"
        assert result["sshSecret"] == "enc:enable-pass"

    @patch("models.device.encrypt_secret", side_effect=lambda value: f"enc:{value}")
    def test_partial_update_preserves_existing_secret(self, _mock_encrypt):
        existing = {
            "sshUsername": "admin",
            "sshPassword": "enc:old-login",
            "sshSecret": "enc:old-enable",
        }
        result = normalize_device_credentials(
            {"sshUsername": "operator"},
            existing=existing,
        )

        assert result["sshUsername"] == "operator"
        assert result["sshPassword"] == "enc:old-login"
        assert result["sshSecret"] == "enc:old-enable"
