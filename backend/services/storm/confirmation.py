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

        # ── Source attribution gate (receivers / non-selected origins) ─
        # Keep telemetry scoring intact; only block progression toward CONFIRMED.
        try:
            from services.storm.storm_source_selector import (  # noqa: PLC0415
                confirmation_allowed_for_source,
            )

            source_risk = latest_risk
            if source_risk is None and risk_rows:
                source_risk = risk_rows[0]
            allowed, block_reason, _selection = confirmation_allowed_for_source(
                device_id,
                name,
                risk_doc=source_risk,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Source attribution gate failed | %s | %s — allowing confirmation path",
                name,
                exc,
            )
            allowed, block_reason = True, ""

        if not allowed:
            result = ConfirmationResult(
                confirmed=False,
                state=STATE_NOT_CONFIRMED,
                current_risk=round(current_risk, 2),
                highest_risk=round(current_risk, 2),
                average_risk=round(current_risk, 2),
                consecutive_high_samples=0,
                required_samples=required,
                reason=block_reason or "Blocked by storm source attribution",
                timestamp=now,
                device_id=device_key,
                interface=name,
                reset=True,
                reset_reason=block_reason or "source_attribution",
            )
            logger.info(
                "Confirmation Blocked | %s | Reason | %s",
                name,
                block_reason,
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
        coll.create_index(
            [("cycleId", ASCENDING)],
            name="idx_confirm_cycle",
        )
        logger.info("[CONFIRM] MongoDB indexes ensured on %s", COLLECTION)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[CONFIRM] Failed to ensure indexes: %s", exc)


def _confirmation_worker_count(*, freeze_latest_inputs: bool) -> int:
    """Bounded concurrency across different interfaces only (default 4, max 4)."""
    import os  # noqa: PLC0415

    if not freeze_latest_inputs:
        return 1
    raw = os.environ.get("STORM_CONFIRMATION_WORKERS", "4")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 4
    return max(1, min(value, 4))


def evaluate_all_confirmations(
    *,
    freeze_latest_inputs: bool = False,
    cycle_id: str | None = None,
    workers: int | None = None,
) -> dict[str, Any]:
    """
    Evaluate confirmation for every interface that has risk history.

    Safe for APScheduler — never raises. Does not invoke Safety/Mitigation.

    When ``freeze_latest_inputs`` is True (storm confirmation job), eligibility,
    recent risk rows, previous confirmation state, and poll-failure inputs are
    snapshotted via bulk Mongo reads before evaluation so concurrent analysis
    cannot change mid-pass inputs. ConfirmationEngine decision logic is unchanged.

    Snapshot maps represent the latest documents available at freeze time for
    the candidate set (and when ``cycle_id`` is set, that cycle's risk row is
    preferred as the newest sample when present — same as Phase 1).
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

    worker_count = (
        max(1, min(int(workers), 4))
        if workers is not None
        else _confirmation_worker_count(freeze_latest_inputs=freeze_latest_inputs)
    )

    logger.info(
        "[CONFIRM] Bulk confirmation evaluation started | freeze=%s | "
        "cycleId=%s | workers=%s",
        freeze_latest_inputs,
        cycle_id or "-",
        worker_count,
    )
    started = time.monotonic()
    engine = get_confirmation_engine(force_new=True)

    total = 0
    confirmed = 0
    pending = 0
    not_confirmed = 0
    errors = 0
    candidate_source = "history"
    risk_lookup_meta: dict[str, Any] = {
        "riskLatestHit": False,
        "riskLatestFallback": False,
        "riskLookupDurationMs": 0,
        "source": "n/a",
    }

    try:
        # Prefer interfaces that have risk history (ever scored).
        # When risk_latest is rebuilt/populated it preserves that population;
        # otherwise fall back to the history distinct aggregate.
        candidates: list[dict[str, Any]]
        try:
            from services.storm.risk_latest import (  # noqa: PLC0415
                load_confirmation_candidates_from_latest,
                risk_latest_enabled,
            )

            if risk_latest_enabled():
                latest_candidates = load_confirmation_candidates_from_latest()
                if latest_candidates:
                    candidates = latest_candidates
                    candidate_source = "risk_latest"
                else:
                    candidates = []
            else:
                candidates = []
        except Exception:  # noqa: BLE001
            candidates = []

        if not candidates:
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
            candidates = list(_db().storm_risk_history.aggregate(pipeline))
            candidate_source = "history"

        prepared: list[dict[str, Any]] = []
        if freeze_latest_inputs:
            from services.storm.confirmation_prefetch import (  # noqa: PLC0415
                bulk_device_status_map,
                bulk_interface_exists_set,
                bulk_latest_confirmation_map,
                bulk_latest_eligibility_map,
                bulk_latest_stats_map,
                bulk_recent_risk_rows_map,
                detect_poll_failure_from_maps,
                prefer_cycle_risk_rows,
            )

            required = max(int(config.required_confirmations), 1)
            risk_limit = max(required * 3, 12)
            pairs: list[tuple[Any, str]] = []
            for row in candidates:
                key = row.get("_id") or {}
                device_id = key.get("deviceId")
                name = key.get("interface")
                if device_id is None or not name:
                    continue
                pairs.append((device_id, str(name)))

            eligibility_map = bulk_latest_eligibility_map(pairs)
            confirmation_map = bulk_latest_confirmation_map(pairs)
            risk_map, risk_lookup_meta = bulk_recent_risk_rows_map(
                limit=risk_limit, pairs=pairs
            )
            device_ids = list({d for d, _ in pairs})
            status_map = bulk_device_status_map(device_ids)
            exists_set = bulk_interface_exists_set(pairs)
            stats_map = bulk_latest_stats_map(pairs)

            for row in candidates:
                key = row.get("_id") or {}
                device_id = key.get("deviceId")
                name = key.get("interface")
                if device_id is None or not name:
                    continue
                map_key = (str(device_id), str(name))
                risk_rows = prefer_cycle_risk_rows(
                    risk_map.get(map_key, []),
                    cycle_id,
                )
                latest_risk = risk_rows[0] if risk_rows else None
                eligible = eligibility_map.get(map_key)
                previous = confirmation_map.get(map_key)
                poll_failed, poll_failure_reason = detect_poll_failure_from_maps(
                    device_id,
                    str(name),
                    stale_seconds=config.poll_stale_seconds,
                    latest_risk=latest_risk,
                    risk_rows=risk_rows,
                    device_status=status_map.get(str(device_id)),
                    interface_exists=map_key in exists_set,
                    latest_stat=stats_map.get(map_key),
                )
                prepared.append(
                    {
                        "device_id": device_id,
                        "name": name,
                        "hostname": row.get("hostname"),
                        "ip_address": row.get("ipAddress"),
                        "eligible": eligible,
                        "risk_rows": risk_rows,
                        # Empty dict freezes "no previous" without re-query
                        # (same field defaults as None in evaluate()).
                        "previous": previous if previous is not None else {},
                        "poll_failed": poll_failed,
                        "poll_failure_reason": poll_failure_reason,
                    }
                )
        else:
            for row in candidates:
                key = row.get("_id") or {}
                device_id = key.get("deviceId")
                name = key.get("interface")
                if device_id is None or not name:
                    continue
                prepared.append(
                    {
                        "device_id": device_id,
                        "name": name,
                        "hostname": row.get("hostname"),
                        "ip_address": row.get("ipAddress"),
                        "eligible": None,
                        "risk_rows": None,
                        "previous": None,
                        "poll_failed": None,
                        "poll_failure_reason": None,
                    }
                )

        def _evaluate_one(item: dict[str, Any]) -> tuple[Optional[ConfirmationResult], Optional[dict]]:
            kwargs: dict[str, Any] = {
                "hostname": item.get("hostname"),
                "ip_address": item.get("ip_address"),
                "persist": False,
            }
            if freeze_latest_inputs:
                eligible = item.get("eligible")
                kwargs["eligible"] = False if eligible is None else bool(eligible)
                kwargs["risk_rows"] = item.get("risk_rows") or []
                kwargs["previous_confirmation"] = item.get("previous")
                kwargs["poll_failed"] = bool(item.get("poll_failed"))
                kwargs["poll_failure_reason"] = item.get("poll_failure_reason")
            result = engine.evaluate(
                item["device_id"],
                item["name"],
                **kwargs,
            )
            oid = item["device_id"]
            if isinstance(oid, str) and ObjectId.is_valid(oid):
                oid = ObjectId(oid)
            document = create_confirmation_document(
                device_id=oid,
                interface=item["name"],
                result=result,
                hostname=item.get("hostname"),
                ip_address=item.get("ip_address"),
            )
            if cycle_id:
                document["cycleId"] = str(cycle_id)
            return result, document

        results: list[tuple[Optional[ConfirmationResult], Optional[dict]]] = []
        if worker_count <= 1 or len(prepared) <= 1:
            for item in prepared:
                total += 1
                try:
                    results.append(_evaluate_one(item))
                except Exception as exc:  # noqa: BLE001
                    errors += 1
                    results.append((None, None))
                    logger.error("[CONFIRM] Failed %s: %s", item.get("name"), exc)
        else:
            from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: PLC0415

            # One task per interface — never two workers on the same interface.
            total = len(prepared)
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                futures = {
                    pool.submit(_evaluate_one, item): item for item in prepared
                }
                for fut in as_completed(futures):
                    item = futures[fut]
                    try:
                        results.append(fut.result())
                    except Exception as exc:  # noqa: BLE001
                        errors += 1
                        results.append((None, None))
                        logger.error(
                            "[CONFIRM] Failed %s: %s", item.get("name"), exc
                        )

        documents: list[dict] = []
        for result, document in results:
            if result is None:
                continue
            if result.state == STATE_CONFIRMED:
                confirmed += 1
            elif result.state == STATE_PENDING:
                pending += 1
            else:
                not_confirmed += 1
            if document is not None:
                documents.append(document)

        if documents:
            # Append-only history: same documents as insert_one, batched.
            _db()[COLLECTION].insert_many(documents, ordered=False)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[CONFIRM] Bulk evaluation aborted: %s", exc)
        return {
            "total": total,
            "confirmed": confirmed,
            "pending": pending,
            "notConfirmed": not_confirmed,
            "errors": errors + 1,
            "disabled": False,
            "workers": worker_count,
        }

    elapsed = round(time.monotonic() - started, 2)
    elapsed_ms = int(elapsed * 1000)
    logger.info(
        "[CONFIRM] Bulk complete | total=%s confirmed=%s pending=%s "
        "notConfirmed=%s errors=%s confirmationDurationMs=%s | cycleId=%s | "
        "workers=%s | candidateCount=%s candidateSource=%s | "
        "riskLatestHit=%s riskLatestFallback=%s riskLookupDurationMs=%s "
        "riskLookupSource=%s",
        total,
        confirmed,
        pending,
        not_confirmed,
        errors,
        elapsed_ms,
        cycle_id or "-",
        worker_count,
        total,
        candidate_source,
        risk_lookup_meta.get("riskLatestHit"),
        risk_lookup_meta.get("riskLatestFallback"),
        risk_lookup_meta.get("riskLookupDurationMs"),
        risk_lookup_meta.get("source"),
    )
    return {
        "total": total,
        "confirmed": confirmed,
        "pending": pending,
        "notConfirmed": not_confirmed,
        "errors": errors,
        "disabled": False,
        "workers": worker_count,
        "candidateSource": candidate_source,
        "riskLatestHit": bool(risk_lookup_meta.get("riskLatestHit")),
        "riskLatestFallback": bool(risk_lookup_meta.get("riskLatestFallback")),
        "riskLookupDurationMs": int(risk_lookup_meta.get("riskLookupDurationMs") or 0),
        "confirmationDurationMs": elapsed_ms,
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
        from utils.mongo_safe import regex_filter  # noqa: PLC0415
        regex = regex_filter(search)
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
