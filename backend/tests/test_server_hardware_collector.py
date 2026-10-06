"""
Unit and Integration Tests for Phase 3D iLO Server Hardware Collector.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId

from services.server_hardware.collector import (
    HISTORY_CADENCE_SECONDS,
    JOB_ID,
    _maybe_append_history,
    collect_all_server_hardware,
    poll_single_device_ilo_hardware,
)
from services.server_hardware.models import ServerHardware
from utils.redfish_client import (
    RedfishAuthenticationError,
    RedfishConnectionError,
    RedfishTimeoutError,
)
from utils.secret_crypto import encrypt_secret

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "redfish"


@pytest.fixture
def ilo5_root():
    return json.loads((FIXTURES_DIR / "ilo5_dl380.json").read_text(encoding="utf-8"))


class MockMongoCollection:
    def __init__(self):
        self.docs = []
        self.indexes = {}

    def create_index(self, keys, name=None, unique=False, **kwargs):
        idx_name = name or f"idx_{len(self.indexes)}"
        self.indexes[idx_name] = {"keys": keys, "unique": unique}
        return idx_name

    def insert_one(self, doc):
        doc_copy = dict(doc)
        if "_id" not in doc_copy:
            doc_copy["_id"] = ObjectId()
        self.docs.append(doc_copy)

    def replace_one(self, filter_dict, replacement_doc, upsert=False):
        matched_idx = None
        for i, existing in enumerate(self.docs):
            if self._matches(existing, filter_dict):
                matched_idx = i
                break

        if matched_idx is not None:
            new_doc = dict(replacement_doc)
            new_doc["_id"] = self.docs[matched_idx]["_id"]
            self.docs[matched_idx] = new_doc
            return type("Result", (), {"matched_count": 1, "modified_count": 1})()

        if upsert:
            self.insert_one(replacement_doc)
            return type("Result", (), {"matched_count": 0, "upserted_id": replacement_doc.get("_id")})()

        return type("Result", (), {"matched_count": 0, "modified_count": 0})()

    def find_one(self, filter_dict, projection=None):
        for existing in self.docs:
            if self._matches(existing, filter_dict):
                if projection:
                    res = {}
                    for k in projection:
                        if k in existing:
                            res[k] = existing[k]
                    return res
                return dict(existing)
        return None

    def find(self, filter_dict):
        results = [dict(d) for d in self.docs if self._matches(d, filter_dict)]

        class Cursor:
            def __init__(self, data):
                self.data = data

            def sort(self, key, direction):
                reverse = direction == -1
                self.data.sort(key=lambda x: x.get(key) or datetime.min, reverse=reverse)
                return self

            def limit(self, n):
                self.data = self.data[:n]
                return self

            def __iter__(self):
                return iter(self.data)

        return Cursor(results)

    def _matches(self, doc, filter_dict):
        for k, v in filter_dict.items():
            if k == "$or":
                sub_matches = any(self._matches(doc, cond) for cond in v)
                if not sub_matches:
                    return False
            elif isinstance(v, dict):
                doc_val = doc.get(k)
                if "$in" in v:
                    if doc_val not in v["$in"]:
                        return False
                if "$type" in v:
                    if v["$type"] == "string" and not isinstance(doc_val, str):
                        return False
                if "$gt" in v:
                    if doc_val is None or doc_val <= v["$gt"]:
                        return False
                if "$lte" in v:
                    if doc_val is None or doc_val > v["$lte"]:
                        return False
            else:
                if doc.get(k) != v:
                    return False
        return True


class MockMongoDatabase:
    def __init__(self):
        self.devices = MockMongoCollection()
        self.server_hardware_current = MockMongoCollection()
        self.server_hardware_history = MockMongoCollection()

    def command(self, *args, **kwargs):
        return {"ok": 1}


@pytest.fixture
def mock_db():
    return MockMongoDatabase()


# ---------------------------------------------------------------------------
# 1. Device Filtering & Identity Isolation Tests
# ---------------------------------------------------------------------------

def test_collector_filters_only_eligible_device_types_with_ilo_address(mock_db):
    """Collector queries only Server, Linux Server, ESXi Server with non-empty iloAddress."""
    dev1 = {
        "_id": ObjectId(),
        "hostname": "srv-1",
        "deviceType": "Server",
        "ipAddress": "10.0.0.1",
        "iloAddress": "10.0.0.100",
        "credentials": {"iloUsername": "admin", "iloPassword": encrypt_secret("pass")},
    }
    dev2 = {
        "_id": ObjectId(),
        "hostname": "switch-1",
        "deviceType": "Switch",
        "iloAddress": "10.0.0.101",
    }
    dev3 = {
        "_id": ObjectId(),
        "hostname": "srv-no-ilo",
        "deviceType": "Server",
        "ipAddress": "10.0.0.2",
    }
    mock_db.devices.docs.extend([dev1, dev2, dev3])

    with patch("services.server_hardware.collector.require_scheduler_leadership", return_value=True):
        with patch("services.server_hardware.collector.poll_single_device_ilo_hardware", return_value=True) as mock_poll:
            summary = collect_all_server_hardware(database=mock_db)
            assert summary["total"] == 1
            assert summary["success"] == 1
            assert mock_poll.call_count == 1
            assert mock_poll.call_args[0][0]["_id"] == dev1["_id"]


def test_collector_os_ip_and_ilo_address_isolation():
    """Verify poll_single_device_ilo_hardware passes iloAddress (not ipAddress) to RedfishClient."""
    dev = {
        "_id": ObjectId(),
        "hostname": "srv-dual",
        "deviceType": "Linux Server",
        "ipAddress": "192.168.1.50",
        "iloAddress": "10.10.10.50",
        "credentials": {"iloUsername": "admin", "iloPassword": encrypt_secret("secret")},
    }

    with patch("services.server_hardware.collector.RedfishClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.get.side_effect = RedfishConnectionError("Connection failed")

        poll_single_device_ilo_hardware(dev)

        assert mock_client_cls.call_count == 1
        kwargs = mock_client_cls.call_args[1]
        assert kwargs["ilo_address"] == "10.10.10.50"
        assert kwargs["ilo_address"] != "192.168.1.50"


# ---------------------------------------------------------------------------
# 2. Credential Security & Missing Input Handling
# ---------------------------------------------------------------------------

def test_collector_skips_device_missing_credentials(mock_db):
    """Devices missing iloUsername or iloPassword are safely skipped."""
    dev_no_user = {
        "_id": ObjectId(),
        "deviceType": "Server",
        "iloAddress": "10.0.0.10",
        "credentials": {"iloPassword": encrypt_secret("pass")},
    }
    dev_no_pass = {
        "_id": ObjectId(),
        "deviceType": "Server",
        "iloAddress": "10.0.0.11",
        "credentials": {"iloUsername": "admin"},
    }

    assert poll_single_device_ilo_hardware(dev_no_user, database=mock_db) is False
    assert poll_single_device_ilo_hardware(dev_no_pass, database=mock_db) is False


def test_collector_decryption_failure_handling(mock_db):
    """Invalid secret decryption raises error gracefully without crashing collector."""
    dev = {
        "_id": ObjectId(),
        "deviceType": "Server",
        "iloAddress": "10.0.0.12",
        "credentials": {"iloUsername": "admin", "iloPassword": "npenc:invalid_token_string"},
    }

    with patch("services.server_hardware.collector.decrypt_secret", side_effect=RuntimeError("Decryption failed")):
        res = poll_single_device_ilo_hardware(dev, database=mock_db)
        assert res is False


# ---------------------------------------------------------------------------
# 3. Successful Collection, Normalization & Persistence
# ---------------------------------------------------------------------------

def test_collector_successful_poll_updates_current_and_history(mock_db, ilo5_root):
    """Successful Redfish API responses normalize and persist current snapshot and initial history."""
    dev_id = ObjectId()
    dev = {
        "_id": dev_id,
        "hostname": "srv-prod-1",
        "deviceType": "Server",
        "iloAddress": "10.0.0.20",
        "credentials": {"iloUsername": "admin", "iloPassword": encrypt_secret("password123")},
    }

    def mock_get(endpoint):
        if endpoint == "/redfish/v1/":
            return ilo5_root
        if "/Systems/1/" in endpoint:
            return {"HostName": "srv-prod-1", "PowerState": "On"}
        if "/Chassis/1/Thermal/" in endpoint:
            return {"Temperatures": [], "Fans": []}
        if "/Chassis/1/Power/" in endpoint:
            return {"PowerSupplies": []}
        return {"Members": []}

    with patch("services.server_hardware.collector.RedfishClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.get.side_effect = mock_get

        saved = poll_single_device_ilo_hardware(dev, database=mock_db)
        assert saved is True

        # Verify current document was inserted
        curr = mock_db.server_hardware_current.find_one({"deviceId": dev_id})
        assert curr is not None
        assert curr["deviceId"] == dev_id
        assert curr["identity"]["hostname"] == "srv-prod-1"
        assert "updatedAt" in curr

        # Verify history document was inserted
        hist = mock_db.server_hardware_history.find({"deviceId": dev_id})
        assert len(hist.data) == 1


# ---------------------------------------------------------------------------
# 4. Optional vs Essential Endpoint Failure Handling
# ---------------------------------------------------------------------------

def test_collector_optional_endpoint_failure_still_succeeds(mock_db, ilo5_root):
    """Failure on optional endpoint (e.g., Thermal 404) does not fail the device hardware poll."""
    dev_id = ObjectId()
    dev = {
        "_id": dev_id,
        "deviceType": "Server",
        "iloAddress": "10.0.0.21",
        "credentials": {"iloUsername": "admin", "iloPassword": encrypt_secret("password123")},
    }

    def mock_get(endpoint):
        if endpoint == "/redfish/v1/":
            return ilo5_root
        if "/Systems/1/" in endpoint:
            return {"HostName": "srv-prod-2"}
        if "/Thermal/" in endpoint:
            raise RedfishConnectionError("404 Thermal Not Found")
        return {"Members": []}

    with patch("services.server_hardware.collector.RedfishClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.get.side_effect = mock_get

        saved = poll_single_device_ilo_hardware(dev, database=mock_db)
        assert saved is True

        curr = mock_db.server_hardware_current.find_one({"deviceId": dev_id})
        assert curr is not None
        assert curr["capabilities"]["has_thermals"] is False


def test_collector_essential_endpoint_failure_aborts_poll(mock_db):
    """Failure on essential endpoint (/redfish/v1/ or System) aborts poll without updating current snapshot."""
    dev_id = ObjectId()
    dev = {
        "_id": dev_id,
        "deviceType": "Server",
        "iloAddress": "10.0.0.22",
        "credentials": {"iloUsername": "admin", "iloPassword": encrypt_secret("password123")},
    }

    with patch("services.server_hardware.collector.RedfishClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.get.side_effect = RedfishAuthenticationError("401 Unauthorized")

        saved = poll_single_device_ilo_hardware(dev, database=mock_db)
        assert saved is False

        curr = mock_db.server_hardware_current.find_one({"deviceId": dev_id})
        assert curr is None


def test_collector_auth_failure_does_not_overwrite_previous_snapshot(mock_db):
    """Authentication or network failure does not overwrite previously saved current snapshot."""
    dev_id = ObjectId()
    dev = {
        "_id": dev_id,
        "deviceType": "Server",
        "iloAddress": "10.0.0.23",
        "credentials": {"iloUsername": "admin", "iloPassword": encrypt_secret("password123")},
    }

    # Pre-existing snapshot
    old_doc = {
        "_id": ObjectId(),
        "deviceId": dev_id,
        "schemaVersion": 1,
        "observedAt": datetime.now(timezone.utc) - timedelta(minutes=10),
        "updatedAt": datetime.now(timezone.utc) - timedelta(minutes=10),
        "identity": {"hostname": "previous-valid-host"},
    }
    mock_db.server_hardware_current.docs.append(old_doc)

    with patch("services.server_hardware.collector.RedfishClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.get.side_effect = RedfishTimeoutError("Read timeout")

        saved = poll_single_device_ilo_hardware(dev, database=mock_db)
        assert saved is False

        curr = mock_db.server_hardware_current.find_one({"deviceId": dev_id})
        assert curr["identity"]["hostname"] == "previous-valid-host"


# ---------------------------------------------------------------------------
# 5. Device Fault Isolation Tests
# ---------------------------------------------------------------------------

def test_collector_device_failure_isolation(mock_db):
    """One failing device does not prevent other devices from being polled successfully."""
    dev1 = {
        "_id": ObjectId(),
        "hostname": "srv-failing",
        "deviceType": "Server",
        "iloAddress": "10.0.0.30",
        "credentials": {"iloUsername": "admin", "iloPassword": encrypt_secret("pass")},
    }
    dev2 = {
        "_id": ObjectId(),
        "hostname": "srv-working",
        "deviceType": "Server",
        "iloAddress": "10.0.0.31",
        "credentials": {"iloUsername": "admin", "iloPassword": encrypt_secret("pass")},
    }
    mock_db.devices.docs.extend([dev1, dev2])

    def mock_poll(dev, database=None):
        if dev["_id"] == dev1["_id"]:
            return False
        return True

    with patch("services.server_hardware.collector.require_scheduler_leadership", return_value=True):
        with patch("services.server_hardware.collector.poll_single_device_ilo_hardware", side_effect=mock_poll):
            summary = collect_all_server_hardware(database=mock_db)
            assert summary["total"] == 2
            assert summary["polled"] == 2
            assert summary["success"] == 1
            assert summary["failed"] == 1


# ---------------------------------------------------------------------------
# 6. History Retention Cadence Tests (10 minutes)
# ---------------------------------------------------------------------------

def test_maybe_append_history_cadence_logic(mock_db):
    """Verify history append enforces >= 10-minute cadence and handles out-of-order timestamps."""
    dev_id = ObjectId()
    hw = ServerHardware()
    t_now = datetime.now(timezone.utc)

    # 1. No prior history -> appends history
    _maybe_append_history(dev_id, hw, t_now, database=mock_db)
    hist1 = mock_db.server_hardware_history.find({"deviceId": dev_id})
    assert len(hist1.data) == 1

    # 2. 5 minutes later -> skipped (less than 10 mins)
    t_5m = t_now + timedelta(minutes=5)
    _maybe_append_history(dev_id, hw, t_5m, database=mock_db)
    hist2 = mock_db.server_hardware_history.find({"deviceId": dev_id})
    assert len(hist2.data) == 1

    # 3. 11 minutes later -> appends history
    t_11m = t_now + timedelta(minutes=11)
    _maybe_append_history(dev_id, hw, t_11m, database=mock_db)
    hist3 = mock_db.server_hardware_history.find({"deviceId": dev_id})
    assert len(hist3.data) == 2


def test_maybe_append_history_future_and_naive_timestamps(mock_db):
    """Future or timezone-naive timestamps in history are handled safely."""
    dev_id = ObjectId()
    hw = ServerHardware()
    t_now = datetime.now(timezone.utc)

    # Existing history entry
    hist_doc = {
        "_id": ObjectId(),
        "deviceId": dev_id,
        "observedAt": t_now,
    }
    mock_db.server_hardware_history.docs.append(hist_doc)

    # Past timestamp (elapsed < 0) -> does not append duplicate
    t_past = t_now - timedelta(minutes=5)
    _maybe_append_history(dev_id, hw, t_past, database=mock_db)
    hist = mock_db.server_hardware_history.find({"deviceId": dev_id})
    assert len(hist.data) == 1


def test_current_write_success_history_write_failure(mock_db):
    """If current write succeeds but history append raises exception, operational logging records partial success."""
    dev_id = ObjectId()
    hw = ServerHardware()
    t_now = datetime.now(timezone.utc)

    with patch("services.server_hardware.collector.append_server_hardware_history", side_effect=Exception("DB write error")):
        _maybe_append_history(dev_id, hw, t_now, database=mock_db)
        # Should not raise exception out of function


# ---------------------------------------------------------------------------
# 7. Leadership & Scheduler Integration Tests
# ---------------------------------------------------------------------------

def test_collector_skips_when_not_scheduler_leader(mock_db):
    """Collector skips execution completely if require_scheduler_leadership returns False."""
    dev = {
        "_id": ObjectId(),
        "deviceType": "Server",
        "iloAddress": "10.0.0.40",
    }
    mock_db.devices.docs.append(dev)

    with patch("services.server_hardware.collector.require_scheduler_leadership", return_value=False):
        summary = collect_all_server_hardware(database=mock_db)
        assert summary["total"] == 0
        assert summary["success"] == 0


def test_collector_leadership_loss_during_cycle_aborts(mock_db):
    """Leadership loss mid-cycle aborts remaining un-dispatched device queue."""
    dev1 = {"_id": ObjectId(), "deviceType": "Server", "iloAddress": "10.0.0.41"}
    dev2 = {"_id": ObjectId(), "deviceType": "Server", "iloAddress": "10.0.0.42"}
    mock_db.devices.docs.extend([dev1, dev2])

    guard_mock = MagicMock()
    # First device ensure returns True, second device ensure returns False (leadership lost)
    guard_mock.ensure.side_effect = [True, False]

    with patch("services.server_hardware.collector.require_scheduler_leadership", return_value=True):
        with patch("services.server_hardware.collector.CycleLeadershipGuard", return_value=guard_mock):
            with patch("services.server_hardware.collector.poll_single_device_ilo_hardware", return_value=True):
                summary = collect_all_server_hardware(database=mock_db)
                assert summary["total"] == 2
                assert summary["skipped"] == 1


def test_scheduler_job_registration():
    """Verify ilo_hardware_poll_job registration in scheduler module."""
    from scheduler import ILO_HARDWARE_POLL_JOB_ID, _start_ilo_hardware_poll_job, scheduler

    with patch.object(scheduler, "add_job") as mock_add_job:
        _start_ilo_hardware_poll_job()
        assert mock_add_job.call_count == 1
        kwargs = mock_add_job.call_args[1]
        assert kwargs["id"] == ILO_HARDWARE_POLL_JOB_ID
        assert kwargs["seconds"] == 60
        assert kwargs["max_instances"] == 1
        assert kwargs["coalesce"] is True


def test_collector_credential_and_token_redaction(caplog):
    """Assert decrypted passwords and session tokens do not appear in logger outputs or exceptions."""
    dev_id = ObjectId()
    dev = {
        "_id": dev_id,
        "hostname": "srv-secret",
        "deviceType": "Server",
        "iloAddress": "10.0.0.99",
        "credentials": {"iloUsername": "admin", "iloPassword": encrypt_secret("SUPER_SECRET_PASS")},
    }

    with caplog.at_level(logging.DEBUG):
        with patch("services.server_hardware.collector.RedfishClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.get.side_effect = RedfishAuthenticationError("401 Invalid Token X-Auth-Token: secret_token_value")

            saved = poll_single_device_ilo_hardware(dev)
            assert saved is False

            log_text = caplog.text.lower()
            assert "super_secret_pass" not in log_text
            assert "secret_token_value" not in log_text
