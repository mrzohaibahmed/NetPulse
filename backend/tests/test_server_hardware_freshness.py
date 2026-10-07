"""
Unit tests for HPE iLO Telemetry Freshness Evaluation Primitives (Phase 4A Step 3).
"""

from datetime import datetime, timedelta, timezone

import pytest

from services.server_hardware.freshness import (
    DEFAULT_STALE_THRESHOLD_SECONDS,
    TelemetryFreshness,
    evaluate_telemetry_freshness,
)


class TestTelemetryFreshnessEvaluator:
    @pytest.fixture
    def base_time(self):
        return datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)

    def test_1_fresh_telemetry(self, base_time):
        """Observation age < 1800 seconds returns FRESH."""
        obs = base_time - timedelta(minutes=10)  # 600s age
        res = evaluate_telemetry_freshness(obs, now=base_time)
        assert res.status == "FRESH"
        assert res.is_stale is False
        assert res.data_age_seconds == 600
        assert res.stale_threshold_seconds == 1800

    def test_2_boundary_exact_threshold(self, base_time):
        """Observation age == 1800 seconds returns FRESH (<= threshold)."""
        obs = base_time - timedelta(seconds=1800)
        res = evaluate_telemetry_freshness(obs, now=base_time)
        assert res.status == "FRESH"
        assert res.is_stale is False
        assert res.data_age_seconds == 1800

    def test_3_stale_telemetry(self, base_time):
        """Observation age > 1800 seconds returns STALE."""
        obs = base_time - timedelta(seconds=1801)
        res = evaluate_telemetry_freshness(obs, now=base_time)
        assert res.status == "STALE"
        assert res.is_stale is True
        assert res.data_age_seconds == 1801

    def test_4_never_polled_no_observation(self, base_time):
        """No observedAt and no collection state returns NEVER_POLLED."""
        res = evaluate_telemetry_freshness(None, None, now=base_time)
        assert res.status == "NEVER_POLLED"
        assert res.is_stale is True
        assert res.data_age_seconds is None
        assert res.last_poll_status == "NEVER_POLLED"

    def test_5_first_poll_failed_returns_never_polled(self, base_time):
        """First poll failure without any prior hardware observation returns NEVER_POLLED."""
        state = {
            "lastAttemptAt": base_time - timedelta(minutes=5),
            "lastFailureAt": base_time - timedelta(minutes=5),
            "lastSuccessAt": None,
            "lastPollStatus": "AUTHENTICATION_ERROR",
            "consecutiveFailures": 1,
            "lastError": "Invalid credentials",
        }
        res = evaluate_telemetry_freshness(None, state, now=base_time)
        assert res.status == "NEVER_POLLED"
        assert res.is_stale is True
        assert res.data_age_seconds is None
        assert res.last_poll_status == "AUTHENTICATION_ERROR"

    def test_6_recent_active_failure_returns_failing(self, base_time):
        """Existing successful snapshot plus newer failed attempt returns FAILING."""
        t_succ = base_time - timedelta(minutes=10)
        t_fail = base_time - timedelta(minutes=5)
        state = {
            "lastSuccessAt": t_succ,
            "lastFailureAt": t_fail,
            "lastAttemptAt": t_fail,
            "lastPollStatus": "TIMEOUT",
            "consecutiveFailures": 1,
            "lastError": "Read timeout",
        }
        res = evaluate_telemetry_freshness(t_succ, state, now=base_time)
        assert res.status == "FAILING"
        assert res.is_stale is True
        assert res.data_age_seconds == 600

    def test_7_failure_followed_by_newer_success(self, base_time):
        """Older failure followed by newer successful poll returns FRESH."""
        t_fail = base_time - timedelta(minutes=15)
        t_succ = base_time - timedelta(minutes=5)
        state = {
            "lastSuccessAt": t_succ,
            "lastFailureAt": t_fail,
            "lastAttemptAt": t_succ,
            "lastPollStatus": "SUCCESS",
            "consecutiveFailures": 0,
            "lastError": None,
        }
        res = evaluate_telemetry_freshness(t_succ, state, now=base_time)
        assert res.status == "FRESH"
        assert res.is_stale is False
        assert res.data_age_seconds == 300

    def test_8_failure_with_stale_telemetry(self, base_time):
        """Old successful observation plus current collection failure returns FAILING (precedence over STALE)."""
        t_succ = base_time - timedelta(hours=2)
        t_fail = base_time - timedelta(minutes=5)
        state = {
            "lastSuccessAt": t_succ,
            "lastFailureAt": t_fail,
            "lastAttemptAt": t_fail,
            "lastPollStatus": "CONNECTION_ERROR",
            "consecutiveFailures": 3,
            "lastError": "Connection refused",
        }
        res = evaluate_telemetry_freshness(t_succ, state, now=base_time)
        assert res.status == "FAILING"
        assert res.is_stale is True
        assert res.data_age_seconds == 7200

    def test_9_stale_without_active_failure(self, base_time):
        """Old successful observation and no active failure returns STALE."""
        t_succ = base_time - timedelta(hours=2)
        state = {
            "lastSuccessAt": t_succ,
            "lastFailureAt": None,
            "lastAttemptAt": t_succ,
            "lastPollStatus": "SUCCESS",
            "consecutiveFailures": 0,
        }
        res = evaluate_telemetry_freshness(t_succ, state, now=base_time)
        assert res.status == "STALE"
        assert res.is_stale is True
        assert res.data_age_seconds == 7200

    def test_10_consecutive_failures_remain_failing(self, base_time):
        """Multiple consecutive failures maintain FAILING state."""
        t_succ = base_time - timedelta(minutes=30)
        t_fail = base_time - timedelta(minutes=1)
        state = {
            "lastSuccessAt": t_succ,
            "lastFailureAt": t_fail,
            "lastAttemptAt": t_fail,
            "lastPollStatus": "TLS_ERROR",
            "consecutiveFailures": 5,
        }
        res = evaluate_telemetry_freshness(t_succ, state, now=base_time)
        assert res.status == "FAILING"
        assert res.is_stale is True

    def test_11_future_observed_at_clock_skew(self, base_time):
        """Future observedAt timestamp (clock skew) produces dataAgeSeconds == 0."""
        future_obs = base_time + timedelta(seconds=10)
        res = evaluate_telemetry_freshness(future_obs, now=base_time)
        assert res.status == "FRESH"
        assert res.is_stale is False
        assert res.data_age_seconds == 0

    def test_12_missing_collection_state_evaluates_on_age(self, base_time):
        """Valid snapshot with collection_state=None evaluates based on data age."""
        obs_fresh = base_time - timedelta(minutes=5)
        obs_stale = base_time - timedelta(hours=1)

        res_fresh = evaluate_telemetry_freshness(obs_fresh, None, now=base_time)
        assert res_fresh.status == "FRESH"
        assert res_fresh.is_stale is False

        res_stale = evaluate_telemetry_freshness(obs_stale, None, now=base_time)
        assert res_stale.status == "STALE"
        assert res_stale.is_stale is True

    def test_13_missing_optional_timestamps(self, base_time):
        """Missing optional timestamps in collection state evaluate safely."""
        obs = base_time - timedelta(minutes=5)
        state = {
            "lastPollStatus": "SUCCESS",
            "consecutiveFailures": 0,
        }
        res = evaluate_telemetry_freshness(obs, state, now=base_time)
        assert res.status == "FRESH"
        assert res.is_stale is False

    def test_14_timezone_correctness(self, base_time):
        """Handles timezone-naive or timezone-aware datetimes consistently."""
        naive_base = datetime(2026, 10, 7, 12, 0, 0)
        naive_obs = datetime(2026, 10, 7, 11, 50, 0)  # 10 mins prior

        res = evaluate_telemetry_freshness(naive_obs, now=naive_base)
        assert res.status == "FRESH"
        assert res.data_age_seconds == 600

    def test_15_custom_threshold(self, base_time):
        """Supports custom stale_threshold_seconds (e.g., 600s / 10 mins)."""
        obs = base_time - timedelta(seconds=601)
        res = evaluate_telemetry_freshness(obs, now=base_time, stale_threshold_seconds=600)
        assert res.status == "STALE"
        assert res.stale_threshold_seconds == 600

    def test_16_invalid_threshold_raises_value_error(self, base_time):
        """Zero or negative stale_threshold_seconds raises ValueError."""
        obs = base_time - timedelta(minutes=5)
        with pytest.raises(ValueError, match="stale_threshold_seconds must be a positive number"):
            evaluate_telemetry_freshness(obs, now=base_time, stale_threshold_seconds=0)

        with pytest.raises(ValueError, match="stale_threshold_seconds must be a positive number"):
            evaluate_telemetry_freshness(obs, now=base_time, stale_threshold_seconds=-10)

    def test_17_determinism(self, base_time):
        """Identical inputs and timestamp produce identical output dictionary."""
        obs = base_time - timedelta(minutes=15)
        state = {"lastPollStatus": "SUCCESS", "consecutiveFailures": 0}

        r1 = evaluate_telemetry_freshness(obs, state, now=base_time)
        r2 = evaluate_telemetry_freshness(obs, state, now=base_time)
        assert r1 == r2
        assert r1.to_dict() == r2.to_dict()

    def test_18_to_dict_format(self, base_time):
        """to_dict produces JSON-compatible dictionary with camelCase fields."""
        obs = base_time - timedelta(minutes=5)
        state = {"lastPollStatus": "SUCCESS"}
        res = evaluate_telemetry_freshness(obs, state, now=base_time)

        d = res.to_dict()
        assert d["status"] == "FRESH"
        assert d["dataAgeSeconds"] == 300
        assert d["staleThresholdSeconds"] == 1800
        assert d["isStale"] is False
        assert d["observedAt"] == "2026-10-07T11:55:00Z"
        assert d["lastPollStatus"] == "SUCCESS"
