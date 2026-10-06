"""Step 2: iLO identity, address validation, credentials, indexes, monitoring exclusion."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_mock_db_module = MagicMock()
_mock_db_module.db = MagicMock()
sys.modules.setdefault("config.database", _mock_db_module)

from bson import ObjectId

from models.device import create_device, normalize_device_credentials
from services.device_indexes import (
    ILO_UNIQUE_INDEX_NAME,
    IP_UNIQUE_INDEX_NAME,
    ensure_device_indexes,
)
from services.monitor_claim import build_due_unclaimed_filter, claim_device
from utils.management_address import (
    is_ilo_only_device_type,
    normalize_ilo_address,
    normalize_os_ip,
)
from utils.serializers import serialize_credentials, serialize_device
from utils.utc import utc_now


class ManagementAddressTests(unittest.TestCase):
    def test_normalize_os_ip(self):
        self.assertEqual(normalize_os_ip(" 10.0.0.1 "), "10.0.0.1")
        with self.assertRaises(ValueError):
            normalize_os_ip("")
        with self.assertRaises(ValueError):
            normalize_os_ip("not-an-ip")

    def test_ilo_ipv4_and_hostname_lowercase(self):
        self.assertEqual(normalize_ilo_address("10.1.2.3"), "10.1.2.3")
        self.assertEqual(normalize_ilo_address("ILO.Example.COM"), "ilo.example.com")

    def test_ilo_rejects_urls_and_whitespace(self):
        for bad in (
            "https://ilo.example/redfish/v1",
            "http://ilo.example",
            "ilo.example/redfish/v1",
            "user@ilo.example",
            "ilo.example:443",
            "ilo example.com",
            " ilo.example.com",
            "",
        ):
            with self.assertRaises(ValueError, msg=bad):
                normalize_ilo_address(bad)

    def test_ilo_only_types(self):
        self.assertTrue(is_ilo_only_device_type("Server"))
        self.assertTrue(is_ilo_only_device_type("Linux Server"))
        self.assertTrue(is_ilo_only_device_type("ESXi Server"))
        self.assertFalse(is_ilo_only_device_type("Switch"))


class CreateDeviceIdentityTests(unittest.TestCase):
    def test_os_ip_device_unchanged(self):
        doc = create_device("sw1", "10.0.0.1", "Switch", monitor=True)
        self.assertEqual(doc["ipAddress"], "10.0.0.1")
        self.assertNotIn("iloAddress", doc)
        self.assertTrue(doc["monitor"])
        self.assertIn("nextCheckAt", doc)

    def test_ilo_only_omits_ip_and_forces_monitor_off(self):
        doc = create_device(
            "srv1",
            None,
            "Server",
            monitor=True,
            ilo_address="ilo.example.com",
        )
        self.assertNotIn("ipAddress", doc)
        self.assertEqual(doc["iloAddress"], "ilo.example.com")
        self.assertFalse(doc["monitor"])
        self.assertNotIn("nextCheckAt", doc)

    def test_both_addresses(self):
        doc = create_device(
            "srv2",
            "10.0.0.5",
            "Server",
            monitor=True,
            ilo_address="10.0.0.6",
        )
        self.assertEqual(doc["ipAddress"], "10.0.0.5")
        self.assertEqual(doc["iloAddress"], "10.0.0.6")
        self.assertTrue(doc["monitor"])


class CredentialSerializationTests(unittest.TestCase):
    @patch("models.device.encrypt_secret", side_effect=lambda v: f"npenc:{v}")
    def test_ilo_password_encrypted_and_preserved(self, _enc):
        first = normalize_device_credentials(
            {"iloUsername": "admin", "iloPassword": "secret", "iloPort": 443}
        )
        self.assertEqual(first["iloPassword"], "npenc:secret")
        self.assertEqual(first["iloUsername"], "admin")
        self.assertEqual(first["iloPort"], 443)

        second = normalize_device_credentials(
            {"iloUsername": "admin2"},
            existing=first,
        )
        self.assertEqual(second["iloPassword"], "npenc:secret")
        self.assertEqual(second["iloUsername"], "admin2")

    def test_serialize_never_returns_password(self):
        creds = {
            "iloUsername": "admin",
            "iloPassword": "npenc:cipher",
            "iloPort": 443,
            "sshPassword": "npenc:ssh",
        }
        out = serialize_credentials(creds)
        self.assertNotIn("iloPassword", out)
        self.assertNotIn("sshPassword", out)
        self.assertTrue(out["iloPasswordConfigured"])
        self.assertEqual(out["iloUsername"], "admin")
        self.assertEqual(out["iloPort"], 443)
        self.assertNotIn("npenc:", str(out))

    def test_serialize_device_includes_ilo_address(self):
        doc = {
            "_id": ObjectId(),
            "hostname": "h",
            "iloAddress": "ilo.example.com",
            "deviceType": "Server",
            "credentials": {"iloPassword": "npenc:x"},
        }
        serialized = serialize_device(doc)
        self.assertIsNone(serialized.get("ipAddress"))
        self.assertEqual(serialized["iloAddress"], "ilo.example.com")
        self.assertNotIn("iloPassword", serialized["credentials"])
        self.assertTrue(serialized["credentials"]["iloPasswordConfigured"])


class MonitorEligibilityTests(unittest.TestCase):
    def test_due_filter_requires_os_ip(self):
        filt = build_due_unclaimed_filter(utc_now())
        self.assertEqual(filt["monitor"], True)
        self.assertEqual(filt["ipAddress"], {"$type": "string", "$gt": ""})

    def test_claim_skips_without_os_ip(self):
        result = claim_device(
            ObjectId(),
            device={"hostname": "ilo-only", "monitor": True},
        )
        self.assertIsNone(result)


class DeviceIndexMigrationTests(unittest.TestCase):
    def test_ensure_partial_indexes_from_legacy(self):
        state = {
            "indexes": {
                "_id_": {"name": "_id_", "key": {"_id": 1}},
                IP_UNIQUE_INDEX_NAME: {
                    "name": IP_UNIQUE_INDEX_NAME,
                    "key": {"ipAddress": 1},
                    "unique": True,
                },
            }
        }

        mock_devices = MagicMock()

        def list_indexes():
            return list(state["indexes"].values())

        def create_index(keys, **kwargs):
            name = kwargs["name"]
            doc = {
                "name": name,
                "key": {k: v for k, v in keys},
                "unique": bool(kwargs.get("unique")),
            }
            if "partialFilterExpression" in kwargs:
                doc["partialFilterExpression"] = kwargs["partialFilterExpression"]
            state["indexes"][name] = doc
            return name

        def drop_index(name):
            state["indexes"].pop(name, None)

        mock_devices.list_indexes.side_effect = list_indexes
        mock_devices.create_index.side_effect = create_index
        mock_devices.drop_index.side_effect = drop_index

        with patch("services.device_indexes.db") as mock_db:
            mock_db.devices = mock_devices
            ensure_device_indexes()
            ensure_device_indexes()

        self.assertIn(IP_UNIQUE_INDEX_NAME, state["indexes"])
        self.assertIn(ILO_UNIQUE_INDEX_NAME, state["indexes"])
        self.assertNotIn("uniq_devices_ipAddress_partial_mig", state["indexes"])
        ip_idx = state["indexes"][IP_UNIQUE_INDEX_NAME]
        self.assertTrue(ip_idx["unique"])
        self.assertEqual(
            ip_idx["partialFilterExpression"],
            {"ipAddress": {"$type": "string", "$gt": ""}},
        )
        self.assertEqual(
            state["indexes"][ILO_UNIQUE_INDEX_NAME]["partialFilterExpression"],
            {"iloAddress": {"$type": "string", "$gt": ""}},
        )

    def test_recover_interrupted_staging(self):
        state = {
            "indexes": {
                "_id_": {"name": "_id_", "key": {"_id": 1}},
                "uniq_devices_ipAddress_partial_mig": {
                    "name": "uniq_devices_ipAddress_partial_mig",
                    "key": {"ipAddress": 1},
                    "unique": True,
                    "partialFilterExpression": {
                        "ipAddress": {"$type": "string", "$gt": ""}
                    },
                },
            }
        }
        mock_devices = MagicMock()

        def list_indexes():
            return list(state["indexes"].values())

        def create_index(keys, **kwargs):
            name = kwargs["name"]
            doc = {
                "name": name,
                "key": {k: v for k, v in keys},
                "unique": bool(kwargs.get("unique")),
            }
            if "partialFilterExpression" in kwargs:
                doc["partialFilterExpression"] = kwargs["partialFilterExpression"]
            state["indexes"][name] = doc
            return name

        def drop_index(name):
            state["indexes"].pop(name, None)

        mock_devices.list_indexes.side_effect = list_indexes
        mock_devices.create_index.side_effect = create_index
        mock_devices.drop_index.side_effect = drop_index

        with patch("services.device_indexes.db") as mock_db:
            mock_db.devices = mock_devices
            ensure_device_indexes()

        self.assertIn(IP_UNIQUE_INDEX_NAME, state["indexes"])
        self.assertNotIn("uniq_devices_ipAddress_partial_mig", state["indexes"])


class DeviceRouteIloMatrixTests(unittest.TestCase):
    def setUp(self):
        from app import app
        from utils.auth import create_access_token

        self.app = app
        self.client = app.test_client()
        admin_id = str(ObjectId())
        token = create_access_token(user_id=admin_id, username="admin", role="admin")
        self.auth_headers = {"Authorization": f"Bearer {token}"}

    @patch("routes.device_routes.db")
    def test_add_device_ilo_matrix_routes(self, mock_db):
        inserted_id = ObjectId("507f1f77bcf86cd799439011")
        mock_db.devices.insert_one.return_value.inserted_id = inserted_id
        mock_db.users.find_one.return_value = {"_id": ObjectId(), "username": "admin", "active": True}

        def mock_find_one(query):
            if query.get("_id") == inserted_id:
                return {
                    "_id": inserted_id,
                    "hostname": "srv-ilo1",
                    "deviceType": "Server",
                    "iloAddress": "ilo.example.com",
                    "credentials": {"iloUsername": "admin", "iloPassword": "npenc:secretpassword"},
                    "monitor": False,
                }
            return None

        mock_db.devices.find_one.side_effect = mock_find_one

        # 1. iLO-only server record -> Success
        payload_ilo_only = {
            "hostname": "srv-ilo1",
            "deviceType": "Server",
            "iloAddress": "ilo.example.com",
            "credentials": {"iloUsername": "admin", "iloPassword": "secretpassword"}
        }
        res = self.client.post("/api/devices", json=payload_ilo_only, headers=self.auth_headers)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()["data"]
        self.assertIsNone(data.get("ipAddress"))
        self.assertEqual(data.get("iloAddress"), "ilo.example.com")
        self.assertNotIn("iloPassword", data["credentials"])

        # 2. iLO-only non-server deviceType -> Rejection (400)
        payload_bad_type = {
            "hostname": "sw-ilo",
            "deviceType": "Switch",
            "iloAddress": "10.0.0.50"
        }
        res_bad = self.client.post("/api/devices", json=payload_bad_type, headers=self.auth_headers)
        self.assertEqual(res_bad.status_code, 400)
        self.assertIn("iLO-only devices must use device type", res_bad.get_json()["message"])

        # 3. Neither address -> Rejection (400)
        res_empty = self.client.post("/api/devices", json={"hostname": "no-addr", "deviceType": "Server"}, headers=self.auth_headers)
        self.assertEqual(res_empty.status_code, 400)
        self.assertIn("At least one of ipAddress or iloAddress is required", res_empty.get_json()["message"])

        # 4. Malformed iloAddress URL -> Rejection (400)
        res_url = self.client.post("/api/devices", json={
            "hostname": "bad-url",
            "deviceType": "Server",
            "iloAddress": "https://10.0.0.1/redfish"
        }, headers=self.auth_headers)
        self.assertEqual(res_url.status_code, 400)
        self.assertIn("iloAddress must not be a URL", res_url.get_json()["message"])

    @patch("routes.device_routes.db")
    def test_update_device_ilo_address_routes(self, mock_db):
        oid = ObjectId("507f1f77bcf86cd799439011")
        existing = {
            "_id": oid,
            "hostname": "srv1",
            "deviceType": "Server",
            "ipAddress": "10.0.0.10",
            "monitor": True
        }
        mock_db.devices.find_one.side_effect = lambda query: existing if query.get("_id") == oid else None
        mock_db.users.find_one.return_value = {"_id": ObjectId(), "username": "admin", "active": True}

        # Add iloAddress to existing OS device -> Success
        res = self.client.put(f"/api/devices/{oid}", json={"iloAddress": "ILO.Server1.Local"}, headers=self.auth_headers)
        self.assertEqual(res.status_code, 200)
        mock_db.devices.update_one.assert_called()


if __name__ == "__main__":
    unittest.main()

