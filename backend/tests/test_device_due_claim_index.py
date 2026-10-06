"""Phase 2: devices due/claim index for dispatch monitoring."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pymongo.errors import OperationFailure

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.device_indexes import DUE_CLAIM_INDEX_NAME, ensure_device_indexes


def _index_state(initial=None):
    state = {
        "indexes": initial
        or {
            "_id_": {"name": "_id_", "key": {"_id": 1}},
        }
    }

    mock_devices = MagicMock()

    def list_indexes():
        return list(state["indexes"].values())

    def create_index(keys, **kwargs):
        name = kwargs["name"]
        if name == DUE_CLAIM_INDEX_NAME and "partialFilterExpression" in kwargs:
            # Allow tests to inject failure via side_effect wrapper.
            pass
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
    return state, mock_devices


class TestDeviceDueClaimIndex(unittest.TestCase):
    def test_partial_due_claim_index_requested(self):
        state, mock_devices = _index_state()

        with patch("services.device_indexes.db") as mock_db:
            mock_db.devices = mock_devices
            ensure_device_indexes()

        due = state["indexes"][DUE_CLAIM_INDEX_NAME]
        self.assertEqual(due["key"], {"nextCheckAt": 1, "scanClaimExpiresAt": 1})
        self.assertEqual(due["partialFilterExpression"], {"monitor": True})
        self.assertFalse(due.get("unique"))

        ip = state["indexes"]["uniq_devices_ipAddress"]
        self.assertTrue(ip["unique"])
        self.assertEqual(
            ip["partialFilterExpression"],
            {"ipAddress": {"$type": "string", "$gt": ""}},
        )

    def test_partial_failure_falls_back_to_compound(self):
        state, mock_devices = _index_state()
        original_create = mock_devices.create_index.side_effect

        def _create_index(keys, **kwargs):
            if kwargs.get("name") == DUE_CLAIM_INDEX_NAME and "partialFilterExpression" in kwargs:
                raise OperationFailure("partial indexes unsupported")
            return original_create(keys, **kwargs)

        mock_devices.create_index.side_effect = _create_index

        with patch("services.device_indexes.db") as mock_db:
            mock_db.devices = mock_devices
            ensure_device_indexes()

        due = state["indexes"][DUE_CLAIM_INDEX_NAME]
        self.assertEqual(
            due["key"],
            {"monitor": 1, "nextCheckAt": 1, "scanClaimExpiresAt": 1},
        )
        self.assertNotIn("partialFilterExpression", due)

    def test_ensure_is_idempotent(self):
        state, mock_devices = _index_state()

        with patch("services.device_indexes.db") as mock_db:
            mock_db.devices = mock_devices
            ensure_device_indexes()
            ensure_device_indexes()

        self.assertIn("uniq_devices_ipAddress", state["indexes"])
        self.assertIn("uniq_devices_iloAddress", state["indexes"])
        self.assertIn(DUE_CLAIM_INDEX_NAME, state["indexes"])


if __name__ == "__main__":
    unittest.main()
