"""Focused tests for SSH post-command verification retry and read reliability."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bson import ObjectId

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.interface_collection.ssh_collector import (
    SSHCollectorError,
    SSHCredentials,
    SSHInterfaceCollector,
)
from services.storm.mitigation.engine import execute_mitigation
from services.storm.mitigation.ssh_executor import SSHMitigationExecutor
from services.storm.mitigation.strategy import ShutdownInterfaceStrategy
from services.storm.mitigation.verifier import verify_mitigation
from services.storm.recovery.engine import execute_recovery
from services.storm.recovery.verifier import verify_interface_up
from services.storm.ssh_verification_retry import (
    MAX_VERIFICATION_ATTEMPTS,
    POST_CONFIG_VERIFICATION_SETTLE_SECONDS,
    VERIFICATION_RETRY_DELAY_SECONDS,
    verify_with_bounded_retry,
)


def _creds() -> SSHCredentials:
    return SSHCredentials(
        host="10.0.0.1",
        username="admin",
        password="login-pass",
        secret="enable-pass",
        vendor="cisco_ios",
        timeout=30,
    )


def _collector() -> SSHInterfaceCollector:
    with patch("services.interface_collection.ssh_collector._ensure_paramiko"):
        return SSHInterfaceCollector(_creds(), require_privileged=True)


class VerifyWithBoundedRetryTests(unittest.TestCase):
    @patch("services.storm.ssh_verification_retry.time.sleep")
    def test_succeeds_on_second_attempt(self, mock_sleep):
        attempts = iter([(False, ""), (True, "good output")])

        def attempt_fn():
            return next(attempts)

        ok, output = verify_with_bounded_retry(label="test", attempt_fn=attempt_fn)
        self.assertTrue(ok)
        self.assertEqual(output, "good output")
        mock_sleep.assert_any_call(POST_CONFIG_VERIFICATION_SETTLE_SECONDS)
        mock_sleep.assert_any_call(VERIFICATION_RETRY_DELAY_SECONDS)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("services.storm.ssh_verification_retry.time.sleep")
    def test_fails_after_all_attempts(self, mock_sleep):
        def attempt_fn():
            return False, "still wrong"

        ok, output = verify_with_bounded_retry(label="test", attempt_fn=attempt_fn)
        self.assertFalse(ok)
        self.assertEqual(output, "still wrong")
        # initial settle + retry delays between attempts
        self.assertEqual(mock_sleep.call_count, MAX_VERIFICATION_ATTEMPTS)

    @patch("services.storm.ssh_verification_retry.time.sleep")
    def test_empty_output_is_not_success(self, mock_sleep):
        def attempt_fn():
            return False, ""

        ok, _ = verify_with_bounded_retry(label="test", attempt_fn=attempt_fn)
        self.assertFalse(ok)
        self.assertEqual(mock_sleep.call_count, MAX_VERIFICATION_ATTEMPTS)


class ReadUntilPromptTests(unittest.TestCase):
    @patch("services.interface_collection.ssh_collector.time.sleep")
    def test_idle_without_prompt_waits_for_later_prompt(self, _mock_sleep):
        """Chunk + idle must not terminate before the prompt arrives."""
        collector = _collector()
        shell = MagicMock()
        collector._shell = shell

        recv_ready_values = [
            True,
            False,
            False,
            False,
            False,
            False,
            True,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
        ]
        recv_payloads = [
            b"interface GigabitEthernet1/0/10\n shutdown\n",
            b"Switch# ",
        ]
        recv_ready_iter = iter(recv_ready_values)
        recv_iter = iter(recv_payloads)

        shell.recv_ready.side_effect = lambda: next(recv_ready_iter, False)
        shell.recv.side_effect = lambda _size: next(recv_iter, b"")

        output = collector._read_until_prompt(timeout=5.0)

        self.assertIn("shutdown", output)
        self.assertIn("Switch#", output)

    @patch("services.interface_collection.ssh_collector.time.sleep")
    def test_end_waits_for_exec_prompt_not_config_submode(self, _mock_sleep):
        """end must not complete while the session is still at (config-if)#."""
        collector = _collector()
        shell = MagicMock()
        collector._shell = shell

        recv_ready_values = [True, False, False, True, False, False, False, False]
        recv_payloads = [
            b"Switch(config-if)# ",
            b"Switch# ",
        ]
        recv_ready_iter = iter(recv_ready_values)
        recv_iter = iter(recv_payloads)

        shell.recv_ready.side_effect = lambda: next(recv_ready_iter, False)
        shell.recv.side_effect = lambda _size: next(recv_iter, b"")

        output = collector._read_until_prompt(timeout=5.0, require_exec_prompt=True)

        self.assertIn("Switch#", output)
        self.assertNotIn("(config-if)#", output.split("Switch#")[-1])

    @patch("services.interface_collection.ssh_collector.time.sleep")
    def test_config_submode_prompt_accepted_without_exec_requirement(self, _mock_sleep):
        collector = _collector()
        shell = MagicMock()
        collector._shell = shell
        shell.recv_ready.side_effect = [True, False, False, False, False, False]
        shell.recv.side_effect = [b"Switch(config-if)# "]

        output = collector._read_until_prompt(timeout=5.0, require_exec_prompt=False)

        self.assertIn("(config-if)#", output)

    @patch("services.interface_collection.ssh_collector.time.sleep")
    def test_hard_timeout_still_applies(self, mock_sleep):
        collector = _collector()
        shell = MagicMock()
        collector._shell = shell
        shell.recv_ready.return_value = False

        with patch(
            "services.interface_collection.ssh_collector.time.monotonic",
            side_effect=[0.0, 0.2, 0.4, 31.0],
        ):
            output = collector._read_until_prompt(timeout=30.0)

        self.assertEqual(output, "")
        self.assertTrue(mock_sleep.called)


