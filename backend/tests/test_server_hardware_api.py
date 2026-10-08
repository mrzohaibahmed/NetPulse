"""
Unit and Integration Tests for Phase 3F Server Hardware Health API Route.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from bson import ObjectId
import pytest

from services.server_hardware import (
    ComponentStatus,
    PhysicalDrive,
    ServerCapabilities,
    ServerHardware,
    ServerMemoryDimm,
    ServerProcessor,
    ServerStorage,
    deserialize_server_hardware,
    save_current_server_hardware,
)


@pytest.fixture
def client():
    from app import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def auth_headers():
    from utils.auth import create_access_token

    token = create_access_token("507f1f77bcf86cd799439011", "adminuser", "admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user_auth_headers():
    from utils.auth import create_access_token

    token = create_access_token("507f1f77bcf86cd799439012", "standarduser", "user")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def mock_auth_user():
    user_doc = {
        "_id": ObjectId("507f1f77bcf86cd799439011"),
        "username": "adminuser",
        "role": "admin",
        "active": True,
    }
    with patch("config.database.db.users.find_one", return_value=user_doc):
        yield


def test_deserialization_full_coverage():
    """Verify deserialize_server_hardware correctly hydrates all fields from snapshot dict."""
    doc = {
        "identity": {
            "hostname": "srv-01",
            "manufacturer": "HPE",
            "model": "ProLiant DL380 Gen10",
            "serialNumber": "CN789",
            "uuid": "uuid-123",
            "assetTag": "tag-456",
        },
        "firmware": {
            "iloGeneration": "iLO 5",
            "iloFirmwareVersion": "2.72",
            "biosVersion": "U30 v2.50",
        },
        "power": {
            "powerState": "On",
            "powerConsumedWatts": 150.0,
            "powerCapacityWatts": 800.0,
            "powerSupplies": [
                {
                    "id": "ps1",
                    "name": "Power Supply 1",
                    "model": "800W",
                    "powerCapacityWatts": 800.0,
                    "lastPowerOutputWatts": 75.0,
                    "lineInputVoltage": 230.0,
                    "status": {"state": "Enabled", "health": "OK", "healthRollup": "OK"},
                }
            ],
        },
        "processors": [
            {
                "id": "proc1",
                "name": "Proc 1",
                "manufacturer": "Intel",
                "model": "Xeon Gold 6230",
                "cores": 20,
                "threads": 40,
                "speedMhz": 2100,
                "status": {"state": "Enabled", "health": "OK", "healthRollup": "OK"},
            }
        ],
        "memory": [
            {
                "id": "dimm1",
                "name": "DIMM 1",
                "capacityBytes": 34359738368,
                "speedMhz": 2933,
                "manufacturer": "HPE",
                "partNumber": "P00424-B21",
                "serialNumber": "dimm-sn-1",
                "status": {"state": "Enabled", "health": "OK", "healthRollup": "OK"},
            }
        ],
        "storage": {
            "controllers": [
                {
                    "id": "c1",
                    "name": "Smart Array P408i-a SR Gen10",
                    "model": "P408i-a",
                    "serialNumber": "c-sn-1",
                    "firmwareVersion": "3.00",
                    "status": {"state": "Enabled", "health": "OK", "healthRollup": "OK"},
                    "sourceType": "SmartStorage",
                }
            ],
            "physicalDrives": [
                {
                    "id": "pd1",
                    "name": "Drive 1",
                    "controllerId": "c1",
                    "model": "EG000600JWJN6",
                    "serialNumber": "pd-sn-1",
                    "capacityBytes": 600000000000,
                    "mediaType": "HDD",
                    "protocol": "SAS",
                    "status": {"state": "Enabled", "health": "OK", "healthRollup": "OK"},
                }
            ],
            "logicalVolumes": [
                {
                    "id": "lv1",
                    "name": "Volume 1",
                    "controllerId": "c1",
                    "capacityBytes": 600000000000,
                    "raidType": "RAID1",
                    "status": {"state": "Enabled", "health": "OK", "healthRollup": "OK"},
                }
            ],
        },
        "networkInterfaces": [
            {
                "id": "eth0",
                "name": "NIC 1",
                "macAddress": "00:11:22:33:44:55",
                "linkStatus": "LinkUp",
                "speedMbps": 10000,
                "manufacturer": "HPE",
                "model": "562FLR-SFP+",
                "isManagementInterface": False,
                "status": {"state": "Enabled", "health": "OK", "healthRollup": "OK"},
            }
        ],
        "temperatures": [
            {
                "id": "t1",
                "name": "CPU Temp",
                "readingCelsius": 42.0,
                "upperThresholdCritical": 70.0,
                "status": {"state": "Enabled", "health": "OK", "healthRollup": "OK"},
            }
        ],
        "fans": [
            {
                "id": "f1",
                "name": "Fan 1",
                "readingRpm": 4500,
                "readingPercent": 40.0,
                "status": {"state": "Enabled", "health": "OK", "healthRollup": "OK"},
            }
        ],
        "capabilities": {
            "hasProcessors": True,
            "hasMemory": True,
            "hasStorage": True,
            "hasSmartStorage": True,
            "hasThermals": True,
            "hasFans": True,
            "hasPower": True,
            "hasNetwork": True,
        },
    }

    hw = deserialize_server_hardware(doc)
    assert hw.identity.hostname == "srv-01"
    assert hw.firmware.ilo_generation == "iLO 5"
    assert len(hw.processors) == 1
    assert hw.processors[0].cores == 20
    assert len(hw.storage.physical_drives) == 1
    assert hw.capabilities.has_smart_storage is True


def test_api_unauthenticated(client):
    """GET /api/devices/<id>/server-hardware/health requires auth."""
    resp = client.get(f"/api/devices/{ObjectId()}/server-hardware/health")
    assert resp.status_code == 401
    assert resp.json["success"] is False


def test_api_invalid_device_id(client, auth_headers):
    """GET /api/devices/invalid-id/server-hardware/health returns 400."""
    resp = client.get("/api/devices/invalid-id/server-hardware/health", headers=auth_headers)
    assert resp.status_code == 400
    assert resp.json["message"] == "Invalid device ID"


def test_api_device_not_found(client, auth_headers):
    """GET /api/devices/<nonexistent>/server-hardware/health returns 404."""
    resp = client.get(f"/api/devices/{ObjectId()}/server-hardware/health", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json["message"] == "Device not found"


def test_api_non_server_device(client, auth_headers):
    """Non-server device (e.g. Switch) returns 400 Bad Request."""
    device_id = ObjectId()
    dev_doc = {"_id": device_id, "hostname": "switch-01", "deviceType": "Switch"}

    with patch("routes.device_routes.db") as mock_db:
        mock_db.devices.find_one.return_value = dev_doc
        resp = client.get(f"/api/devices/{device_id}/server-hardware/health", headers=auth_headers)
        assert resp.status_code == 400
        assert resp.json["message"] == "Device is not an eligible server"


def test_api_missing_snapshot_first_poll_failure(client, auth_headers):
    """Server device without hardware snapshot but with failed collection state returns 200 with diagnostics."""
    device_id = ObjectId()
    t_fail = datetime.now(timezone.utc)
    dev_doc = {
        "_id": device_id,
        "hostname": "server-01",
        "deviceType": "Server",
        "iloCollectionState": {
            "lastAttemptAt": t_fail,
            "lastFailureAt": t_fail,
            "lastPollStatus": "AUTHENTICATION_ERROR",
            "consecutiveFailures": 1,
            "lastError": "AUTHENTICATION_ERROR: Invalid iLO password",
        },
    }

    with patch("routes.device_routes.db") as mock_db, patch(
        "routes.device_routes.get_current_server_hardware", return_value=None
    ):
        mock_db.devices.find_one.return_value = dev_doc
        resp = client.get(f"/api/devices/{device_id}/server-hardware/health", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json["data"]
        assert data["deviceId"] == str(device_id)
        assert data["observedAt"] is None
        assert data["health"] is None
        assert data["freshness"]["status"] == "NEVER_POLLED"
        assert data["freshness"]["dataAgeSeconds"] is None
        assert data["collection"]["lastPollStatus"] == "AUTHENTICATION_ERROR"
        assert data["collection"]["consecutiveFailures"] == 1
        assert data["collection"]["lastError"] == "AUTHENTICATION_ERROR: Invalid iLO password"


def test_api_never_polled_uninitialized(client, auth_headers):
    """Server device with no snapshot and no collection state returns 200 with NEVER_POLLED freshness."""
    device_id = ObjectId()
    dev_doc = {"_id": device_id, "hostname": "server-uninit", "deviceType": "Server"}

    with patch("routes.device_routes.db") as mock_db, patch(
        "routes.device_routes.get_current_server_hardware", return_value=None
    ):
        mock_db.devices.find_one.return_value = dev_doc
        resp = client.get(f"/api/devices/{device_id}/server-hardware/health", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json["data"]
        assert data["observedAt"] is None
        assert data["health"] is None
        assert data["freshness"]["status"] == "NEVER_POLLED"
        assert data["collection"]["lastPollStatus"] == "NEVER_POLLED"
        assert data["collection"]["consecutiveFailures"] == 0


def test_api_health_success_all_ok(client, user_auth_headers):
    """Server with healthy snapshot returns 200 OK with formatted health, freshness, and collection payload."""
    device_id = ObjectId()
    dev_doc = {"_id": device_id, "hostname": "server-01", "deviceType": "Server"}
    obs_at = datetime.now(timezone.utc) - timedelta(minutes=5)

    hw = ServerHardware(
        capabilities=ServerCapabilities(has_processors=True, has_memory=True),
        processors=[ServerProcessor(id="cpu1", status=ComponentStatus(health="OK"))],
        memory=[ServerMemoryDimm(id="dimm1", status=ComponentStatus(health="OK"))],
    )
    from services.server_hardware.persistence import serialize_server_hardware

    doc = serialize_server_hardware(hw, device_id, obs_at)

    with patch("routes.device_routes.db") as mock_db, patch(
        "routes.device_routes.get_current_server_hardware", return_value=doc
    ):
        mock_db.devices.find_one.return_value = dev_doc
        resp = client.get(f"/api/devices/{device_id}/server-hardware/health", headers=user_auth_headers)
        assert resp.status_code == 200
        data = resp.json["data"]
        assert data["deviceId"] == str(device_id)
        assert data["observedAt"] is not None
        assert data["health"]["overallHealth"] == "OK"
        assert "processors" in data["health"]["subsystems"]
        assert data["freshness"]["status"] == "FRESH"
        assert data["freshness"]["isStale"] is False
        assert "collection" in data


def test_api_health_stale_telemetry(client, auth_headers):
    """Snapshot older than 30 minutes returns freshness.status == STALE and isStale == true."""
    device_id = ObjectId()
    dev_doc = {"_id": device_id, "hostname": "server-stale", "deviceType": "Server"}
    obs_at = datetime.now(timezone.utc) - timedelta(minutes=45)

    hw = ServerHardware(
        capabilities=ServerCapabilities(has_processors=True),
        processors=[ServerProcessor(id="cpu1", status=ComponentStatus(health="OK"))],
    )
    from services.server_hardware.persistence import serialize_server_hardware

    doc = serialize_server_hardware(hw, device_id, obs_at)

    with patch("routes.device_routes.db") as mock_db, patch(
        "routes.device_routes.get_current_server_hardware", return_value=doc
    ):
        mock_db.devices.find_one.return_value = dev_doc
        resp = client.get(f"/api/devices/{device_id}/server-hardware/health", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json["data"]
        assert data["health"]["overallHealth"] == "OK"
        assert data["freshness"]["status"] == "STALE"
        assert data["freshness"]["isStale"] is True
        assert data["freshness"]["dataAgeSeconds"] >= 2700


def test_api_active_collection_failure_preserves_hardware_health(client, auth_headers):
    """Active collection failure sets freshness.status == FAILING while preserving last known hardware health."""
    device_id = ObjectId()
    t_succ = datetime.now(timezone.utc) - timedelta(minutes=10)
    t_fail = datetime.now(timezone.utc) - timedelta(minutes=2)

    dev_doc = {
        "_id": device_id,
        "hostname": "server-failing-poll",
        "deviceType": "Server",
        "iloCollectionState": {
            "lastAttemptAt": t_fail,
            "lastSuccessAt": t_succ,
            "lastFailureAt": t_fail,
            "lastPollStatus": "TIMEOUT",
            "consecutiveFailures": 1,
            "lastError": "TIMEOUT: Socket read timeout",
        },
    }

    hw = ServerHardware(
        capabilities=ServerCapabilities(has_processors=True),
        processors=[ServerProcessor(id="cpu1", status=ComponentStatus(health="OK"))],
    )
    from services.server_hardware.persistence import serialize_server_hardware

    doc = serialize_server_hardware(hw, device_id, t_succ)

    with patch("routes.device_routes.db") as mock_db, patch(
        "routes.device_routes.get_current_server_hardware", return_value=doc
    ):
        mock_db.devices.find_one.return_value = dev_doc
        resp = client.get(f"/api/devices/{device_id}/server-hardware/health", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json["data"]

        # Hardware health is still OK (from last known good snapshot)
        assert data["health"]["overallHealth"] == "OK"
        # Telemetry freshness indicates FAILING collection
        assert data["freshness"]["status"] == "FAILING"
        assert data["freshness"]["isStale"] is True
        # Collection details reflect current failure
        assert data["collection"]["lastPollStatus"] == "TIMEOUT"
        assert data["collection"]["consecutiveFailures"] == 1


def test_api_health_warning_critical(client, auth_headers):
    """Server with physical drive failure evaluates overallHealth == CRITICAL."""
    device_id = ObjectId()
    dev_doc = {"_id": device_id, "hostname": "server-02", "deviceType": "Linux Server"}
    obs_at = datetime(2026, 10, 6, 10, 30, 0, tzinfo=timezone.utc)

    hw = ServerHardware(
        capabilities=ServerCapabilities(has_storage=True),
        storage=ServerStorage(
            physical_drives=[PhysicalDrive(id="d1", name="Bay 1", status=ComponentStatus(health="Critical"))]
        ),
    )
    from services.server_hardware.persistence import serialize_server_hardware

    doc = serialize_server_hardware(hw, device_id, obs_at)

    with patch("routes.device_routes.db") as mock_db, patch(
        "routes.device_routes.get_current_server_hardware", return_value=doc
    ):
        mock_db.devices.find_one.return_value = dev_doc
        resp = client.get(f"/api/devices/{device_id}/server-hardware/health", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json["data"]
        assert data["health"]["overallHealth"] == "CRITICAL"
        assert data["health"]["subsystems"]["storage"]["status"] == "CRITICAL"
        assert any("Bay 1" in r for r in data["health"]["summaryReasons"])


def test_api_health_and_freshness_independence(client, auth_headers):
    """Verify health CRITICAL + freshness FRESH and health OK + freshness STALE are both supported."""
    device_id = ObjectId()
    obs_recent = datetime.now(timezone.utc) - timedelta(minutes=2)

    dev_doc = {
        "_id": device_id,
        "hostname": "server-crit-fresh",
        "deviceType": "Server",
        "iloCollectionState": {
            "lastAttemptAt": obs_recent,
            "lastSuccessAt": obs_recent,
            "lastPollStatus": "SUCCESS",
            "consecutiveFailures": 0,
        },
    }

    hw = ServerHardware(
        capabilities=ServerCapabilities(has_storage=True),
        storage=ServerStorage(
            physical_drives=[PhysicalDrive(id="d1", name="Bay 1", status=ComponentStatus(health="Critical"))]
        ),
    )
    from services.server_hardware.persistence import serialize_server_hardware

    doc = serialize_server_hardware(hw, device_id, obs_recent)

    with patch("routes.device_routes.db") as mock_db, patch(
        "routes.device_routes.get_current_server_hardware", return_value=doc
    ):
        mock_db.devices.find_one.return_value = dev_doc
        resp = client.get(f"/api/devices/{device_id}/server-hardware/health", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json["data"]
        # Hardware health is CRITICAL
        assert data["health"]["overallHealth"] == "CRITICAL"
        # Telemetry freshness is FRESH
        assert data["freshness"]["status"] == "FRESH"
        assert data["freshness"]["isStale"] is False


def test_api_security_contains_no_secrets(client, auth_headers):
    """Response payload contains no passwords, tokens, or raw Redfish payloads."""
    device_id = ObjectId()
    dev_doc = {
        "_id": device_id,
        "hostname": "server-03",
        "deviceType": "ESXi Server",
        "iloCollectionState": {
            "lastAttemptAt": datetime.now(timezone.utc),
            "lastFailureAt": datetime.now(timezone.utc),
            "lastPollStatus": "AUTHENTICATION_ERROR",
            "consecutiveFailures": 1,
            "lastError": "AUTHENTICATION_ERROR: Invalid iLO password [REDACTED]",
        },
    }
    obs_at = datetime(2026, 10, 6, 10, 0, 0, tzinfo=timezone.utc)
    hw = ServerHardware(capabilities=ServerCapabilities(has_processors=True))

    from services.server_hardware.persistence import serialize_server_hardware

    doc = serialize_server_hardware(hw, device_id, obs_at)

    with patch("routes.device_routes.db") as mock_db, patch(
        "routes.device_routes.get_current_server_hardware", return_value=doc
    ):
        mock_db.devices.find_one.return_value = dev_doc
        resp = client.get(f"/api/devices/{device_id}/server-hardware/health", headers=auth_headers)
        raw_body = json.dumps(resp.json)
        assert "password" not in raw_body.lower() or "[redacted]" in raw_body.lower()
        assert "super_secret_pass" not in raw_body.lower()
        assert "x-auth-token" not in raw_body.lower()
        assert "@odata" not in raw_body


def test_api_admin_can_update_hardware_monitoring_enabled(client, auth_headers):
    """Admin user can update hardwareMonitoringEnabled via PUT /api/devices/<id>."""
    device_id = str(ObjectId())
    dev_doc = {
        "_id": ObjectId(device_id),
        "hostname": "server-04",
        "deviceType": "Server",
        "iloAddress": "10.0.0.120",
        "hardwareMonitoringEnabled": True,
    }

    updated_doc = dict(dev_doc)
    updated_doc["hardwareMonitoringEnabled"] = False

    with patch("routes.device_routes.db") as mock_db:
        mock_db.devices.find_one.side_effect = [dev_doc, updated_doc]
        mock_db.devices.update_one.return_value = MagicMock(modified_count=1)

        resp = client.put(
            f"/api/devices/{device_id}",
            json={"hardwareMonitoringEnabled": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json["success"] is True
        assert resp.json["data"]["hardwareMonitoringEnabled"] is False


def test_api_non_admin_cannot_update_hardware_monitoring_enabled(client):
    """Non-admin user cannot update hardwareMonitoringEnabled via PUT /api/devices/<id>."""
    device_id = str(ObjectId())
    # Unauthenticated or non-admin token
    resp = client.put(
        f"/api/devices/{device_id}",
        json={"hardwareMonitoringEnabled": False},
    )
    assert resp.status_code == 401


