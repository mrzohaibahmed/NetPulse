"""
Confirmation Engine
===================
Determines whether a high-risk condition has persisted long enough to be
considered a real Layer-2 network storm.

Responsibility
--------------
Confirm persistent abnormal behaviour only — no SSH, mitigation, diagnostics,
or safety checks.

Public API
----------
    from services.storm.confirmation import evaluate
    result = evaluate(device_id, interface)

Future Safety Engine must call ``evaluate()`` without modification.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING

from services.storm.confirmation_history import (
    COLLECTION,
    count_trailing_high,
    detect_poll_failure,
    load_eligibility,
    load_latest_confirmation,
    load_latest_risk,
    load_recent_risk_scores,
    window_stats,
)
from services.storm.confirmation_rules import (
    STATE_CONFIRMED,
    STATE_NOT_CONFIRMED,
    STATE_PENDING,
    ConfirmationConfig,
    get_confirmation_config,
    state_from_consecutive,
)
from services.storm.models import ConfirmationResult, create_confirmation_document
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.confirmation")


def _db():
    from config.database import db  # noqa: PLC0415

    return db


class ConfirmationEngine:
    """
    Stateful confirmation tracker driven by Risk history (SOLID).

    - SRP: confirmation only
    - OCP: rules/config injectable
    - DIP: depends on ConfirmationConfig + history helpers
    """

    def __init__(self, config: Optional[ConfirmationConfig] = None) -> None:
        self._config = config or get_confirmation_config()

    @property
    def config(self) -> ConfirmationConfig:
        return self._config

    def evaluate(
        self,
        device_id,
        interface: str,
        *,
        eligible: Optional[bool] = None,
        current_risk: Optional[float] = None,
        risk_rows: Optional[list[dict]] = None,
        poll_failed: Optional[bool] = None,
        poll_failure_reason: Optional[str] = None,
        previous_confirmation: Optional[dict] = None,
        hostname: Optional[str] = None,
        ip_address: Optional[str] = None,
        persist: bool = False,
    ) -> ConfirmationResult:
        """
        Evaluate confirmation for one interface.

        When optional inputs are omitted they are loaded from MongoDB.
        """
        started = time.monotonic()
        now = datetime.now(timezone.utc)
        name = str(interface or "").strip()
        device_key = str(device_id) if device_id is not None else None
        required = int(self._config.required_confirmations)
        threshold = float(self._config.risk_threshold)

        if not self._config.confirmation_enabled:
            result = ConfirmationResult(
                confirmed=False,
                state=STATE_NOT_CONFIRMED,
                current_risk=0.0,
                highest_risk=0.0,
                average_risk=0.0,
                consecutive_high_samples=0,
                required_samples=required,
                reason="Confirmation disabled",
                timestamp=now,
                device_id=device_key,
                interface=name or None,
            )
            self._log(result, started)
            if persist and name:
                self._store(device_id, name, result, hostname, ip_address)
            return result

        if not name:
            result = ConfirmationResult(
                confirmed=False,
                state=STATE_NOT_CONFIRMED,
                current_risk=0.0,
                highest_risk=0.0,
                average_risk=0.0,
                consecutive_high_samples=0,
                required_samples=required,
                reason="Missing interface name",
                timestamp=now,
                device_id=device_key,
                interface=None,
                reset=True,
                reset_reason="Missing interface name",
            )
            self._log(result, started)
            return result

        # ── Gather inputs ──────────────────────────────────────────────
        if eligible is None:
            eligible = load_eligibility(device_id, name)
            if eligible is None:
                eligible = False

        latest_risk = None
        if risk_rows is None:
            risk_rows = load_recent_risk_scores(
                device_id, name, limit=max(required * 3, 12)
            )
        if current_risk is None:
            latest_risk = risk_rows[0] if risk_rows else load_latest_risk(device_id, name)
            if latest_risk is not None:
                try:
                    current_risk = float(latest_risk.get("riskScore") or 0)
                except (TypeError, ValueError):
                    current_risk = 0.0
            else:
                current_risk = 0.0
        else:
            current_risk = float(current_risk)
            if risk_rows:
                latest_risk = risk_rows[0]

        if poll_failed is None:
            poll_failed, poll_failure_reason = detect_poll_failure(
                device_id,
                name,
                stale_seconds=self._config.poll_stale_seconds,
                latest_risk=latest_risk,
            )

        if previous_confirmation is None:
            previous = load_latest_confirmation(device_id, name)
        else:
            previous = previous_confirmation

        prev_consecutive = int((previous or {}).get("consecutiveHighSamples") or 0)
        prev_state = (previous or {}).get("state") or STATE_NOT_CONFIRMED

        # ── Reset rules ────────────────────────────────────────────────
        reset_reason: Optional[str] = None

        if self._config.reset_on_poll_failure and poll_failed:
            reset_reason = poll_failure_reason or "Polling failure"
        elif self._config.reset_on_ineligible and not eligible:
            reset_reason = "Interface not eligible"
        elif self._config.reset_on_low_risk and current_risk < threshold:
            if prev_consecutive > 0 or prev_state != STATE_NOT_CONFIRMED:
                reset_reason = (
                    f"Risk {current_risk:.1f} dropped below threshold "
                    f"{threshold:.0f}"
                )
            else:
                result = ConfirmationResult(
                    confirmed=False,
                    state=STATE_NOT_CONFIRMED,
                    current_risk=round(current_risk, 2),
                    highest_risk=round(current_risk, 2),
                    average_risk=round(current_risk, 2),
                    consecutive_high_samples=0,
                    required_samples=required,
                    reason="Risk below confirmation threshold",
                    timestamp=now,
                    device_id=device_key,
                    interface=name,
                )
                self._log(result, started)
                if persist:
                    self._store(device_id, name, result, hostname, ip_address)
                return result

        if reset_reason:
            result = ConfirmationResult(
                confirmed=False,
                state=STATE_NOT_CONFIRMED,
                current_risk=round(current_risk, 2),
                highest_risk=round(current_risk, 2),
                average_risk=round(current_risk, 2),
                consecutive_high_samples=0,
                required_samples=required,
                reason=f"Confirmation reset — {reset_reason}",
                timestamp=now,
                device_id=device_key,
                interface=name,
                reset=True,
                reset_reason=reset_reason,
            )
            logger.info(
                "Confirmation Reset | %s | Reason | %s",
                name,
                reset_reason,
            )
            self._log(result, started)
            if persist:
                self._store(device_id, name, result, hostname, ip_address)
            return result

        # ── Advance streak from trailing high-risk samples ─────────────
        trailing = count_trailing_high(risk_rows or [], threshold)
        if not trailing and current_risk >= threshold:
            trailing = [current_risk]

        consecutive = min(len(trailing), required * 2)
        current, highest, average = window_stats(
            trailing[:required] or [current_risk]
        )
        state = state_from_consecutive(consecutive, required)
        confirmed = state == STATE_CONFIRMED

        if confirmed:
            reason = (
                f"Risk exceeded threshold for {required} consecutive "
                f"polling cycles."
            )
            if prev_state != STATE_CONFIRMED:
                logger.info(
                    "Storm Confirmed | %s | risk=%.1f | consecutive=%s",
                    name,
                    current,
                    consecutive,
                )
            consecutive_out = required
        elif state == STATE_PENDING:
            if prev_consecutive == 0 and consecutive > 0:
                logger.info(
                    "Confirmation Started | %s | risk=%.1f",
                    name,
                    current,
                )
            reason = "Awaiting additional confirmation samples."
            consecutive_out = consecutive
        else:
            reason = "Risk below confirmation threshold"
            consecutive_out = consecutive

        result = ConfirmationResult(
            confirmed=confirmed,
            state=state,
            current_risk=current,
            highest_risk=highest,
            average_risk=average,
            consecutive_high_samples=consecutive_out,
            required_samples=required,
            reason=reason,
            timestamp=now,
            device_id=device_key,
            interface=name,
        )

        self._log(result, started)
        if persist:
            self._store(device_id, name, result, hostname, ip_address)
        return result

    def _store(
        self,
        device_id,
        interface: str,
        result: ConfirmationResult,
        hostname: Optional[str],
        ip_address: Optional[str],
    ) -> None:
        try:
            oid = device_id
            if isinstance(oid, str) and ObjectId.is_valid(oid):
                oid = ObjectId(oid)
            document = create_confirmation_document(
                device_id=oid,
                interface=interface,
                result=result,
                hostname=hostname,
                ip_address=ip_address,
            )
            _db()[COLLECTION].insert_one(document)
        except Exception as exc:  # noqa: BLE001
            logger.error("[CONFIRM] Failed to store history: %s", exc)

    @staticmethod
    def _log(result: ConfirmationResult, started: float) -> None:
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        logger.info(
            "Confirmation | %s | state=%s | consecutive=%s/%s | risk=%.1f | %sms",
            result.interface or "unknown",
            result.state,
            result.consecutive_high_samples,
            result.required_samples,
            result.current_risk,
            elapsed_ms,
        )


_engine: Optional[ConfirmationEngine] = None


def get_confirmation_engine(
    config: Optional[ConfirmationConfig] = None,
    *,
    force_new: bool = False,
) -> ConfirmationEngine:
    global _engine
    if force_new or _engine is None or config is not None:
        _engine = ConfirmationEngine(config=config)
    return _engine


def evaluate(
    device_id,
    interface: str,
    *,
    eligible: Optional[bool] = None,
    current_risk: Optional[float] = None,
    risk_rows: Optional[list[dict]] = None,
    poll_failed: Optional[bool] = None,
    poll_failure_reason: Optional[str] = None,
    previous_confirmation: Optional[dict] = None,
    hostname: Optional[str] = None,
    ip_address: Optional[str] = None,
    persist: bool = False,
) -> ConfirmationResult:
    """
    Public entry-point for future Safety Engine::

        result = confirmation.evaluate(device_id, interface)
    """
    return get_confirmation_engine().evaluate(
        device_id,
        interface,
        eligible=eligible,
        current_risk=current_risk,
        risk_rows=risk_rows,
        poll_failed=poll_failed,
        poll_failure_reason=poll_failure_reason,
        previous_confirmation=previous_confirmation,
        hostname=hostname,
        ip_address=ip_address,
        persist=persist,
    )


def ensure_confirmation_indexes() -> None:
    try:
        coll = _db()[COLLECTION]
        coll.create_index(
            [
                ("deviceId", ASCENDING),
                ("interface", ASCENDING),
                ("timestamp", DESCENDING),
            ],
            name="idx_confirm_device_iface_ts",
        )
        coll.create_index(
            [("timestamp", DESCENDING)],
            name="idx_confirm_timestamp",
        )
        coll.create_index(
            [("confirmed", ASCENDING), ("timestamp", DESCENDING)],
            name="idx_confirm_confirmed_ts",
        )
        coll.create_index(
            [("state", ASCENDING), ("timestamp", DESCENDING)],
            name="idx_confirm_state_ts",
        )
        logger.info("[CONFIRM] MongoDB indexes ensured on %s", COLLECTION)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[CONFIRM] Failed to ensure indexes: %s", exc)


def evaluate_all_confirmations() -> dict[str, Any]:
    """
    Evaluate confirmation for every interface that has risk history.

    Safe for APScheduler — never raises. Does not invoke Safety/Mitigation.
    """
    config = get_confirmation_config()
    if not config.confirmation_enabled:
        logger.info("[CONFIRM] Skipped — confirmationEnabled=false")
        return {
            "total": 0,
            "confirmed": 0,
            "pending": 0,
            "notConfirmed": 0,
            "errors": 0,
            "disabled": True,
        }

    logger.info("[CONFIRM] Bulk confirmation evaluation started")
    started = time.monotonic()
    engine = get_confirmation_engine(force_new=True)

    total = 0
    confirmed = 0
    pending = 0
    not_confirmed = 0
    errors = 0

    try:
        # Prefer interfaces that have a latest risk score.
        pipeline = [
            {"$sort": {"timestamp": DESCENDING}},
            {
                "$group": {
                    "_id": {
                        "deviceId": "$deviceId",
                        "interface": "$interface",
                    },
                    "hostname": {"$first": "$hostname"},
                    "ipAddress": {"$first": "$ipAddress"},
                }
            },
        ]
        for row in _db().storm_risk_history.aggregate(pipeline):
            key = row.get("_id") or {}
            device_id = key.get("deviceId")
            name = key.get("interface")
            if device_id is None or not name:
                continue
            total += 1
            try:
                result = engine.evaluate(
                    device_id,
                    name,
                    hostname=row.get("hostname"),
                    ip_address=row.get("ipAddress"),
                    persist=True,
                )
                if result.state == STATE_CONFIRMED:
                    confirmed += 1
                elif result.state == STATE_PENDING:
                    pending += 1
                else:
                    not_confirmed += 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.error("[CONFIRM] Failed %s: %s", name, exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[CONFIRM] Bulk evaluation aborted: %s", exc)
        return {
            "total": total,
            "confirmed": confirmed,
            "pending": pending,
            "notConfirmed": not_confirmed,
            "errors": errors + 1,
            "disabled": False,
        }

    elapsed = round(time.monotonic() - started, 2)
    logger.info(
        "[CONFIRM] Bulk complete | total=%s confirmed=%s pending=%s "
        "notConfirmed=%s errors=%s elapsed=%.2fs",
        total,
        confirmed,
        pending,
        not_confirmed,
        errors,
        elapsed,
    )
    return {
        "total": total,
        "confirmed": confirmed,
        "pending": pending,
        "notConfirmed": not_confirmed,
        "errors": errors,
        "disabled": False,
    }


def get_latest_confirmation_results(
    device_id: Optional[ObjectId] = None,
    interface: Optional[str] = None,
    *,
    state: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict], int]:
    match: dict[str, Any] = {}
    if device_id is not None:
        match["deviceId"] = device_id
    if interface:
        match["interface"] = interface

    pipeline: list[dict[str, Any]] = []
    if match:
        pipeline.append({"$match": match})

    pipeline.extend(
        [
            {"$sort": {"timestamp": DESCENDING}},
            {
                "$group": {
                    "_id": {
                        "deviceId": "$deviceId",
                        "interface": "$interface",
                    },
                    "doc": {"$first": "$$ROOT"},
                }
            },
            {"$replaceRoot": {"newRoot": "$doc"}},
        ]
    )

    post: dict[str, Any] = {}
    if state:
        post["state"] = state.upper()
    if search:
        regex = {"$regex": search, "$options": "i"}
        post["$or"] = [
            {"interface": regex},
            {"hostname": regex},
            {"ipAddress": regex},
            {"state": regex},
            {"reason": regex},
        ]
    if post:
        pipeline.append({"$match": post})

    pipeline.append(
        {"$sort": {"confirmed": DESCENDING, "consecutiveHighSamples": DESCENDING, "timestamp": DESCENDING}}
    )

    coll = _db()[COLLECTION]
    count_pipeline = list(pipeline) + [{"$count": "total"}]
    count_result = list(coll.aggregate(count_pipeline))
    total = int(count_result[0]["total"]) if count_result else 0

    pipeline.extend(
        [
            {"$skip": max(int(skip), 0)},
            {"$limit": max(int(limit), 1)},
        ]
    )
    return list(coll.aggregate(pipeline)), total


def get_confirmation_history(
    device_id: ObjectId,
    interface: str,
    *,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict], int]:
    coll = _db()[COLLECTION]
    query = {"deviceId": device_id, "interface": interface}
    total = coll.count_documents(query)
    rows = list(
        coll.find(query)
        .sort("timestamp", DESCENDING)
        .skip(max(int(skip), 0))
        .limit(max(int(limit), 1))
    )
    return rows, total
