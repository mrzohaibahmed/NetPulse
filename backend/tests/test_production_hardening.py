"""
Production hardening tests — deployment, CORS, Mongo config, health, mitigation auth.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from bson import ObjectId


class DeploymentConfigTests(unittest.TestCase):
    def test_api_role_never_starts_scheduler(self):
        from config import deployment

        with patch.dict(
            os.environ,
            {"NETPULSE_ROLE": "api", "NETPULSE_ENABLE_SCHEDULER": "true"},
            clear=False,
        ):
            self.assertFalse(deployment.should_start_scheduler())

    def test_scheduler_role_starts_scheduler(self):
        from config import deployment

        with patch.dict(
            os.environ,
            {"NETPULSE_ROLE": "scheduler", "FLASK_DEBUG": "false"},
            clear=False,
        ):
            self.assertTrue(deployment.should_start_scheduler())

    def test_auto_disables_scheduler_for_multi_worker(self):
        from config import deployment

        with patch.dict(
            os.environ,
            {
                "NETPULSE_ROLE": "all",
                "NETPULSE_ENABLE_SCHEDULER": "auto",
                "GUNICORN_WORKERS": "4",
                "FLASK_DEBUG": "false",
            },
            clear=False,
        ):
            self.assertFalse(deployment.should_start_scheduler())


class CorsConfigTests(unittest.TestCase):
    def test_production_requires_explicit_origins(self):
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

    def test_explicit_origins_parsed(self):
        from config import cors_config

        with patch.dict(
            os.environ,
            {"CORS_ALLOWED_ORIGINS": "https://a.example.com, https://b.example.com/"},
            clear=False,
        ):
            origins = cors_config.resolve_cors_origins()
        self.assertEqual(
            origins,
            ["https://a.example.com", "https://b.example.com"],
        )


class MongoConfigTests(unittest.TestCase):
    def test_build_mongo_client_kwargs_defaults(self):
        from config.mongo_config import build_mongo_client_kwargs

        kwargs = build_mongo_client_kwargs()
        self.assertGreaterEqual(kwargs["maxPoolSize"], 10)
        self.assertIn("waitQueueTimeoutMS", kwargs)
        self.assertTrue(kwargs["retryWrites"])

    def test_safe_summary_redacts_credentials(self):
        from config.mongo_config import safe_mongo_log_summary

        summary = safe_mongo_log_summary(
            "mongodb://user:secret@db.example.com:27017/netpulse",
            {"maxPoolSize": 50},
        )
        self.assertEqual(summary["host"], "db.example.com")
        self.assertNotIn("secret", str(summary.values()))


class MitigationAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.device_id = ObjectId()
        self.interface = "Gi1/0/10"
        self.base_incident = {
            "incidentId": "storm-1",
            "deviceId": self.device_id,
            "interface": self.interface,
            "incidentType": "STORM",
        }

    def test_open_rejected_for_standard_admin_shutdown(self):
        from services.storm.mitigation.authorization import validate_mitigation_authorization

        incident = {**self.base_incident, "status": "OPEN"}
        mock_db = MagicMock()
        with self.assertRaises(ValueError) as ctx:
            validate_mitigation_authorization(
                strategy_name="SHUTDOWN",
                operator="admin",
                incident=incident,
                execution_mode="STANDARD",
                db=mock_db,
            )
        self.assertIn("OPEN", str(ctx.exception))

    def test_prepared_rejected_for_standard_admin_shutdown(self):
        from services.storm.mitigation.authorization import validate_mitigation_authorization

        incident = {**self.base_incident, "status": "PREPARED"}
        mock_db = MagicMock()
        with self.assertRaises(ValueError):
            validate_mitigation_authorization(
                strategy_name="SHUTDOWN",
                operator="admin",
                incident=incident,
                execution_mode="STANDARD",
                db=mock_db,
            )

    def test_ready_requires_confirmed_and_safe_for_standard(self):
        from services.storm.mitigation.authorization import validate_mitigation_authorization

        incident = {**self.base_incident, "status": "READY_FOR_MITIGATION"}
        mock_db = MagicMock()
        mock_db.storm_confirmation_history.find_one.return_value = {
            "confirmed": False,
        }
        with self.assertRaises(ValueError) as ctx:
            validate_mitigation_authorization(
                strategy_name="SHUTDOWN",
                operator="admin",
                incident=incident,
                execution_mode="STANDARD",
                db=mock_db,
            )
        self.assertIn("confirmed", str(ctx.exception).lower())

    def test_ready_with_gates_passes_standard(self):
        from services.storm.mitigation.authorization import validate_mitigation_authorization

        incident = {**self.base_incident, "status": "READY_FOR_MITIGATION"}
        mock_db = MagicMock()
        mock_db.storm_confirmation_history.find_one.return_value = {
            "confirmed": True,
            "timestamp": "2026-01-01T00:00:00Z",
        }
        mock_db.storm_safety_history.find_one.return_value = {
            "safe": True,
            "timestamp": "2026-01-02T00:00:00Z",
        }
        validate_mitigation_authorization(
            strategy_name="SHUTDOWN",
            operator="admin",
            incident=incident,
            execution_mode="STANDARD",
            db=mock_db,
        )

    def test_emergency_allows_open_without_storm_gates(self):
        from services.storm.mitigation.authorization import validate_mitigation_authorization

        incident = {
            **self.base_incident,
            "status": "OPEN",
            "incidentType": "MANUAL",
        }
        mock_db = MagicMock()
        validate_mitigation_authorization(
            strategy_name="SHUTDOWN",
            operator="admin",
            incident=incident,
            execution_mode="EMERGENCY",
            db=mock_db,
        )
        mock_db.storm_confirmation_history.find_one.assert_not_called()


class AutoMitigationBatchTests(unittest.TestCase):
    @patch("services.storm.mitigation.engine.execute_mitigation")
    @patch("services.storm.auto_mitigation.fetch_ready_incidents_batch")
    def test_batch_limit(self, mock_fetch, mock_exec):
        from services.storm.auto_mitigation import run_automatic_mitigation_batch

        mock_fetch.return_value = [
            {"incidentId": "a"},
            {"incidentId": "b"},
        ]
        mock_exec.return_value = {"success": True, "status": "SUCCESS"}
        summary = run_automatic_mitigation_batch(cycle_id="c1")
        self.assertEqual(summary["executed"], 2)
        self.assertEqual(mock_exec.call_count, 2)
        mock_exec.assert_any_call("a", "SHUTDOWN", operator="SYSTEM")
        mock_exec.assert_any_call("b", "SHUTDOWN", operator="SYSTEM")


class HealthEndpointTests(unittest.TestCase):
    def test_liveness_no_db(self):
        from services.ops_health import liveness_payload

        body = liveness_payload()
        self.assertEqual(body["status"], "alive")
        self.assertIn("pid", body)

    @patch("services.ops_health._mongo_ping", return_value=(True, None))
    @patch("services.ops_health.is_scheduler_process", return_value=False)
    def test_readiness_ok_api_only(self, *_mocks):
        from services.ops_health import readiness_payload

        body, code = readiness_payload()
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "ready")

    @patch("services.ops_health._mongo_ping", return_value=(False, "database_unreachable"))
    def test_readiness_mongo_down(self, _mock):
        from services.ops_health import readiness_payload

        body, code = readiness_payload()
        self.assertEqual(code, 503)
        self.assertNotIn("Traceback", str(body))


if __name__ == "__main__":
    unittest.main()
