"""
Security hardening regression tests (SEC-001 … SEC-016 related).
"""

from __future__ import annotations

import os

# Ensure JWT/CORS allow imports under unittest (pytest also sets these in conftest).
os.environ.setdefault(
    "JWT_SECRET",
    "netpulse-test-jwt-secret-do-not-use-in-production-32c+",
)
os.environ.setdefault("FLASK_DEBUG", "true")
os.environ.setdefault("NETPULSE_ENV", "development")
os.environ.setdefault(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5000",
)

import unittest
from unittest.mock import MagicMock, patch

from utils.jwt_secret import is_weak_jwt_secret, resolve_jwt_secret
from utils.mongo_safe import escape_regex, regex_filter
from utils.ssh_security import assert_safe_interface_name, apply_host_key_policy
from services.user_service import is_forbidden_bootstrap_password


class JwtSecretValidationTests(unittest.TestCase):
    def test_placeholder_is_weak(self):
        self.assertTrue(is_weak_jwt_secret("netpulse-dev-secret-change-me"))
        self.assertTrue(is_weak_jwt_secret("change-this-secret-in-production"))
        self.assertTrue(is_weak_jwt_secret("replace-with-a-strong-random-value"))
        self.assertTrue(is_weak_jwt_secret("short"))

    def test_strong_secret_accepted(self):
        secret = "NpTest_" + "xY9mK2pQ7vL4wR8nT1bC5hJ0z"
        self.assertGreaterEqual(len(secret), 32)
        self.assertFalse(is_weak_jwt_secret(secret))

    def test_production_rejects_missing(self):
        with patch.dict(os.environ, {"JWT_SECRET": "", "FLASK_DEBUG": "false"}, clear=False):
            with self.assertRaises(RuntimeError):
                resolve_jwt_secret(allow_insecure_dev=False)

    def test_production_rejects_placeholder(self):
        with patch.dict(
            os.environ,
            {"JWT_SECRET": "change-this-in-production-please-now", "FLASK_DEBUG": "false"},
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                resolve_jwt_secret(allow_insecure_dev=False)

    def test_debug_allows_insecure(self):
        with patch.dict(os.environ, {"JWT_SECRET": "", "FLASK_DEBUG": "true"}, clear=False):
            secret = resolve_jwt_secret(allow_insecure_dev=True)
            self.assertTrue(bool(secret))


class BootstrapPasswordTests(unittest.TestCase):
    def test_known_defaults_forbidden(self):
        self.assertTrue(is_forbidden_bootstrap_password("admin123"))
        self.assertTrue(is_forbidden_bootstrap_password("superadmin123"))
        self.assertTrue(is_forbidden_bootstrap_password("viewer123"))

    def test_strong_password_allowed(self):
        self.assertFalse(is_forbidden_bootstrap_password("CorrectHorseBattery9!"))

    def test_production_refuses_weak_superadmin_create(self):
        from services import user_service

        fake_db = MagicMock()
        fake_db.users.find_one.side_effect = [None, None]  # no super-admin, no user
        fake_db.users.count_documents.return_value = 1

        with patch.object(user_service, "db", fake_db), patch.dict(
            os.environ,
            {
                "FLASK_DEBUG": "false",
                "NETPULSE_ENV": "production",
                "DEFAULT_SUPER_ADMIN_PASSWORD": "superadmin123",
            },
            clear=False,
        ):
            user_service.ensure_super_admin()
        fake_db.users.insert_one.assert_not_called()


class MongoRegexEscapeTests(unittest.TestCase):
    def test_metacharacters_escaped(self):
        for ch in [".", "*", "+", "?", "(", ")", "[", "]", "{", "}", "|", "\\"]:
            escaped = escape_regex(ch)
            self.assertNotEqual(escaped, ch)
            clause = regex_filter(ch)
            self.assertEqual(clause["$options"], "i")
            self.assertEqual(clause["$regex"], escaped)

    def test_normal_search(self):
        self.assertEqual(escape_regex("Gi1/0/10"), re_escape_literal("Gi1/0/10"))


def re_escape_literal(value: str) -> str:
    import re

    return re.escape(value)


class InterfaceValidationTests(unittest.TestCase):
    def test_valid_cisco_name(self):
        self.assertEqual(assert_safe_interface_name("GigabitEthernet1/0/1"), "GigabitEthernet1/0/1")
        self.assertEqual(assert_safe_interface_name("Gi1/0/10"), "Gi1/0/10")

    def test_rejects_injection(self):
        for bad in [
            "Gi1/0/1; shutdown",
            "Gi1/0/1\nconfigure",
            "Gi1/0/1|id",
            "Gi1/0/1`id`",
            "Gi1/0/1$(id)",
            "Gi1/0/1&id",
            'Gi1/0/1"',
            "Gi1/0/1'",
            "Gi1/0/1\\x",
        ]:
            with self.assertRaises(ValueError):
                assert_safe_interface_name(bad)


class SshHostKeyPolicyTests(unittest.TestCase):
    def test_production_uses_reject_policy(self):
        import paramiko

        client = MagicMock()
        with patch.dict(
            os.environ,
            {"FLASK_DEBUG": "false", "SSH_ALLOW_UNKNOWN_HOSTS": "false"},
            clear=False,
        ):
            name = apply_host_key_policy(client)
        self.assertEqual(name, "RejectPolicy")
        client.set_missing_host_key_policy.assert_called()
        policy = client.set_missing_host_key_policy.call_args[0][0]
        self.assertIsInstance(policy, paramiko.RejectPolicy)

    def test_debug_auto_add_requires_explicit_flag(self):
        import paramiko

        client = MagicMock()
        with patch.dict(
            os.environ,
            {"FLASK_DEBUG": "true", "SSH_ALLOW_UNKNOWN_HOSTS": "true"},
            clear=False,
        ):
            name = apply_host_key_policy(client)
        self.assertEqual(name, "AutoAddPolicy")
        policy = client.set_missing_host_key_policy.call_args[0][0]
        self.assertIsInstance(policy, paramiko.AutoAddPolicy)


class SnmpSerializationTests(unittest.TestCase):
    def test_network_serializer_hides_community(self):
        from routes.discovery_routes import serialize_network

        with patch("routes.discovery_routes.calculate_network_stats", return_value={
            "devices": 0, "switches": 0, "online": 0
        }):
            payload = serialize_network({
                "_id": "507f1f77bcf86cd799439011",
                "name": "lab",
                "type": "ETHERNET",
                "cidr": "10.0.0.0/24",
                "scanTargets": "10.0.0.0/24",
                "snmpCommunity": "secret-community",
                "sshPassword": "x",
            })
        self.assertNotIn("snmpCommunity", payload)
        self.assertTrue(payload.get("snmpCommunityConfigured"))


class SafetyFailClosedTests(unittest.TestCase):
    def test_default_fail_closed(self):
        from services.storm.safety_rules import SafetyConfig, reload_safety_config

        env = {
            k: v
            for k, v in os.environ.items()
            if k != "STORM_SAFETY_FAIL_OPEN_MISSING_HEALTH"
        }
        with patch.dict(os.environ, env, clear=True):
            reload_safety_config()
            cfg = reload_safety_config()
            self.assertFalse(cfg.fail_open_missing_health)
        self.assertFalse(SafetyConfig().fail_open_missing_health)

    def test_missing_health_blocks_when_fail_closed(self):
        from services.storm.safety import SafetyEngine
        from services.storm.safety_history import SafetyContext
        from services.storm.safety_rules import SafetyConfig

        ctx = SafetyContext(
            device_id="507f1f77bcf86cd799439011",
            interface="Gi1/0/10",
            device={"status": "Online", "hostname": "sw1"},
            iface={"name": "Gi1/0/10", "adminStatus": "up", "operStatus": "up"},
            confirmation={"confirmed": True, "state": "CONFIRMED"},
            risk={"riskScore": 92.0},
            ssh_reachable=True,
            ssh_error=None,
            live_admin_status="up",
            cpu_percent=None,
            memory_percent=None,
            mitigation_running=False,
            mitigation_attempts=0,
            cooldown_remaining_seconds=0,
            extras={
                "automation_global": True,
                "device_automation": True,
                "interface_automation": True,
                "maintenance_mode": False,
                "device_locked": False,
                "interface_locked": False,
                "manual_override": False,
            },
        )
        engine = SafetyEngine(
            SafetyConfig(fail_open_missing_health=False, require_ssh=True)
        )
        result = engine.evaluate(
            ctx.device_id,
            ctx.interface,
            context=ctx,
            probe_ssh=False,
            persist=False,
        )
        self.assertFalse(result.safe)
        self.assertEqual(result.failed_rule, "RULE_14")


class DeploymentEnvTests(unittest.TestCase):
    def test_unset_env_is_production_when_not_debug(self):
        from config import deployment

        with patch.dict(
            os.environ,
            {"FLASK_DEBUG": "false", "NETPULSE_ENV": ""},
            clear=False,
        ):
            # Force empty NETPULSE_ENV
            os.environ.pop("NETPULSE_ENV", None)
            self.assertEqual(deployment.get_app_environment(), "production")

    def test_production_cors_fail_closed(self):
        from config import cors_config

        with patch.dict(
            os.environ,
            {
                "FLASK_DEBUG": "false",
                "NETPULSE_ENV": "production",
                "CORS_ALLOWED_ORIGINS": "",
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                cors_config.resolve_cors_origins()


class HealthDisclosureTests(unittest.TestCase):
    def test_liveness_omits_host_and_pid(self):
        from services.ops_health import liveness_payload

        body = liveness_payload()
        self.assertEqual(body["status"], "alive")
        self.assertNotIn("hostname", body)
        self.assertNotIn("pid", body)

    def test_readiness_omits_owner_and_role(self):
        from services.ops_health import readiness_payload

        with patch("services.ops_health._mongo_ping", return_value=(True, None)), patch(
            "services.ops_health.is_scheduler_process", return_value=False
        ):
            body, code = readiness_payload()
        self.assertEqual(code, 200)
        self.assertNotIn("ownerId", body)
        self.assertNotIn("hostname", body)
        self.assertNotIn("role", body)
        self.assertIn("checks", body)


class ApiErrorTests(unittest.TestCase):
    def test_internal_error_hides_exception_text(self):
        from flask import Flask

        from utils.api_errors import internal_error_response

        app = Flask(__name__)
        with app.app_context():
            with app.test_request_context("/"):
                resp, code = internal_error_response(
                    RuntimeError("mongodb://user:secret@host/db")
                )
        self.assertEqual(code, 500)
        data = resp.get_json()
        self.assertEqual(data["error"], "Internal server error")
        self.assertNotIn("secret", str(data))
        self.assertIn("requestId", data)


class ViewerAuthDecoratorTests(unittest.TestCase):
    def test_scan_routes_require_operator(self):
        import inspect

        from routes import isp_routes, scan_routes

        # ensure require_auth(roles=["operator"]) is applied (closure stores roles)
        for fn in (scan_routes.scan_device, isp_routes.manual_scan_isp):
            self.assertTrue(callable(fn))
            # Walk wrappers until we find the require_auth wrapper cell with roles
            found_roles = None
            current = fn
            while current is not None:
                closure = getattr(current, "__closure__", None) or ()
                for cell in closure:
                    try:
                        val = cell.cell_contents
                    except ValueError:
                        continue
                    if val == ["operator"]:
                        found_roles = val
                current = getattr(current, "__wrapped__", None)
            self.assertEqual(
                found_roles,
                ["operator"],
                msg=f"{fn.__name__} must require operator role",
            )


class LoginRateLimitLogicTests(unittest.TestCase):
    def test_failure_then_lock_then_clear(self):
        from services import login_rate_limit as lrl

        store: dict = {}

        class FakeColl:
            def create_index(self, *a, **k):
                return None

            def find_one(self, query):
                return store.get(query.get("key"))

            def update_one(self, query, update, upsert=False):
                key = query["key"]
                doc = store.get(key, {"key": key})
                doc.update(update.get("$set") or {})
                store[key] = doc

            def delete_many(self, query):
                keys = set(query.get("key", {}).get("$in") or [])
                for key in list(store):
                    if key in keys:
                        del store[key]

        fake_db = MagicMock()
        fake_db.__getitem__.return_value = FakeColl()

        with patch.object(lrl, "_db", return_value=fake_db), patch.object(
            lrl, "get_max_failures", return_value=3
        ), patch.object(lrl, "get_lockout_seconds", return_value=60), patch.object(
            lrl, "get_window_seconds", return_value=900
        ):
            lrl._INDEXES_ENSURED = True
            user, ip = "alice", "10.0.0.8"
            for _ in range(2):
                allowed, _retry = lrl.check_login_allowed(user, ip)
                self.assertTrue(allowed)
                lrl.record_login_failure(user, ip)
            allowed, _retry = lrl.check_login_allowed(user, ip)
            self.assertTrue(allowed)
            result = lrl.record_login_failure(user, ip)
            self.assertTrue(result["locked"])
            allowed, retry = lrl.check_login_allowed(user, ip)
            self.assertFalse(allowed)
            self.assertGreater(retry, 0)
            lrl.clear_login_failures(user, ip)
            allowed, _retry = lrl.check_login_allowed(user, ip)
            self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
