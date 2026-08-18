from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bson import ObjectId

from app import app
from services.storm.mitigation.strategy import ShutdownInterfaceStrategy
from utils.auth import create_access_token


class ManualInterfaceControlRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.device_id = str(ObjectId("507f1f77bcf86cd799439011"))
        self.interface = "Gi1/0/10"
        self.device_doc = {
            "_id": ObjectId(self.device_id),
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
            "status": "Online",
            "credentials": {
                "sshUsername": "admin",
                "sshPassword": "password",
                "sshVendor": "cisco_ios",
            },
        }
        self.incident_doc = {
            "incidentId": "storm-2026-009999",
            "deviceId": ObjectId(self.device_id),
            "interface": self.interface,
            "status": "OPEN",
            "incidentType": "MANUAL",
            "hostname": "sw1",
            "ipAddress": "10.0.0.1",
        }

    def _auth_headers(self, role: str, username: str = "user") -> dict[str, str]:
        token = create_access_token(
            user_id=str(ObjectId()),
            username=username,
            role=role,
        )
        return {"Authorization": f"Bearer {token}"}

    @patch("routes.interface_routes.execute_mitigation")
    @patch("routes.interface_routes.create_manual_incident")
    def test_user_forbidden_no_incident_no_ssh(self, mock_create_incident, mock_execute):
        res = self.client.post(
            f"/api/interfaces/{self.device_id}/{self.interface}/manual-shutdown",
            json={"confirm": True},
            headers=self._auth_headers("user"),
        )

        self.assertEqual(res.status_code, 403)
        mock_create_incident.assert_not_called()
        mock_execute.assert_not_called()

    @patch("routes.interface_routes.execute_mitigation")
    @patch("routes.interface_routes.create_manual_incident")
    def test_missing_confirm_rejected(self, mock_create_incident, mock_execute):
        res = self.client.post(
            f"/api/interfaces/{self.device_id}/{self.interface}/manual-shutdown",
            json={"reason": "break-glass test"},
            headers=self._auth_headers("admin"),
        )

        self.assertEqual(res.status_code, 400)
        mock_create_incident.assert_not_called()
        mock_execute.assert_not_called()

    def test_missing_reason_rejected(self):
        res = self.client.post(
            f"/api/interfaces/{self.device_id}/{self.interface}/manual-shutdown",
            json={"confirm": True},
            headers=self._auth_headers("admin"),
        )
        self.assertEqual(res.status_code, 400)

    def test_user_forbidden_without_reason(self):
        res = self.client.post(
            f"/api/interfaces/{self.device_id}/{self.interface}/manual-shutdown",
            json={"confirm": True, "reason": "test"},
            headers=self._auth_headers("user"),
        )
        self.assertNotEqual(res.status_code, 403)

    @patch("services.storm.mitigation.audit.log_audit")
    @patch("routes.interface_routes.log_audit")
    @patch("routes.interface_routes.db")
    @patch("services.storm.mitigation.engine.LockService.release_mitigation_locks")
    @patch("services.storm.mitigation.engine.LockService.acquire_mitigation_locks")
    @patch("services.storm.mitigation.engine.get_incident")
    @patch("services.storm.mitigation.engine._db")
    @patch("services.storm.mitigation.ssh_executor.SSHInterfaceCollector")
    @patch("routes.interface_routes.create_manual_incident")
    def test_manual_shutdown_success_path(
        self,
        mock_create_incident,
        mock_ssh,
        mock_db_fn,
        mock_get_incident,
        mock_acquire,
        mock_release,
        mock_route_db,
        mock_route_audit,
        mock_mit_audit,
    ):
        mock_create_incident.return_value = self.incident_doc
        mock_get_incident.return_value = self.incident_doc
        mock_acquire.return_value = ("device:lock", "iface:lock")
        mock_route_db.devices.find_one.return_value = self.device_doc

        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        mock_db_fn.return_value = fake_db

        mock_collector = MagicMock()
        mock_ssh.return_value = mock_collector
        mock_collector.run_command.side_effect = lambda cmd, wait=0.4: (
            f"interface {self.interface}\n shutdown\n" if "show" in cmd else "OK"
        )

        res = self.client.post(
            f"/api/interfaces/{self.device_id}/{self.interface}/manual-shutdown",
            json={"confirm": True, "reason": "operator break-glass"},
            headers=self._auth_headers("admin", "admin1"),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["incidentId"], self.incident_doc["incidentId"])

        expected_commands = ShutdownInterfaceStrategy().get_commands(self.interface, "cisco_ios")
        run_cmds = [call.args[0] for call in mock_collector.run_command.call_args_list]
        for cmd in expected_commands:
            self.assertIn(cmd, run_cmds)

        update_call = fake_db.storm_incidents.update_one.call_args
        self.assertEqual(
            update_call[0][0],
            {"incidentId": self.incident_doc["incidentId"]},
        )
        self.assertEqual(update_call[0][1]["$set"]["status"], "MITIGATED")
        mock_route_audit.assert_called_once()

    @patch("services.storm.mitigation.audit.log_audit")
    @patch("routes.interface_routes.log_audit")
    @patch("routes.interface_routes.db")
    @patch("services.storm.mitigation.engine.LockService.release_mitigation_locks")
    @patch("services.storm.mitigation.engine.LockService.acquire_mitigation_locks")
    @patch("services.storm.mitigation.engine.get_incident")
    @patch("services.storm.mitigation.engine._db")
    @patch("services.storm.mitigation.ssh_executor.SSHInterfaceCollector")
    @patch("routes.interface_routes.create_manual_incident")
    def test_manual_shutdown_ssh_failure_sets_mitigation_failed_and_audits(
        self,
        mock_create_incident,
        mock_ssh,
        mock_db_fn,
        mock_get_incident,
        mock_acquire,
        mock_release,
        mock_route_db,
        mock_route_audit,
        mock_mit_audit,
    ):
        mock_create_incident.return_value = self.incident_doc
        mock_get_incident.return_value = self.incident_doc
        mock_acquire.return_value = ("device:lock", "iface:lock")
        mock_route_db.devices.find_one.return_value = self.device_doc

        fake_db = MagicMock()
        fake_db.devices.find_one.return_value = self.device_doc
        mock_db_fn.return_value = fake_db

        mock_collector = MagicMock()
        mock_ssh.return_value = mock_collector
        mock_collector.connect.side_effect = RuntimeError("SSH Timeout")

        res = self.client.post(
            f"/api/interfaces/{self.device_id}/{self.interface}/manual-shutdown",
            json={"confirm": True, "reason": "SSH failure test"},
            headers=self._auth_headers("admin", "admin1"),
        )

        self.assertEqual(res.status_code, 400)
        payload = res.get_json()
        self.assertFalse(payload["success"])

        fake_db.storm_incidents.update_one.assert_called_with(
            {"incidentId": self.incident_doc["incidentId"]},
            {"$set": {"status": "MITIGATION_FAILED", "updatedAt": unittest.mock.ANY}},
        )
        mock_route_audit.assert_called_once()

    @patch("routes.interface_routes.execute_manual_recovery")
    def test_user_forbidden_for_manual_recover_without_confirm(self, mock_execute):
        res = self.client.post(
            f"/api/interfaces/{self.device_id}/{self.interface}/manual-recover",
            json={"confirm": True},
            headers=self._auth_headers("user"),
        )

        self.assertNotEqual(res.status_code, 403)

    @patch("routes.interface_routes.execute_manual_recovery")
    def test_manual_recover_missing_confirm_rejected(self, mock_execute):
        res = self.client.post(
            f"/api/interfaces/{self.device_id}/{self.interface}/manual-recover",
            json={},
            headers=self._auth_headers("user"),
        )

        self.assertEqual(res.status_code, 400)
        mock_execute.assert_not_called()

    @patch("routes.interface_routes.log_audit")
    @patch("routes.interface_routes.execute_manual_recovery")
    @patch("routes.interface_routes.db")
    def test_manual_recover_invalid_state_rejected(
        self, mock_db, mock_execute, mock_audit
    ):
        non_mitigated = dict(self.incident_doc, status="OPEN")
        mock_db.devices.find_one.return_value = self.device_doc
        mock_db.storm_incidents.find_one.return_value = non_mitigated

        res = self.client.post(
            f"/api/interfaces/{self.device_id}/{self.interface}/manual-recover",
            json={"confirm": True},
            headers=self._auth_headers("user"),
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("MITIGATED", res.get_json()["message"])
        mock_execute.assert_not_called()
        mock_audit.assert_not_called()

    @patch("routes.interface_routes.log_audit")
    @patch("routes.interface_routes.execute_manual_recovery")
    @patch("routes.interface_routes.db")
    def test_manual_recover_success_path(self, mock_db, mock_execute, mock_audit):
        mitigated = dict(self.incident_doc, status="MITIGATED", recoveryRetryCount=0)
        mock_db.devices.find_one.return_value = self.device_doc
        mock_db.storm_incidents.find_one.return_value = mitigated
        mock_execute.return_value = {
            "success": True,
            "status": "RECOVERED",
            "incidentId": self.incident_doc["incidentId"],
        }

        res = self.client.post(
            f"/api/interfaces/{self.device_id}/{self.interface}/manual-recover",
            json={"confirm": True},
            headers=self._auth_headers("admin", "admin1"),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertTrue(payload["success"])
        mock_execute.assert_called_once_with(
            self.incident_doc["incidentId"],
            operator="admin1",
        )
        mock_audit.assert_called_once()

    @patch("routes.interface_routes.log_audit")
    @patch("routes.interface_routes.execute_manual_recovery")
    @patch("routes.interface_routes.db")
    def test_manual_recover_failure_path(self, mock_db, mock_execute, mock_audit):
        mitigated = dict(self.incident_doc, status="MITIGATED", recoveryRetryCount=1)
        mock_db.devices.find_one.return_value = self.device_doc
        mock_db.storm_incidents.find_one.return_value = mitigated
        mock_execute.return_value = {
            "success": False,
            "status": "FAILED",
            "incidentId": self.incident_doc["incidentId"],
            "error": "Unable to establish SSH connection.",
        }

        res = self.client.post(
            f"/api/interfaces/{self.device_id}/{self.interface}/manual-recover",
            json={"confirm": True},
            headers=self._auth_headers("user", "user1"),
        )

        self.assertEqual(res.status_code, 400)
        payload = res.get_json()
        self.assertFalse(payload["success"])
        self.assertIn("SSH", payload["message"])
        mock_audit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
