"""
Unit tests for HPE iLO Collection State Persistence Primitives (Phase 4A Step 1).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from pymongo.errors import PyMongoError

from services.server_hardware.collection_state import (
    CollectionStateError,
    get_ilo_collection_state,
    record_ilo_poll_attempt,
    record_ilo_poll_failure,
    record_ilo_poll_success,
    sanitize_error_message,
)


class TestCollectionStateSanitization:
    def test_sanitize_error_message_none(self):
        assert sanitize_error_message(None) is None
        assert sanitize_error_message("") is None

    def test_sanitize_error_message_redacts_credentials(self):
        raw = "Failed auth for user admin with password='MySecretPassword123' on 192.168.1.1"
        sanitized = sanitize_error_message(raw)
        assert "MySecretPassword123" not in sanitized
        assert "[REDACTED]" in sanitized

    def test_sanitize_error_message_redacts_authorization_header(self):
        raw = "HTTP 401 sending Authorization: Basic YWRtaW46c2VjcmV0"
        sanitized = sanitize_error_message(raw)
        assert "YWRtaW46c2VjcmV0" not in sanitized
        assert "Authorization: [REDACTED]" in sanitized

    def test_sanitize_error_message_redacts_x_auth_token(self):
        raw = "Redfish session rejected for X-Auth-Token: 9876543210abcdef"
        sanitized = sanitize_error_message(raw)
        assert "9876543210abcdef" not in sanitized
        assert "[REDACTED]" in sanitized

    def test_sanitize_error_message_truncates_long_errors(self):
        long_err = "Error details: " + ("x" * 600)
        sanitized = sanitize_error_message(long_err)
        assert len(sanitized) <= 500
        assert sanitized.endswith("...")


class TestCollectionStatePersistence:
    @pytest.fixture
    def mock_db(self):
        class MockMongo:
            def __init__(self):
                self.devices_data = {}

            @property
            def devices(self):
                db_self = self

                class DevicesColl:
                    def find_one(self, filter_doc, projection=None):
                        oid = filter_doc.get("_id")
                        doc = db_self.devices_data.get(str(oid))
                        if not doc:
                            return None
                        if projection and "iloCollectionState" in projection:
                            return {"_id": doc["_id"], "iloCollectionState": doc.get("iloCollectionState")}
                        return dict(doc)

                    def update_one(self, filter_doc, update_doc):
                        oid = filter_doc.get("_id")
                        doc = db_self.devices_data.get(str(oid))
                        if not doc:
                            res = MagicMock()
                            res.matched_count = 0
                            return res

                        # Check $or condition for out-of-order protection
                        existing_state = doc.get("iloCollectionState")
                        match = False
                        if not existing_state:
                            match = True
                        else:
                            last_attempt = existing_state.get("lastAttemptAt")
                            if last_attempt is None:
                                match = True
                            else:
                                or_conds = filter_doc.get("$or", [])
                                for cond in or_conds:
                                    sub_cond = cond.get("iloCollectionState.lastAttemptAt")
                                    if isinstance(sub_cond, dict) and "$lte" in sub_cond:
                                        target_lte = sub_cond["$lte"]
                                        if last_attempt <= target_lte:
                                            match = True
                                            break

                        if not match:
                            res = MagicMock()
                            res.matched_count = 0
                            return res

                        # Apply $set and $inc
                        if "iloCollectionState" not in doc:
                            doc["iloCollectionState"] = {}

                        state = doc["iloCollectionState"]

                        if "$set" in update_doc:
                            for k, v in update_doc["$set"].items():
                                field = k.replace("iloCollectionState.", "")
                                state[field] = v

                        if "$inc" in update_doc:
                            for k, v in update_doc["$inc"].items():
                                field = k.replace("iloCollectionState.", "")
                                current_val = state.get(field, 0)
                                state[field] = current_val + v

                        res = MagicMock()
                        res.matched_count = 1
                        return res

                return DevicesColl()

        return MockMongo()

    def test_missing_state_returns_never_polled(self, mock_db):
        device_id = ObjectId()
        mock_db.devices_data[str(device_id)] = {"_id": device_id, "hostname": "srv-01"}

        state = get_ilo_collection_state(device_id, database=mock_db)
        assert state["lastPollStatus"] == "NEVER_POLLED"
        assert state["consecutiveFailures"] == 0
        assert state["lastAttemptAt"] is None
        assert state["lastSuccessAt"] is None
        assert state["lastFailureAt"] is None
        assert state["lastError"] is None

    def test_record_attempt_basic(self, mock_db):
        device_id = ObjectId()
        mock_db.devices_data[str(device_id)] = {"_id": device_id, "hostname": "srv-01"}

        t1 = datetime(2026, 10, 7, 10, 0, 0, tzinfo=timezone.utc)
        success = record_ilo_poll_attempt(device_id, t1, database=mock_db)
        assert success is True

        state = get_ilo_collection_state(device_id, database=mock_db)
        assert state["lastAttemptAt"] == t1
        assert state["lastPollStatus"] == "NEVER_POLLED"

    def test_record_success_updates_state_and_resets_failures(self, mock_db):
        device_id = ObjectId()
        mock_db.devices_data[str(device_id)] = {
            "_id": device_id,
            "iloCollectionState": {
                "consecutiveFailures": 3,
                "lastPollStatus": "TIMEOUT",
                "lastError": "Previous timeout error",
            },
        }

        t1 = datetime(2026, 10, 7, 10, 5, 0, tzinfo=timezone.utc)
        t_succ = datetime(2026, 10, 7, 10, 5, 5, tzinfo=timezone.utc)

        res = record_ilo_poll_success(device_id, attempt_at=t1, success_at=t_succ, database=mock_db)
        assert res is True

        state = get_ilo_collection_state(device_id, database=mock_db)
        assert state["lastAttemptAt"] == t1
        assert state["lastSuccessAt"] == t_succ
        assert state["lastPollStatus"] == "SUCCESS"
        assert state["consecutiveFailures"] == 0
        assert state["lastError"] is None

    def test_record_failure_increments_consecutive_failures(self, mock_db):
        device_id = ObjectId()
        mock_db.devices_data[str(device_id)] = {"_id": device_id}

        t1 = datetime(2026, 10, 7, 10, 0, 0, tzinfo=timezone.utc)

        # Fail 1
        record_ilo_poll_failure(
            device_id,
            attempt_at=t1,
            error_message="HTTP 401 with password='secret'",
            status="AUTHENTICATION_ERROR",
            database=mock_db,
        )
        state1 = get_ilo_collection_state(device_id, database=mock_db)
        assert state1["consecutiveFailures"] == 1
        assert state1["lastPollStatus"] == "AUTHENTICATION_ERROR"
        assert "secret" not in state1["lastError"]
        assert "[REDACTED]" in state1["lastError"]

        # Fail 2
        t2 = t1 + timedelta(minutes=10)
        record_ilo_poll_failure(
            device_id,
            attempt_at=t2,
            error_message="Connection timed out",
            status="TIMEOUT",
            database=mock_db,
        )
        state2 = get_ilo_collection_state(device_id, database=mock_db)
        assert state2["consecutiveFailures"] == 2
        assert state2["lastPollStatus"] == "TIMEOUT"

        # Fail 3
        t3 = t2 + timedelta(minutes=10)
        record_ilo_poll_failure(
            device_id,
            attempt_at=t3,
            error_message="Connection refused",
            status="CONNECTION_ERROR",
            database=mock_db,
        )
        state3 = get_ilo_collection_state(device_id, database=mock_db)
        assert state3["consecutiveFailures"] == 3

        # Success resets consecutiveFailures to 0
        t4 = t3 + timedelta(minutes=10)
        record_ilo_poll_success(device_id, attempt_at=t4, database=mock_db)
        state4 = get_ilo_collection_state(device_id, database=mock_db)
        assert state4["consecutiveFailures"] == 0
        assert state4["lastPollStatus"] == "SUCCESS"

    def test_first_poll_failure_without_hardware_snapshot(self, mock_db):
        device_id = ObjectId()
        mock_db.devices_data[str(device_id)] = {"_id": device_id}

        t1 = datetime(2026, 10, 7, 10, 0, 0, tzinfo=timezone.utc)
        record_ilo_poll_failure(
            device_id,
            attempt_at=t1,
            error_message="TLS verification failed",
            status="TLS_ERROR",
            database=mock_db,
        )

        state = get_ilo_collection_state(device_id, database=mock_db)
        assert state["lastPollStatus"] == "TLS_ERROR"
        assert state["consecutiveFailures"] == 1
        assert state["lastSuccessAt"] is None
        assert state["lastFailureAt"] is not None

    def test_out_of_order_protection_case_a(self, mock_db):
        """Case A: Newer success (T2) followed by older failure (T1 < T2) -> Older failure ignored."""
        device_id = ObjectId()
        mock_db.devices_data[str(device_id)] = {"_id": device_id}

        t1 = datetime(2026, 10, 7, 10, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 10, 7, 10, 10, 0, tzinfo=timezone.utc)

        # Attempt B (T2) succeeds first
        record_ilo_poll_success(device_id, attempt_at=t2, database=mock_db)
        state_after_b = get_ilo_collection_state(device_id, database=mock_db)
        assert state_after_b["lastAttemptAt"] == t2
        assert state_after_b["lastPollStatus"] == "SUCCESS"

        # Delayed Attempt A (T1) finishes later with failure
        res_a = record_ilo_poll_failure(
            device_id,
            attempt_at=t1,
            error_message="Delayed failure",
            status="TIMEOUT",
            database=mock_db,
        )
        assert res_a is False

        # Verify state was NOT overwritten by older Attempt A
        state_final = get_ilo_collection_state(device_id, database=mock_db)
        assert state_final["lastAttemptAt"] == t2
        assert state_final["lastPollStatus"] == "SUCCESS"
        assert state_final["consecutiveFailures"] == 0

    def test_out_of_order_protection_case_b(self, mock_db):
        """Case B: Newer failure (T2) followed by older success (T1 < T2) -> Older success ignored."""
        device_id = ObjectId()
        mock_db.devices_data[str(device_id)] = {"_id": device_id}

        t1 = datetime(2026, 10, 7, 10, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 10, 7, 10, 10, 0, tzinfo=timezone.utc)

        # Attempt B (T2) fails first
        record_ilo_poll_failure(
            device_id,
            attempt_at=t2,
            error_message="Newer attempt failed",
            status="TIMEOUT",
            database=mock_db,
        )

        # Delayed Attempt A (T1) finishes later with success
        res_a = record_ilo_poll_success(device_id, attempt_at=t1, database=mock_db)
        assert res_a is False

        # Verify state was NOT overwritten by older Attempt A
        state_final = get_ilo_collection_state(device_id, database=mock_db)
        assert state_final["lastAttemptAt"] == t2
        assert state_final["lastPollStatus"] == "TIMEOUT"
        assert state_final["consecutiveFailures"] == 1

    def test_device_isolation(self, mock_db):
        dev1 = ObjectId()
        dev2 = ObjectId()
        mock_db.devices_data[str(dev1)] = {"_id": dev1}
        mock_db.devices_data[str(dev2)] = {"_id": dev2}

        t1 = datetime(2026, 10, 7, 10, 0, 0, tzinfo=timezone.utc)
        record_ilo_poll_success(dev1, attempt_at=t1, database=mock_db)

        state1 = get_ilo_collection_state(dev1, database=mock_db)
        state2 = get_ilo_collection_state(dev2, database=mock_db)

        assert state1["lastPollStatus"] == "SUCCESS"
        assert state2["lastPollStatus"] == "NEVER_POLLED"

    def test_device_not_found_raises(self, mock_db):
        non_existent_id = ObjectId()
        with pytest.raises(CollectionStateError, match="Device not found"):
            get_ilo_collection_state(non_existent_id, database=mock_db)

    def test_db_error_raises_collection_state_error(self, mock_db):
        device_id = ObjectId()
        failing_db = MagicMock()
        failing_db.devices.update_one.side_effect = PyMongoError("Mongo connection lost")

        t1 = datetime(2026, 10, 7, 10, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(CollectionStateError, match="Failed to record poll attempt"):
            record_ilo_poll_attempt(device_id, t1, database=failing_db)
