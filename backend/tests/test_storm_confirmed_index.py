"""Unit tests for Storm Confirmed unique alert index bootstrap."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from pymongo.errors import OperationFailure


class ActiveStormConfirmedIndexTests(unittest.TestCase):
    @patch("services.monitor_indexes.db")
    def test_ensures_unique_partial_index(self, mock_db):
        from services.monitor_indexes import (
            ACTIVE_STORM_CONFIRMED_INDEX,
            ensure_monitoring_idempotency_indexes,
        )

        mock_db.pingHistory.create_index.return_value = "ok"
        mock_db.alerts.create_index.return_value = "ok"

        ensure_monitoring_idempotency_indexes()

        storm_calls = [
            call
            for call in mock_db.alerts.create_index.call_args_list
            if call.kwargs.get("name") == ACTIVE_STORM_CONFIRMED_INDEX
            or (call.args and call.args[0] and "interface" in str(call.args[0]))
        ]
        self.assertTrue(storm_calls)
        call = storm_calls[-1]
        self.assertEqual(call.kwargs.get("name"), ACTIVE_STORM_CONFIRMED_INDEX)
        self.assertTrue(call.kwargs.get("unique"))
        partial = call.kwargs.get("partialFilterExpression") or {}
        self.assertEqual(partial.get("title"), "Storm Confirmed")
        self.assertIs(partial.get("resolved"), False)
        self.assertIs(partial.get("dismissed"), False)

    @patch("services.monitor_indexes._log_active_storm_confirmed_duplicates")
    @patch("services.monitor_indexes.db")
    def test_operation_failure_reports_duplicates(self, mock_db, mock_log_dupes):
        from services.monitor_indexes import ensure_monitoring_idempotency_indexes

        mock_db.pingHistory.create_index.return_value = "ok"

        def _create_index(*args, **kwargs):
            if kwargs.get("name") == "uniq_alerts_active_storm_confirmed":
                raise OperationFailure("E11000 duplicate key")
            return "ok"

        mock_db.alerts.create_index.side_effect = _create_index

        ensure_monitoring_idempotency_indexes()
        mock_log_dupes.assert_called_once()


if __name__ == "__main__":
    unittest.main()