class EnsureExecPromptTests(unittest.TestCase):
    @patch("services.interface_collection.ssh_collector.time.sleep")
    def test_sends_end_when_still_in_config_submode(self, _mock_sleep):
        collector = _collector()
        shell = MagicMock()
        collector._shell = shell

        with patch.object(
            collector,
            "_read_session_prompt",
            side_effect=["Switch(config-if)#", "Switch#"],
        ), patch.object(
            collector,
            "_run_command",
            return_value="Switch#",
        ) as mock_run, patch.object(
            collector,
            "_read_until_prompt",
            return_value="Switch# ",
        ), patch.object(
            collector,
            "_drain",
            return_value="",
        ):
            collector.ensure_exec_prompt(settle_seconds=0.0)

        mock_run.assert_called_once_with("end", wait=0.5, require_exec_prompt=True)
        self.assertTrue(collector._privileged_confirmed)

    @patch("services.interface_collection.ssh_collector.time.sleep")
    def test_raises_when_exec_prompt_missing(self, _mock_sleep):
        collector = _collector()
        collector._shell = MagicMock()

        with patch.object(collector, "_read_session_prompt", return_value=None), patch.object(
            collector,
            "_read_until_prompt",
            return_value="partial output without prompt",
        ), patch.object(collector, "_drain", return_value=""):
            with self.assertRaises(SSHCollectorError):
                collector.ensure_exec_prompt(settle_seconds=0.0)

    @patch("services.interface_collection.ssh_collector.time.sleep")
    def test_skips_reprompt_when_already_at_exec(self, mock_sleep):
        collector = _collector()
        collector._shell = MagicMock()

        with patch.object(
            collector,
            "_read_session_prompt",
            return_value="Switch#",
        ), patch.object(collector, "_run_command") as mock_run, patch.object(
            collector,
            "_drain",
            return_value="",
        ):
            collector.ensure_exec_prompt(settle_seconds=0.25)

        mock_run.assert_not_called()
        self.assertTrue(collector._privileged_confirmed)
        mock_sleep.assert_called_once_with(0.25)


class SSHExecutorConfigSyncTests(unittest.TestCase):
    def _executor(self) -> SSHMitigationExecutor:
        executor = SSHMitigationExecutor.__new__(SSHMitigationExecutor)
        executor.device = {"hostname": "sw1", "ipAddress": "10.0.0.1"}
        executor.creds = MagicMock()
        executor.creds.host = "10.0.0.1"
        executor.collector = MagicMock(spec=SSHInterfaceCollector)
        return executor

    def test_config_batch_syncs_exec_before_returning(self):
        executor = self._executor()
        executor.collector.run_command.return_value = "OK"

        cmds = ["configure terminal", "interface Gi1/0/10", "shutdown", "end"]
        executor.execute_commands(cmds, "Gi1/0/10")

        executor.collector.mark_entering_config_mode.assert_called_once()
        executor.collector.ensure_exec_prompt.assert_called_once()

    def test_show_only_batch_skips_exec_sync(self):
        executor = self._executor()
        executor.collector.run_command.return_value = "interface Gi1/0/10\n shutdown\n"

        executor.execute_commands(
            ["show running-config interface Gi1/0/10"],
            "Gi1/0/10",
        )

        executor.collector.ensure_exec_prompt.assert_not_called()


