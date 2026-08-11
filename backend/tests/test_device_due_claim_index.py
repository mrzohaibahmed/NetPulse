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


class TestDeviceDueClaimIndex(unittest.TestCase):
    def test_partial_due_claim_index_requested(self):
        mock_devices = MagicMock()
        mock_devices.create_index.side_effect = [
            "uniq_devices_ipAddress",
            DUE_CLAIM_INDEX_NAME,
        ]

        with patch("services.device_indexes.db") as mock_db:
            mock_db.devices = mock_devices
            ensure_device_indexes()

        due_call = mock_devices.create_index.call_args_list[1]
        self.assertEqual(
            due_call.args[0],
            [("nextCheckAt", 1), ("scanClaimExpiresAt", 1)],
        )
        self.assertEqual(due_call.kwargs["name"], DUE_CLAIM_INDEX_NAME)
        self.assertEqual(due_call.kwargs["partialFilterExpression"], {"monitor": True})
        self.assertNotIn("unique", due_call.kwargs)
        self.assertNotIn("expireAfterSeconds", due_call.kwargs)

    def test_partial_failure_falls_back_to_compound(self):
        mock_devices = MagicMock()

        def _create_index(keys, **kwargs):
            if kwargs.get("name") == DUE_CLAIM_INDEX_NAME and "partialFilterExpression" in kwargs:
                raise OperationFailure("partial indexes unsupported")
            return kwargs.get("name") or "ok"

        mock_devices.create_index.side_effect = _create_index

        with patch("services.device_indexes.db") as mock_db:
            mock_db.devices = mock_devices
            ensure_device_indexes()

        # ip unique + failed partial + fallback compound
        self.assertEqual(mock_devices.create_index.call_count, 3)
        fallback = mock_devices.create_index.call_args_list[2]
        self.assertEqual(
            fallback.args[0],
            [("monitor", 1), ("nextCheckAt", 1), ("scanClaimExpiresAt", 1)],
        )
        self.assertEqual(fallback.kwargs["name"], DUE_CLAIM_INDEX_NAME)
        self.assertNotIn("partialFilterExpression", fallback.kwargs)
        self.assertNotIn("expireAfterSeconds", fallback.kwargs)

    def test_ensure_is_idempotent(self):
        mock_devices = MagicMock()
        mock_devices.create_index.return_value = "ok"

        with patch("services.device_indexes.db") as mock_db:
            mock_db.devices = mock_devices
            ensure_device_indexes()
            ensure_device_indexes()

        self.assertEqual(mock_devices.create_index.call_count, 4)


if __name__ == "__main__":
    unittest.main()
