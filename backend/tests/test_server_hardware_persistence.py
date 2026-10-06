"""
Unit and Integration Tests for Phase 3C Server Hardware Persistence.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bson
import pytest
from bson import ObjectId

from services.server_hardware import (
    CoolingFan,
    LogicalVolume,
    PhysicalDrive,
    ServerHardware,
    ServerHardwarePersistenceError,
    ServerMemoryDimm,
    ServerNetworkInterface,
    ServerProcessor,
    StorageController,
    TemperatureSensor,
    append_server_hardware_history,
    ensure_server_hardware_indexes,
    get_current_server_hardware,
    get_server_hardware_history,
    normalize_server_hardware,
    save_current_server_hardware,
    serialize_server_hardware,
)
from services.server_hardware.persistence import _normalize_device_id

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "redfish"


@pytest.fixture
def ilo4_root():
    return json.loads((FIXTURES_DIR / "ilo4_dl380.json").read_text(encoding="utf-8"))


@pytest.fixture
def ilo5_root():
    return json.loads((FIXTURES_DIR / "ilo5_dl380.json").read_text(encoding="utf-8"))


@pytest.fixture
def ilo6_root():
    return json.loads((FIXTURES_DIR / "ilo6_dl380.json").read_text(encoding="utf-8"))


# Mock PyMongo Database for testing persistence without touching production MongoDB
class MockMongoCollection:
    def __init__(self):
        self.docs = []
        self.indexes = {}
        self.validator = None

    def create_index(self, keys, name=None, unique=False, expireAfterSeconds=None, **kwargs):
        index_name = name or f"idx_{len(self.indexes)}"
        self.indexes[index_name] = {
            "keys": keys,
            "unique": unique,
            "expireAfterSeconds": expireAfterSeconds,
        }
        return index_name

    def insert_one(self, doc):
        doc_copy = dict(doc)
        if "_id" not in doc_copy:
            doc_copy["_id"] = ObjectId()

        # Check unique index constraint
        for idx_name, idx_spec in self.indexes.items():
            if idx_spec.get("unique"):
                keys = idx_spec["keys"]
                for existing in self.docs:
                    matches = True
                    for k, _ in keys:
                        if existing.get(k) != doc_copy.get(k):
                            matches = False
                            break
                    if matches:
                        from pymongo.errors import DuplicateKeyError
                        raise DuplicateKeyError(f"E11000 duplicate key error on index {idx_name}")

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

    def update_one(self, filter_dict, update_dict):
        matched_idx = None
        for i, existing in enumerate(self.docs):
            if self._matches(existing, filter_dict):
                matched_idx = i
                break

        if matched_idx is not None:
            if "$set" in update_dict:
                for k, v in update_dict["$set"].items():
                    self.docs[matched_idx][k] = v
            return type("Result", (), {"matched_count": 1, "modified_count": 1})()

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
                if "$lte" in v:
                    if doc_val is None or doc_val > v["$lte"]:
                        return False
                if "$lt" in v:
                    if doc_val is None or doc_val >= v["$lt"]:
                        return False
                if "$exists" in v:
                    exists = k in doc
                    if exists != v["$exists"]:
                        return False
            else:
                if doc.get(k) != v:
                    return False
        return True


class MockMongoDatabase:
    def __init__(self):
        self.server_hardware_current = MockMongoCollection()
        self.server_hardware_history = MockMongoCollection()

    def command(self, cmd, coll, **kwargs):
        if cmd == "collMod":
            getattr(self, coll).validator = kwargs.get("validator")
        return {"ok": 1}


@pytest.fixture
def mock_db():
    mdb = MockMongoDatabase()
    ensure_server_hardware_indexes(database=mdb)
    return mdb


# ---------------------------------------------------------------------------
# 1. Device ID Normalization & Input Validation Tests
# ---------------------------------------------------------------------------

def test_device_id_normalization():
    """Verify ObjectId and 24-char hex string inputs normalize to ObjectId."""
    oid = ObjectId()
    assert _normalize_device_id(oid) == oid

    hex_str = "507f1f77bcf86cd799439011"
    assert _normalize_device_id(hex_str) == ObjectId(hex_str)

    with pytest.raises(ServerHardwarePersistenceError):
        _normalize_device_id("invalid-hex-string")

    with pytest.raises(ServerHardwarePersistenceError):
        _normalize_device_id(12345)


# ---------------------------------------------------------------------------
# 2. Current Hardware Snapshot Persistence & Replacement Tests
# ---------------------------------------------------------------------------

def test_save_current_first_observation(mock_db, ilo5_root):
    """Initial observation inserts a new document in server_hardware_current."""
    dev_id = ObjectId()
    hw = normalize_server_hardware(service_root_data=ilo5_root)
    obs_at = datetime.now(timezone.utc)

    saved = save_current_server_hardware(dev_id, hw, obs_at, database=mock_db)
    assert saved is True

    fetched = get_current_server_hardware(dev_id, database=mock_db)
    assert fetched is not None
    assert fetched["deviceId"] == dev_id
    assert fetched["firmware"]["iloGeneration"] == "iLO 5"
    assert "updatedAt" in fetched


def test_save_current_newer_observation_replaces(mock_db, ilo5_root):
    """Newer observation replaces existing current document."""
    dev_id = ObjectId()
    obs1 = datetime.now(timezone.utc)
    hw1 = normalize_server_hardware(service_root_data=ilo5_root)

    save_current_server_hardware(dev_id, hw1, obs1, database=mock_db)

    obs2 = obs1 + timedelta(minutes=5)
    hw2 = normalize_server_hardware(
        service_root_data=ilo5_root,
        system_data={"HostName": "updated-hostname"},
    )

    saved = save_current_server_hardware(dev_id, hw2, obs2, database=mock_db)
    assert saved is True

    fetched = get_current_server_hardware(dev_id, database=mock_db)
    assert fetched["observedAt"] == obs2
    assert fetched["identity"]["hostname"] == "updated-hostname"


def test_save_current_older_observation_rejected(mock_db, ilo5_root):
    """Older observation does NOT overwrite newer current document."""
    dev_id = ObjectId()
    obs_new = datetime.now(timezone.utc)
    hw_new = normalize_server_hardware(system_data={"HostName": "new-hostname"})

    save_current_server_hardware(dev_id, hw_new, obs_new, database=mock_db)

    obs_old = obs_new - timedelta(minutes=10)
    hw_old = normalize_server_hardware(system_data={"HostName": "old-hostname"})

    saved = save_current_server_hardware(dev_id, hw_old, obs_old, database=mock_db)
    assert saved is False

    fetched = get_current_server_hardware(dev_id, database=mock_db)
    assert fetched["identity"]["hostname"] == "new-hostname"
    assert fetched["observedAt"] == obs_new


def test_stale_data_removal_on_full_document_replacement(mock_db):
    """
    MANDATORY STALE-DATA TEST: Full replacement removes stale array elements completely.
    (e.g., 4 DIMMs down to 2 DIMMs, 4 disks down to 2 disks).
    """
    dev_id = ObjectId()
    obs1 = datetime.now(timezone.utc)

    # Old snapshot with 4 DIMMs and 4 Disks
    hw_old = ServerHardware(
        memory=[
            ServerMemoryDimm(id="dimm1"), ServerMemoryDimm(id="dimm2"),
            ServerMemoryDimm(id="dimm3"), ServerMemoryDimm(id="dimm4")
        ],
        processors=[ServerProcessor(id="cpu1"), ServerProcessor(id="cpu2")]
    )
    save_current_server_hardware(dev_id, hw_old, obs1, database=mock_db)

    # New snapshot with 2 DIMMs and 1 CPU
    obs2 = obs1 + timedelta(minutes=1)
    hw_new = ServerHardware(
        memory=[ServerMemoryDimm(id="dimm1"), ServerMemoryDimm(id="dimm2")],
        processors=[ServerProcessor(id="cpu1")]
    )
    save_current_server_hardware(dev_id, hw_new, obs2, database=mock_db)

    fetched = get_current_server_hardware(dev_id, database=mock_db)
    assert len(fetched["memory"]) == 2
    assert [m["id"] for m in fetched["memory"]] == ["dimm1", "dimm2"]
    assert len(fetched["processors"]) == 1
    assert [p["id"] for p in fetched["processors"]] == ["cpu1"]


# ---------------------------------------------------------------------------
# 3. History Append & Idempotency Tests
# ---------------------------------------------------------------------------

def test_append_history_and_idempotency(mock_db, ilo4_root):
    """Verify history appends cleanly and compound index rejects duplicate (deviceId, observedAt)."""
    dev_id = ObjectId()
    obs_at = datetime.now(timezone.utc)
    hw = normalize_server_hardware(service_root_data=ilo4_root)

    appended = append_server_hardware_history(dev_id, hw, obs_at, database=mock_db)
    assert appended is True

    # Duplicate write with exact same (deviceId, observedAt)
    appended_dup = append_server_hardware_history(dev_id, hw, obs_at, database=mock_db)
    assert appended_dup is False

    history = get_server_hardware_history(dev_id, database=mock_db)
    assert len(history) == 1
    assert "updatedAt" not in history[0]  # History omits updatedAt


def test_history_older_unrecorded_observation_allowed(mock_db):
    """Older unrecorded observation can still be appended to history."""
    dev_id = ObjectId()
    t_new = datetime.now(timezone.utc)
    t_old = t_new - timedelta(hours=1)

    hw = normalize_server_hardware()
    append_server_hardware_history(dev_id, hw, t_new, database=mock_db)
    append_server_hardware_history(dev_id, hw, t_old, database=mock_db)

    history = get_server_hardware_history(dev_id, database=mock_db)
    assert len(history) == 2
    assert history[0]["observedAt"] == t_new
    assert history[1]["observedAt"] == t_old


# ---------------------------------------------------------------------------
# 4. Security & Data Exclusion Tests
# ---------------------------------------------------------------------------

def test_serialized_document_security(ilo5_root):
    """Assert serialized documents exclude passwords, tokens, credentials, and raw Redfish JSON."""
    dev_id = ObjectId()
    obs_at = datetime.now(timezone.utc)
    hw = normalize_server_hardware(service_root_data=ilo5_root)

    doc = serialize_server_hardware(hw, dev_id, obs_at)

    raw_json_str = json.dumps(doc, default=str)
    assert "password" not in raw_json_str.lower()
    assert "x-auth-token" not in raw_json_str.lower()
    assert "authorization" not in raw_json_str.lower()
    assert "@odata" not in raw_json_str


def test_serialized_document_size_safety(ilo4_root, ilo5_root, ilo6_root):
    """Assert BSON document size remains safely under 25 KB target threshold."""
    dev_id = ObjectId()
    obs_at = datetime.now(timezone.utc)

    for root in (ilo4_root, ilo5_root, ilo6_root):
        hw = normalize_server_hardware(service_root_data=root)
        doc = serialize_server_hardware(hw, dev_id, obs_at)
        bson_bytes = bson.encode(doc)
        assert len(bson_bytes) < 25000, f"BSON size {len(bson_bytes)} exceeds 25KB threshold"


# ---------------------------------------------------------------------------
# 5. Index & Validator Schema Checks
# ---------------------------------------------------------------------------

def test_index_definitions(mock_db):
    """Verify current and history index definitions."""
    curr_idx = mock_db.server_hardware_current.indexes
    hist_idx = mock_db.server_hardware_history.indexes

    assert "uniq_server_hardware_current_deviceId" in curr_idx
    assert curr_idx["uniq_server_hardware_current_deviceId"]["unique"] is True

    assert "idx_server_hardware_history_device_obs" in hist_idx
    assert "uniq_server_hardware_history_device_obs" in hist_idx
    assert hist_idx["uniq_server_hardware_history_device_obs"]["unique"] is True

    assert "ttl_server_hardware_history_observedAt" in hist_idx
    assert hist_idx["ttl_server_hardware_history_observedAt"]["expireAfterSeconds"] == 2592000


def test_race_path_existing_document_retry(mock_db):
    """Verify that when find_one finds an existing doc with observedAt <= incoming, replace_one retries."""
    dev_id = ObjectId()
    obs1 = datetime.now(timezone.utc)
    hw1 = normalize_server_hardware(system_data={"HostName": "host-v1"})

    # Manually insert doc with obs1
    doc = serialize_server_hardware(hw1, dev_id, obs1)
    doc["_id"] = ObjectId()
    doc["updatedAt"] = obs1
    mock_db.server_hardware_current.docs.append(doc)

    obs2 = obs1 + timedelta(seconds=10)
    hw2 = normalize_server_hardware(system_data={"HostName": "host-v2"})

    # Calling save_current_server_hardware should detect existing doc and replace it
    saved = save_current_server_hardware(dev_id, hw2, obs2, database=mock_db)
    assert saved is True

    fetched = get_current_server_hardware(dev_id, database=mock_db)
    assert fetched["identity"]["hostname"] == "host-v2"
    assert fetched["observedAt"] == obs2


def test_ilo4_ilo5_ilo6_serialization_and_schema_validity(mock_db, ilo4_root, ilo5_root, ilo6_root):
    """Verify normalized snapshots for iLO 4, 5, and 6 serialize correctly and pass validation structure."""
    dev_id = ObjectId()
    obs_at = datetime.now(timezone.utc)

    for gen, root in [("iLO 4", ilo4_root), ("iLO 5", ilo5_root), ("iLO 6", ilo6_root)]:
        hw = normalize_server_hardware(service_root_data=root)
        doc = serialize_server_hardware(hw, dev_id, obs_at)

        assert doc["deviceId"] == dev_id
        assert doc["schemaVersion"] == 1
        assert doc["observedAt"] == obs_at
        assert doc["firmware"]["iloGeneration"] == gen
        assert isinstance(doc["processors"], list)
        assert isinstance(doc["memory"], list)
        assert isinstance(doc["networkInterfaces"], list)
        assert isinstance(doc["temperatures"], list)
        assert isinstance(doc["fans"], list)

        # Save to mock_db
        saved = save_current_server_hardware(dev_id, hw, obs_at, database=mock_db)
        assert saved is True