class MitigationVerifierRetryTests(unittest.TestCase):
    def setUp(self):
        self.executor = MagicMock()
        self.executor.creds.vendor = "cisco_ios"
        self.executor.creds.host = "10.0.0.1"
        self.strategy = ShutdownInterfaceStrategy()
        self.interface = "Gi1/0/10"

    @patch("services.storm.mitigation.verifier.verify_with_bounded_retry")
    def test_delegates_to_bounded_retry(self, mock_retry):
        mock_retry.return_value = (True, "interface Gi1/0/10\n shutdown\n")
        ok, output = verify_mitigation(self.executor, self.strategy, self.interface)
        self.assertTrue(ok)
        mock_retry.assert_called_once()
        self.assertEqual(mock_retry.call_args.kwargs["label"], "mitigation:SHUTDOWN")

    @patch("services.storm.ssh_verification_retry.time.sleep")
    def test_retry_succeeds_on_second_show(self, mock_sleep):
        show_outputs = iter(
            [
                "interface GigabitEthernet1/0/10\n no shutdown\n",
                "interface GigabitEthernet1/0/10\n shutdown\n",
            ]
        )
        self.executor.execute_commands.side_effect = lambda cmds, iface: [next(show_outputs)]

        ok, output = verify_mitigation(self.executor, self.strategy, self.interface)

        self.assertTrue(ok)
        self.assertIn("shutdown", output)
        self.assertEqual(self.executor.execute_commands.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("services.storm.ssh_verification_retry.time.sleep")
    def test_all_show_attempts_fail(self, mock_sleep):
        self.executor.execute_commands.return_value = [
            "interface GigabitEthernet1/0/10\n no shutdown\n"
        ]

        ok, _ = verify_mitigation(self.executor, self.strategy, self.interface)

        self.assertFalse(ok)
        self.assertEqual(self.executor.execute_commands.call_count, MAX_VERIFICATION_ATTEMPTS)


class RecoveryVerifierRetryTests(unittest.TestCase):
    def setUp(self):
        self.executor = MagicMock()
        self.executor.creds.vendor = "cisco_ios"
        self.executor.creds.host = "10.0.0.1"
        self.executor.collector = MagicMock()
        self.interface = "Gi1/0/10"

    @patch("services.storm.ssh_verification_retry.time.sleep")
    def test_retry_succeeds_when_admin_up_on_second_attempt(self, mock_sleep):
        show_outputs = iter(
            [
                "interface GigabitEthernet1/0/10\n shutdown\n",
                "interface GigabitEthernet1/0/10\n description recovered\n",
            ]
        )
        self.executor.execute_commands.side_effect = lambda cmds, iface: [next(show_outputs)]

        ok, output = verify_interface_up(self.executor, self.interface)

        self.assertTrue(ok)
        self.assertNotIn("\n shutdown", output.lower())
        self.assertEqual(self.executor.execute_commands.call_count, 2)
        self.assertEqual(self.executor.collector.ensure_exec_prompt.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("services.storm.ssh_verification_retry.time.sleep")
    def test_consistent_down_state_fails(self, mock_sleep):
        self.executor.execute_commands.return_value = [
            "interface GigabitEthernet1/0/10\n shutdown\n",
        ]

        ok, _ = verify_interface_up(self.executor, self.interface)

        self.assertFalse(ok)
        self.assertEqual(self.executor.execute_commands.call_count, MAX_VERIFICATION_ATTEMPTS)


class MitigationEngineIntegrationTests(unittest.TestCase):
    incident_id = "storm-2026-000001"
    device_id = ObjectId("507f1f77bcf86cd799439011")
    interface = "Gi1/0/10"

    incident_doc = {
        "incidentId": incident_id,
        "deviceId": device_id,
        "interface": interface,
        "status": "READY_FOR_MITIGATION",
        "incidentType": "STORM",
    }
    device_doc = {
        "_id": device_id,
        "hostname": "sw1",
        "ipAddress": "10.0.0.1",
        "credentials": {
            "sshUsername": "admin",
            "sshPassword": "password",
            "sshVendor": "cisco_ios",
        },
    }

    def _fake_db(self):
        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        fake_db.storm_confirmation_history.find_one.return_value = {"confirmed": True}
        fake_db.storm_safety_history.find_one.return_value = {"safe": True}
        return fake_db

    @patch("services.storm.mitigation.engine.get_incident")
    @patch("services.storm.mitigation.engine._db")
    @patch("services.storm.mitigation.ssh_executor.SSHInterfaceCollector")
    @patch("services.storm.mitigation.engine.LockService.acquire_mitigation_locks")
    @patch("services.storm.mitigation.engine.LockService.release_mitigation_locks")
    @patch("services.storm.ssh_verification_retry.time.sleep")
    def test_shutdown_succeeds_with_verification_retry_and_no_rollback(
        self,
        mock_sleep,
        mock_release,
        mock_acquire,
        mock_ssh,
        mock_db_fn,
        mock_get_incident,
    ):
        mock_get_incident.return_value = self.incident_doc
        mock_acquire.return_value = ("device:lock", "iface:lock")
        mock_db_fn.return_value = self._fake_db()

        mock_collector = MagicMock(spec=SSHInterfaceCollector)
        mock_ssh.return_value = mock_collector
        mock_collector.assert_privileged_mode = MagicMock()
        show_attempts = iter(
            [
                "interface GigabitEthernet1/0/10\n",
                "interface GigabitEthernet1/0/10\n shutdown\n",
            ]
        )

        def run_command(cmd, wait=0.4):
            if cmd.strip().lower().startswith("show "):
                return next(show_attempts)
            return "OK"

        mock_collector.run_command.side_effect = run_command

        res = execute_mitigation(self.incident_id, "SHUTDOWN", operator="admin")

        self.assertTrue(res["success"])
        shutdown_count = sum(
            1
            for call in mock_collector.run_command.call_args_list
            if call.args[0].strip().lower() == "shutdown"
        )
        show_count = sum(
            1
            for call in mock_collector.run_command.call_args_list
            if "show running-config interface" in call.args[0].lower()
        )
        self.assertEqual(shutdown_count, 1)
        self.assertEqual(show_count, 2)
        rollback_cmds = [
            call.args[0]
            for call in mock_collector.run_command.call_args_list
            if call.args[0].strip().lower() == "no shutdown"
        ]
        self.assertEqual(rollback_cmds, [])

    @patch("services.storm.mitigation.engine.get_incident")
    @patch("services.storm.mitigation.engine._db")
    @patch("services.storm.mitigation.ssh_executor.SSHInterfaceCollector")
    @patch("services.storm.mitigation.engine.LockService.acquire_mitigation_locks")
    @patch("services.storm.mitigation.engine.LockService.release_mitigation_locks")
    @patch("services.storm.ssh_verification_retry.time.sleep")
    def test_config_command_failure_does_not_verify_or_retry_config(
        self,
        mock_sleep,
        mock_release,
        mock_acquire,
        mock_ssh,
        mock_db_fn,
        mock_get_incident,
    ):
        mock_get_incident.return_value = self.incident_doc
        mock_acquire.return_value = ("device:lock", "iface:lock")
        mock_db_fn.return_value = self._fake_db()

        mock_collector = MagicMock(spec=SSHInterfaceCollector)
        mock_ssh.return_value = mock_collector
        mock_collector.assert_privileged_mode = MagicMock()

        def run_command(cmd, wait=0.4):
            if cmd.strip().lower() == "shutdown":
                return "% Command rejected: Protected port.\nSwitch(config-if)#"
            return "OK"

        mock_collector.run_command.side_effect = run_command

        res = execute_mitigation(self.incident_id, "SHUTDOWN", operator="admin")

        self.assertFalse(res["success"])
        shutdown_count = sum(
            1
            for call in mock_collector.run_command.call_args_list
            if call.args[0].strip().lower() == "shutdown"
        )
        show_count = sum(
            1
            for call in mock_collector.run_command.call_args_list
            if "show running-config interface" in call.args[0].lower()
        )
        self.assertEqual(shutdown_count, 1)
        self.assertEqual(show_count, 0)

    @patch("services.storm.mitigation.engine.get_incident")
    @patch("services.storm.mitigation.engine._db")
    @patch("services.storm.mitigation.ssh_executor.SSHInterfaceCollector")
    @patch("services.storm.mitigation.engine.LockService.acquire_mitigation_locks")
    @patch("services.storm.mitigation.engine.LockService.release_mitigation_locks")
    @patch("services.storm.ssh_verification_retry.time.sleep")
    def test_command_completion_before_verification(
        self,
        mock_sleep,
        mock_release,
        mock_acquire,
        mock_ssh,
        mock_db_fn,
        mock_get_incident,
    ):
        mock_get_incident.return_value = self.incident_doc
        mock_acquire.return_value = ("device:lock", "iface:lock")
        mock_db_fn.return_value = self._fake_db()

        mock_collector = MagicMock(spec=SSHInterfaceCollector)
        mock_ssh.return_value = mock_collector
        mock_collector.assert_privileged_mode = MagicMock()
        events: list[str] = []

        def run_command(cmd, wait=0.4):
            normalized = cmd.strip().lower()
            if normalized == "shutdown":
                events.append("complete:shutdown")
                return "Switch(config-if)#"
            if normalized == "end":
                events.append("complete:end")
                return "Switch#"
            if normalized.startswith("show running-config interface"):
                events.append("complete:show")
                return "interface GigabitEthernet1/0/10\n shutdown\n"
            events.append(f"complete:{normalized}")
            return "OK"

        def track_send(cmd, wait=0.4):
            normalized = cmd.strip().lower()
            if normalized == "shutdown":
                events.append("send:shutdown")
            elif normalized.startswith("show running-config interface"):
                events.append("send:show")
            return run_command(cmd, wait)

        mock_collector.run_command.side_effect = track_send

        res = execute_mitigation(self.incident_id, "SHUTDOWN", operator="admin")

        self.assertTrue(res["success"])
        self.assertLess(
            events.index("complete:shutdown"),
            events.index("send:show"),
        )
        self.assertLess(
            events.index("complete:end"),
            events.index("send:show"),
        )


class RecoveryEngineIntegrationTests(unittest.TestCase):
    incident_id = "storm-2026-000002"
    device_id = ObjectId("507f1f77bcf86cd799439012")
    interface = "Gi1/0/10"

    incident_doc = {
        "incidentId": incident_id,
        "deviceId": device_id,
        "interface": interface,
        "status": "MITIGATED",
        "recoveryRetryCount": 0,
    }
    device_doc = {
        "_id": device_id,
        "hostname": "sw1",
        "ipAddress": "10.0.0.1",
        "credentials": {
            "sshUsername": "admin",
            "sshPassword": "password",
            "sshVendor": "cisco_ios",
        },
    }

    @patch("services.storm.recovery.engine.get_settings")
    @patch("services.storm.recovery.engine.get_incident")
    @patch("services.storm.recovery.engine._db")
    @patch("services.storm.recovery.engine.validate_recovery_policy")
    @patch("services.storm.recovery.engine.SSHMitigationExecutor")
    @patch("services.storm.recovery.engine.LockService.acquire_recovery_locks")
    @patch("services.storm.recovery.engine.LockService.release_recovery_locks")
    @patch("services.storm.recovery.post_recovery.invalidate_pipeline_after_recovery")
    @patch("services.storm.recovery.engine.collect_post_recovery_stats")
    @patch("services.storm.ssh_verification_retry.time.sleep")
    def test_recovery_succeeds_with_verification_retry(
        self,
        mock_sleep,
        mock_stats,
        mock_invalidate,
        mock_release,
        mock_acquire,
        mock_ssh,
        mock_policy,
        mock_db_fn,
        mock_get_incident,
        mock_settings,
    ):
        mock_get_incident.return_value = self.incident_doc
        mock_policy.return_value = {"passed": True}
        mock_acquire.return_value = ("recovery:device", "recovery:interface")
        mock_settings.return_value = {
            "maximumRecoveryAttempts": 3,
            "stabilizationSeconds": 60,
        }
        mock_stats.return_value = {"adminStatus": "up"}
        mock_invalidate.return_value = None

        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        mock_db_fn.return_value = fake_db

        executor = MagicMock()
        mock_ssh.return_value.__enter__.return_value = executor
        executor.creds.vendor = "cisco_ios"
        executor.collector = MagicMock()

        show_outputs = iter(
            [
                "interface GigabitEthernet1/0/10\n shutdown\n",
                "interface GigabitEthernet1/0/10\n description recovered\n",
            ]
        )
        config_done = {"value": False}

        def run_command(cmd, wait=0.4):
            if cmd.strip().lower() == "no shutdown":
                config_done["value"] = True
                return "Switch(config-if)#"
            if cmd.strip().lower().startswith("show running-config interface"):
                self.assertTrue(config_done["value"])
                return next(show_outputs)
            return "OK"

        executor.collector.run_command.side_effect = run_command
        executor.execute_commands.side_effect = lambda cmds, iface: [
            executor.collector.run_command(cmd) for cmd in cmds
        ]

        res = execute_recovery(
            self.incident_id,
            force=False,
            operator="SYSTEM",
            skip_policy_validation=True,
        )

        self.assertTrue(res["success"])
        no_shutdown_count = sum(
            1
            for call in executor.collector.run_command.call_args_list
            if call.args[0].strip().lower() == "no shutdown"
        )
        show_count = sum(
            1
            for call in executor.collector.run_command.call_args_list
            if call.args[0].strip().lower().startswith("show running-config interface")
        )
        self.assertEqual(no_shutdown_count, 1)
        self.assertEqual(show_count, 2)


if __name__ == "__main__":
    unittest.main()
