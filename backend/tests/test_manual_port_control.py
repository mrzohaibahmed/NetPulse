from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bson import ObjectId
from flask import Flask

from routes.manual_routes import manual_bp
from services.storm.emergency_shutdown import (
    EMERGENCY_CONFIRMATION,
    EmergencyShutdownError,
    execute_emergency_shutdown,
    validate_emergency_request,
)
from utils.auth import create_access_token
from services.storm.models import SafetyResult
from utils.rate_limit import EMERGENCY_SHUTDOWN_LIMITER


class ManualPortControlTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(manual_bp, url_prefix="/api")
        self.client = self.app.test_client()

        self.admin_token = create_access_token(
            user_id="507f1f77bcf86cd799439012",
            username="admin",
            role="admin",
        )
        self.operator_token = create_access_token(
            user_id="507f1f77bcf86cd799439015",
            username="operator",
            role="operator",
        )
        self.super_token = create_access_token(
            user_id="507f1f77bcf86cd799439013",
            username="super",
            role="super-admin",
        )
        self.viewer_token = create_access_token(
            user_id="507f1f77bcf86cd799439014",
            username="viewer",
            role="viewer",
        )

        self.device_oid = ObjectId("507f1f77bcf86cd799439011")
        self.device_id = str(self.device_oid)
        self.interface = "Gi1/0/10"
        self.reason = "Maintenance window for security isolation"
        self.emergency_reason = "Security incident isolation required immediately"
        self.incident_id = "storm-2026-000999"

        EMERGENCY_SHUTDOWN_LIMITER.reset()

    def _auth(self, token: str):
        return {"Authorization": f"Bearer {token}"}

    def _emergency_payload(self, **overrides):
        payload = {
            "deviceId": self.device_id,
            "interface": self.interface,
            "reason": self.emergency_reason,
            "confirmation": EMERGENCY_CONFIRMATION,
        }
        payload.update(overrides)
        return payload

    @patch("routes.manual_routes.log_audit")
    @patch("routes.manual_routes.db")
    @patch("routes.manual_routes.execute_mitigation")
    @patch("routes.manual_routes.prepare_mitigation")
    @patch("routes.manual_routes.find_open_incident")
    @patch("routes.manual_routes.evaluate_safety")
    def test_successful_manual_shutdown(
        self,
        mock_eval_safety,
        mock_find_open,
        mock_prepare,
        mock_execute_mitigation,
        mock_db,
        mock_log_audit,
    ):
        mock_find_open.return_value = None
        mock_eval_safety.return_value = SafetyResult(
            safe=True,
            reason="All safety checks passed",
            failed_rule=None,
            checks={},
            timestamp=None,
            device_id=str(self.device_oid),
            interface=self.interface,
            status="SAFE",
        )

        mock_prepare.return_value = {"ready": True, "incidentId": self.incident_id, "reason": "OK"}
        mock_execute_mitigation.return_value = {"success": True, "status": "SUCCESS", "incidentId": self.incident_id}

        mock_db.storm_mitigation_history.find_one.return_value = {
            "verificationResult": {"success": True},
            "rollbackPerformed": False,
        }

        res = self.client.post(
            "/api/manual/shutdown",
            headers=self._auth(self.admin_token),
            json={"deviceId": self.device_id, "interface": self.interface, "reason": self.reason},
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["incidentId"], self.incident_id)

        mock_prepare.assert_called_once()
        incident_metadata = mock_prepare.call_args.kwargs.get("incident_metadata")
        self.assertIsNotNone(incident_metadata)
        self.assertEqual(incident_metadata.get("incidentType"), "MANUAL")
        self.assertEqual(incident_metadata.get("reason"), self.reason)
        self.assertEqual(incident_metadata.get("action"), "SHUTDOWN")
        self.assertEqual(incident_metadata.get("triggerType"), "MANUAL")

        mock_log_audit.assert_called_once()

    @patch("routes.manual_routes.log_audit")
    @patch("routes.manual_routes.execute_mitigation")
    @patch("routes.manual_routes.prepare_mitigation")
    @patch("routes.manual_routes.find_open_incident")
    @patch("routes.manual_routes.evaluate_safety")
    def test_safety_failure_blocks_shutdown(
        self,
        mock_eval_safety,
        mock_find_open,
        mock_prepare,
        mock_execute_mitigation,
        mock_log_audit,
    ):
        mock_find_open.return_value = None
        mock_eval_safety.return_value = SafetyResult(
            safe=False,
            reason="SSH reachability unknown",
            failed_rule="RULE_3",
            checks={},
            timestamp=None,
            device_id=str(self.device_oid),
            interface=self.interface,
            status="UNSAFE",
        )

        res = self.client.post(
            "/api/manual/shutdown",
            headers=self._auth(self.admin_token),
            json={"deviceId": self.device_id, "interface": self.interface, "reason": self.reason},
        )
        self.assertEqual(res.status_code, 409)
        payload = res.get_json()
        self.assertFalse(payload["success"])
        self.assertIn("Safety failed", payload["message"])

        mock_prepare.assert_not_called()
        mock_execute_mitigation.assert_not_called()
        mock_log_audit.assert_not_called()

    @patch("routes.manual_routes.evaluate_safety")
    def test_duplicate_manual_shutdown_returns_409(self, mock_eval_safety):
        with patch("routes.manual_routes.find_open_incident", return_value={"incidentId": self.incident_id}):
            res = self.client.post(
                "/api/manual/shutdown",
                headers=self._auth(self.admin_token),
                json={"deviceId": self.device_id, "interface": self.interface, "reason": self.reason},
            )

        self.assertEqual(res.status_code, 409)
        mock_eval_safety.assert_not_called()

    def test_missing_reason_returns_400_for_manual_shutdown(self):
        res = self.client.post(
            "/api/manual/shutdown",
            headers=self._auth(self.admin_token),
            json={"deviceId": self.device_id, "interface": self.interface},
        )
        self.assertEqual(res.status_code, 400)

    @patch("routes.manual_routes.log_audit")
    @patch("routes.manual_routes.db")
    @patch("routes.manual_routes.execute_mitigation")
    @patch("routes.manual_routes.prepare_mitigation")
    @patch("routes.manual_routes.find_open_incident")
    @patch("routes.manual_routes.evaluate_safety")
    def test_lock_conflict_maps_to_409(
        self,
        mock_eval_safety,
        mock_find_open,
        mock_prepare,
        mock_execute_mitigation,
        mock_db,
        mock_log_audit,
    ):
        mock_find_open.return_value = None
        mock_eval_safety.return_value = SafetyResult(
            safe=True,
            reason="All safety checks passed",
            failed_rule=None,
            checks={},
            timestamp=None,
            device_id=str(self.device_oid),
            interface=self.interface,
            status="SAFE",
        )
        mock_prepare.return_value = {"ready": True, "incidentId": self.incident_id, "reason": "OK"}
        mock_execute_mitigation.return_value = {
            "success": False,
            "status": "MITIGATION_FAILED",
            "incidentId": self.incident_id,
            "error": "Mitigation lock conflict: Device is currently executing another mitigation.",
        }
        mock_db.storm_mitigation_history.find_one.return_value = {
            "verificationResult": {"success": False},
            "rollbackPerformed": False,
        }

        res = self.client.post(
            "/api/manual/shutdown",
            headers=self._auth(self.admin_token),
            json={"deviceId": self.device_id, "interface": self.interface, "reason": self.reason},
        )

        self.assertEqual(res.status_code, 409)
        payload = res.get_json()
        self.assertFalse(payload["success"])

        mock_log_audit.assert_called_once()

    @patch("routes.manual_routes.log_audit")
    @patch("routes.manual_routes.db")
    @patch("routes.manual_routes.execute_mitigation")
    @patch("routes.manual_routes.prepare_mitigation")
    @patch("routes.manual_routes.find_open_incident")
    @patch("routes.manual_routes.evaluate_safety")
    def test_verification_failure_records_rollback_yes_and_verification_failed(
        self,
        mock_eval_safety,
        mock_find_open,
        mock_prepare,
        mock_execute_mitigation,
        mock_db,
        mock_log_audit,
    ):
        mock_find_open.return_value = None
        mock_eval_safety.return_value = SafetyResult(
            safe=True,
            reason="All safety checks passed",
            failed_rule=None,
            checks={},
            timestamp=None,
            device_id=str(self.device_oid),
            interface=self.interface,
            status="SAFE",
        )
        mock_prepare.return_value = {
            "ready": True,
            "incidentId": self.incident_id,
            "reason": "OK",
        }
        mock_execute_mitigation.return_value = {
            "success": False,
            "status": "ROLLBACK_SUCCESS",
            "incidentId": self.incident_id,
            "error": "Verification Failed",
        }
        mock_db.storm_mitigation_history.find_one.return_value = {
            "verificationResult": {"success": False},
            "rollbackPerformed": True,
        }

        res = self.client.post(
            "/api/manual/shutdown",
            headers=self._auth(self.admin_token),
            json={"deviceId": self.device_id, "interface": self.interface, "reason": self.reason},
        )
        self.assertEqual(res.status_code, 400)
        payload = res.get_json()
        self.assertFalse(payload["success"])

        self.assertTrue(mock_log_audit.called)
        details = mock_log_audit.call_args.kwargs.get("details") or {}
        self.assertEqual(details.get("rollback"), "Yes")
        self.assertEqual(details.get("verification"), "Failed")
        self.assertEqual(details.get("result"), "Failed")

    def test_unauthorized_user_cannot_call_manual_shutdown(self):
        res = self.client.post(
            "/api/manual/shutdown",
            headers=self._auth(self.viewer_token),
            json={"deviceId": self.device_id, "interface": self.interface, "reason": self.reason},
        )
        self.assertEqual(res.status_code, 403)

    @patch("routes.manual_routes.log_audit")
    @patch("routes.manual_routes.execute_emergency_shutdown")
    def test_emergency_shutdown_super_admin_success(
        self,
        mock_execute_emergency,
        mock_log_audit,
    ):
        mock_execute_emergency.return_value = {
            "success": True,
            "status": "SUCCESS",
            "incidentId": self.incident_id,
            "error": None,
            "verificationPassed": True,
            "rollbackPerformed": False,
            "executionTimeMs": 1200,
        }

        res = self.client.post(
            "/api/manual/emergency-shutdown",
            headers=self._auth(self.super_token),
            json=self._emergency_payload(),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertTrue(payload["success"])
        mock_execute_emergency.assert_called_once()
        call_kwargs = mock_execute_emergency.call_args.kwargs
        self.assertEqual(call_kwargs["operator"], "super")
        self.assertEqual(call_kwargs["reason"], self.emergency_reason)
        mock_log_audit.assert_not_called()

    def test_emergency_shutdown_admin_rejected(self):
        res = self.client.post(
            "/api/manual/emergency-shutdown",
            headers=self._auth(self.admin_token),
            json=self._emergency_payload(),
        )
        self.assertEqual(res.status_code, 403)

    def test_emergency_shutdown_operator_rejected(self):
        res = self.client.post(
            "/api/manual/emergency-shutdown",
            headers=self._auth(self.operator_token),
            json=self._emergency_payload(),
        )
        self.assertEqual(res.status_code, 403)

    def test_emergency_shutdown_viewer_rejected(self):
        res = self.client.post(
            "/api/manual/emergency-shutdown",
            headers=self._auth(self.viewer_token),
            json=self._emergency_payload(),
        )
        self.assertEqual(res.status_code, 403)

    @patch("routes.manual_routes.log_audit")
    def test_emergency_shutdown_invalid_confirmation(self, mock_log_audit):
        res = self.client.post(
            "/api/manual/emergency-shutdown",
            headers=self._auth(self.super_token),
            json=self._emergency_payload(confirmation="shutdown"),
        )
        self.assertEqual(res.status_code, 400)
        mock_log_audit.assert_called()

    @patch("routes.manual_routes.log_audit")
    def test_emergency_shutdown_missing_reason(self, mock_log_audit):
        res = self.client.post(
            "/api/manual/emergency-shutdown",
            headers=self._auth(self.super_token),
            json=self._emergency_payload(reason=""),
        )
        self.assertEqual(res.status_code, 400)
        mock_log_audit.assert_called()

    @patch("routes.manual_routes.log_audit")
    @patch("routes.manual_routes.execute_emergency_shutdown")
    def test_emergency_shutdown_short_reason_rejected_by_service(
        self,
        mock_execute_emergency,
        mock_log_audit,
    ):
        mock_execute_emergency.side_effect = EmergencyShutdownError(
            "reason must be at least 10 characters",
            status_code=400,
        )
        res = self.client.post(
            "/api/manual/emergency-shutdown",
            headers=self._auth(self.super_token),
            json=self._emergency_payload(reason="too short"),
        )
        self.assertEqual(res.status_code, 400)
        mock_log_audit.assert_called()

    @patch("routes.manual_routes.log_audit")
    @patch("routes.manual_routes.execute_emergency_shutdown")
    def test_emergency_shutdown_lock_conflict(
        self,
        mock_execute_emergency,
        mock_log_audit,
    ):
        mock_execute_emergency.side_effect = EmergencyShutdownError(
            "Mitigation lock conflict: another operation is in progress",
            status_code=409,
        )
        res = self.client.post(
            "/api/manual/emergency-shutdown",
            headers=self._auth(self.super_token),
            json=self._emergency_payload(),
        )
        self.assertEqual(res.status_code, 409)
        mock_log_audit.assert_called()

    @patch("routes.manual_routes.log_audit")
    @patch("routes.manual_routes.execute_emergency_shutdown")
    def test_emergency_shutdown_ssh_failure(
        self,
        mock_execute_emergency,
        mock_log_audit,
    ):
        mock_execute_emergency.side_effect = EmergencyShutdownError(
            "SSH is unavailable for this device",
            status_code=503,
        )
        res = self.client.post(
            "/api/manual/emergency-shutdown",
            headers=self._auth(self.super_token),
            json=self._emergency_payload(),
        )
        self.assertEqual(res.status_code, 503)
        mock_log_audit.assert_called()

    @patch("routes.manual_routes.log_audit")
    @patch("routes.manual_routes.execute_emergency_shutdown")
    def test_emergency_shutdown_verification_failure_with_rollback(
        self,
        mock_execute_emergency,
        mock_log_audit,
    ):
        mock_execute_emergency.return_value = {
            "success": False,
            "status": "ROLLBACK_SUCCESS",
            "incidentId": self.incident_id,
            "error": "Verification failed after shutdown attempt",
            "verificationPassed": False,
            "rollbackPerformed": True,
            "executionTimeMs": 900,
        }

        res = self.client.post(
            "/api/manual/emergency-shutdown",
            headers=self._auth(self.super_token),
            json=self._emergency_payload(),
        )
        self.assertEqual(res.status_code, 400)
        payload = res.get_json()
        self.assertFalse(payload["success"])
        self.assertTrue(payload["rollbackPerformed"])

    @patch("routes.manual_routes.log_audit")
    @patch("routes.manual_routes.execute_recovery")
    @patch("routes.manual_routes.get_incident")
    def test_successful_manual_recovery(
        self,
        mock_get_incident,
        mock_execute_recovery,
        mock_log_audit,
    ):
        mock_get_incident.return_value = {
            "incidentId": self.incident_id,
            "status": "MITIGATED",
        }
        mock_execute_recovery.return_value = {
            "success": True,
            "status": "MONITORING",
            "incidentId": self.incident_id,
        }

        res = self.client.post(
            "/api/manual/recover",
            headers=self._auth(self.super_token),
            json={"incidentId": self.incident_id, "reason": self.reason},
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertTrue(payload["success"])
        mock_log_audit.assert_called_once()
        self.assertTrue(mock_execute_recovery.call_args.kwargs.get("force"))

    @patch("routes.manual_routes.log_audit")
    @patch("routes.manual_routes.execute_recovery")
    @patch("routes.manual_routes.get_incident")
    def test_admin_can_manual_recover(
        self,
        mock_get_incident,
        mock_execute_recovery,
        mock_log_audit,
    ):
        mock_get_incident.return_value = {
            "incidentId": self.incident_id,
            "status": "MITIGATED",
        }
        mock_execute_recovery.return_value = {
            "success": True,
            "status": "MONITORING",
            "incidentId": self.incident_id,
        }

        res = self.client.post(
            "/api/manual/recover",
            headers=self._auth(self.admin_token),
            json={"incidentId": self.incident_id, "reason": self.reason},
        )

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["success"])
        mock_execute_recovery.assert_called_once()
        self.assertTrue(mock_execute_recovery.call_args.kwargs.get("force"))

    def test_viewer_cannot_manual_recover(self):
        res = self.client.post(
            "/api/manual/recover",
            headers=self._auth(self.viewer_token),
            json={"incidentId": self.incident_id, "reason": self.reason},
        )
        self.assertEqual(res.status_code, 403)

    @patch("routes.manual_routes.log_audit")
    @patch("routes.manual_routes.db")
    @patch("routes.manual_routes.execute_recovery")
    @patch("routes.manual_routes.create_emergency_incident")
    @patch("routes.manual_routes.get_incident")
    def test_manual_recover_creates_incident_when_none_exists(
        self,
        mock_get_incident,
        mock_create_incident,
        mock_execute_recovery,
        mock_db,
        mock_log_audit,
    ):
        mock_get_incident.side_effect = [
            None,  # after create reload
        ]
        mock_db.storm_incidents.find_one.return_value = None
        mock_db.devices.find_one.return_value = {
            "_id": self.device_oid,
            "hostname": "sw1",
            "status": "Online",
        }
        mock_create_incident.return_value = {
            "incidentId": self.incident_id,
            "status": "OPEN",
        }
        mock_execute_recovery.return_value = {
            "success": True,
            "status": "MONITORING",
            "incidentId": self.incident_id,
        }
        mock_db.storm_recovery_history.find_one.return_value = {
            "verificationResult": {"success": True},
        }

        res = self.client.post(
            "/api/manual/recover",
            headers=self._auth(self.admin_token),
            json={
                "deviceId": self.device_id,
                "interface": self.interface,
                "reason": self.reason,
            },
        )

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["success"])
        mock_create_incident.assert_called_once()
        self.assertTrue(mock_execute_recovery.call_args.kwargs.get("force"))

    @patch("routes.manual_routes.log_audit")
    @patch("routes.manual_routes.execute_recovery")
    @patch("routes.manual_routes.get_incident")
    def test_manual_recovery_lock_conflict_maps_to_409(
        self,
        mock_get_incident,
        mock_execute_recovery,
        mock_log_audit,
    ):
        mock_get_incident.return_value = {
            "incidentId": self.incident_id,
            "status": "MITIGATED",
        }
        mock_execute_recovery.return_value = {
            "success": False,
            "status": "FAILURE",
            "incidentId": self.incident_id,
            "error": "Recovery lock conflict: Device is currently executing recovery.",
            "retryCount": 0,
        }

        res = self.client.post(
            "/api/manual/recover",
            headers=self._auth(self.super_token),
            json={"incidentId": self.incident_id, "reason": self.reason},
        )

        self.assertEqual(res.status_code, 409)
        payload = res.get_json()
        self.assertFalse(payload["success"])
        mock_log_audit.assert_called_once()


