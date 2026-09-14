from __future__ import annotations

import unittest
from unittest.mock import patch

from bson import ObjectId

from tests.app_bootstrap import load_test_app
from utils.auth import create_access_token

app = load_test_app()


class SwitchHardwareRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.device_id = str(ObjectId("507f1f77bcf86cd799439011"))

    def _headers(self, role: str = "admin") -> dict[str, str]:
        token = create_access_token(
            user_id=str(ObjectId()),
            username=role,
            role=role,
        )
        return {"Authorization": f"Bearer {token}"}

    @patch("routes.switch_hardware_routes.db")
    def test_get_hardware_requires_eligible_device(self, mock_db):
        mock_db.devices.find_one.return_value = {
            "_id": ObjectId(self.device_id),
            "deviceType": "Linux Server",
            "monitor": True,
        }
        res = self.client.get(
            f"/api/switches/{self.device_id}/hardware",
            headers=self._headers("user"),
        )
        self.assertEqual(res.status_code, 400)

    @patch("routes.switch_hardware_routes.log_audit")
    @patch("routes.switch_hardware_routes.collect_device_hardware")
    @patch("routes.switch_hardware_routes.db")
    def test_user_cannot_manual_collect(self, mock_db, mock_collect, _audit):
        mock_db.devices.find_one.return_value = {
            "_id": ObjectId(self.device_id),
            "deviceType": "Managed Switch",
            "vendor": "Cisco",
            "monitor": True,
        }
        res = self.client.post(
            f"/api/switches/{self.device_id}/hardware/collect",
            headers=self._headers("user"),
        )
        self.assertEqual(res.status_code, 403)
        mock_collect.assert_not_called()

    @patch("routes.switch_hardware_routes.log_audit")
    @patch("routes.switch_hardware_routes.collect_device_hardware")
    @patch("routes.switch_hardware_routes.db")
    def test_admin_manual_collect(self, mock_db, mock_collect, _audit):
        mock_db.devices.find_one.return_value = {
            "_id": ObjectId(self.device_id),
            "deviceType": "Managed Switch",
            "vendor": "Cisco",
            "monitor": True,
        }
        mock_collect.return_value = {
            "success": True,
            "document": {"deviceId": ObjectId(self.device_id), "collectionStatus": "success"},
            "errors": [],
        }
        res = self.client.post(
            f"/api/switches/{self.device_id}/hardware/collect",
            headers=self._headers("admin"),
        )
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertTrue(body["success"])
        self.assertNotIn("password", str(body).lower())


if __name__ == "__main__":
    unittest.main()