class EmergencyShutdownServiceTests(unittest.TestCase):
    device_oid = ObjectId("507f1f77bcf86cd799439011")

    @patch("services.storm.emergency_shutdown.log_audit")
    @patch("services.storm.emergency_shutdown.db")
    @patch("services.storm.emergency_shutdown.execute_mitigation")
    @patch("services.storm.emergency_shutdown.create_emergency_incident")
    @patch("services.storm.emergency_shutdown.LockService.is_mitigation_active", return_value=False)
    @patch("services.storm.emergency_shutdown.probe_ssh_readonly", return_value=(True, None))
    @patch("services.storm.emergency_shutdown.load_interface")
    @patch("services.interface_collection.ssh_collector.resolve_ssh_credentials")
    def test_execute_emergency_shutdown_uses_emergency_mode(
        self,
        mock_resolve_creds,
        mock_load_iface,
        mock_probe,
        mock_lock_active,
        mock_create_incident,
        mock_execute,
        mock_db,
        mock_log_audit,
    ):
        mock_resolve_creds.return_value = MagicMock(vendor="cisco")
        mock_load_iface.return_value = {"name": "Gi1/0/10", "vendor": "cisco"}
        mock_db.devices.find_one.return_value = {
            "_id": self.device_oid,
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "status": "Online",
            "credentials": {},
        }
        mock_create_incident.return_value = {"incidentId": "storm-2026-000001"}
        mock_execute.return_value = {"success": True, "status": "SUCCESS"}
        mock_db.storm_mitigation_history.find_one.return_value = {
            "verificationResult": {"success": True},
            "rollbackPerformed": False,
        }

        result = execute_emergency_shutdown(
            device_id=str(self.device_oid),
            interface="Gi1/0/10",
            reason="Security incident isolation required immediately",
            operator="super",
            source_ip="127.0.0.1",
            session_id="sess-1",
        )

        self.assertTrue(result["success"])
        mock_execute.assert_called_once()
        self.assertEqual(mock_execute.call_args.kwargs.get("execution_mode"), "EMERGENCY")
        incident_kwargs = mock_create_incident.call_args.kwargs
        self.assertEqual(incident_kwargs.get("trigger_type"), "MANUAL")
        self.assertEqual(incident_kwargs.get("incident_type"), "EMERGENCY")
        mock_log_audit.assert_called_once()
        audit_details = mock_log_audit.call_args.kwargs.get("details") or {}
        self.assertTrue(audit_details.get("emergency"))

    @patch("services.storm.emergency_shutdown.db")
    @patch("services.storm.emergency_shutdown.LockService.is_mitigation_active", return_value=True)
    @patch("services.storm.emergency_shutdown.probe_ssh_readonly", return_value=(True, None))
    @patch("services.storm.emergency_shutdown.load_interface")
    @patch("services.interface_collection.ssh_collector.resolve_ssh_credentials")
    def test_validate_rejects_active_lock(
        self,
        mock_resolve_creds,
        mock_load_iface,
        mock_probe,
        mock_lock_active,
        mock_db,
    ):
        mock_resolve_creds.return_value = MagicMock(vendor="cisco")
        mock_load_iface.return_value = {"name": "Gi1/0/10"}
        mock_db.devices.find_one.return_value = {
            "_id": self.device_oid,
            "status": "Online",
            "ipAddress": "10.0.0.1",
            "credentials": {},
        }

        with self.assertRaises(EmergencyShutdownError) as ctx:
            validate_emergency_request(
                device_id=str(self.device_oid),
                interface="Gi1/0/10",
                reason="Security incident isolation required immediately",
                confirmation=EMERGENCY_CONFIRMATION,
            )
        self.assertEqual(ctx.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
