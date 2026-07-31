"""
Safety Engine
=============
Final decision layer before automatic mitigation.

Responsibility
--------------
Validate whether it is currently safe to execute mitigation on a confirmed
storm. Never performs mitigation, diagnostics, or recovery.

Public API
----------
    from services.storm.safety import evaluate
    result = evaluate(device_id, interface)

The Mitigation Engine and recovery policy call ``evaluate()`` and trust the result.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING

from services.storm.models import SafetyResult, create_safety_document
from services.storm.safety_checks import SafetyCheck, build_default_checks
from services.storm.safety_history import (
    SAFETY_COLLECTION,
    SafetyContext,
    build_safety_context,
)
from services.storm.safety_rules import SafetyConfig, get_safety_config
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.safety")

# Keys whose boolean in the public checks object means "hazard present"
# (inverted relative to pass/fail).
_HAZARD_KEYS = frozenset(
    {
        "maintenanceMode",
        "deviceLocked",
        "interfaceLocked",
        "mitigationRunning",
    }
)


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _public_check_value(key: str, passed: bool) -> bool:
    if key in _HAZARD_KEYS:
        return not passed
    return passed


class SafetyEngine:
    """
    Deterministic pre-mitigation gate (SOLID).

    - SRP: safety validation only
    - OCP: checks injectable
    - DIP: depends on SafetyConfig + SafetyContext
    """

    def __init__(
        self,
        config: Optional[SafetyConfig] = None,
        checks: Optional[tuple[SafetyCheck, ...]] = None,
    ) -> None:
        self._config = config or get_safety_config()
        self._checks = checks or build_default_checks()

    @property
    def config(self) -> SafetyConfig:
        return self._config

    def evaluate(
        self,
        device_id,
        interface: str,
        *,
        context: Optional[SafetyContext] = None,
        probe_ssh: bool = True,
        skip_check_codes: Optional[set[str]] = None,
        hostname: Optional[str] = None,
        ip_address: Optional[str] = None,
        persist: bool = False,
    ) -> SafetyResult:
        started = time.monotonic()
        now = datetime.now(timezone.utc)
        name = str(interface or "").strip()
        device_key = str(device_id) if device_id is not None else None

        logger.info("Safety evaluation started | %s", name or "unknown")

        if not self._config.safety_enabled:
            result = SafetyResult(
                safe=False,
                reason="Safety evaluation disabled",
                confidence=0.0,
                failed_rule=None,
                checks={},
                timestamp=now,
                device_id=device_key,
                interface=name or None,
                status="WAITING",
            )
            self._log(result, started)
            if persist and name:
                self._store(device_id, name, result, hostname, ip_address)
            return result

        if not name:
            result = SafetyResult(
                safe=False,
                reason="Missing interface name",
                confidence=0.0,
                failed_rule="RULE_4",
                checks={},
                timestamp=now,
                device_id=device_key,
                interface=None,
                status="UNSAFE",
            )
            self._log(result, started)
            return result

        try:
            ctx = context or build_safety_context(
                device_id,
                name,
                config=self._config,
                probe_ssh=probe_ssh,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Safety context build failed | %s | %s", name, exc)
            result = SafetyResult(
                safe=False,
                reason=f"Safety context unavailable: {exc}",
                confidence=0.0,
                failed_rule=None,
                checks={},
                timestamp=now,
                device_id=device_key,
                interface=name,
                status="UNSAFE",
            )
            self._log(result, started)
            if persist:
                self._store(device_id, name, result, hostname, ip_address)
            return result

        checks_out: dict[str, Any] = {}
        failed_rule: Optional[str] = None
        fail_reason: Optional[str] = None
        passed_count = 0
        skipped = set(skip_check_codes or ())
        evaluated_total = 0

        for check in self._checks:
            if check.code in skipped:
                # Recovery policy already performs its own explicit SSH reachability
                # probe. Skipping only RULE_3 here avoids a false negative when the
                # reused Safety evaluation intentionally sets probe_ssh=False.
                checks_out[check.key] = None
                continue

            evaluated_total += 1
            try:
                passed, detail = check.runner(ctx, self._config)
            except Exception as exc:  # noqa: BLE001
                passed = False
                detail = f"{check.reason_fail} ({exc})"
                logger.warning(
                    "Safety check error | %s | %s | %s",
                    name,
                    check.code,
                    exc,
                )

            checks_out[check.key] = _public_check_value(check.key, passed)
            if passed:
                passed_count += 1
                continue

            failed_rule = check.code
            fail_reason = detail or check.reason_fail
            break

        confidence = round((passed_count / max(evaluated_total, 1)) * 100.0, 2)

        if failed_rule:
            status = "WAITING" if failed_rule == "RULE_8" else "UNSAFE"
            if failed_rule == "RULE_8":
                logger.info("Cooldown active | %s | %s", name, fail_reason)
            if failed_rule == "RULE_10":
                logger.info("Maintenance mode | %s", name)
            result = SafetyResult(
                safe=False,
                reason=fail_reason or "Safety check failed",
                confidence=confidence,
                failed_rule=failed_rule,
                checks=checks_out,
                timestamp=now,
                device_id=device_key,
                interface=name,
                cooldown_remaining_seconds=ctx.cooldown_remaining_seconds,
                mitigation_attempts=ctx.mitigation_attempts,
                cpu_percent=ctx.cpu_percent,
                memory_percent=ctx.memory_percent,
                status=status,
            )
            logger.info(
                "Safety failed | %s | rule=%s | %s",
                name,
                failed_rule,
                fail_reason,
            )
        else:
            # Fill remaining hazard keys for a complete checks object
            for check in self._checks:
                checks_out.setdefault(
                    check.key,
                    _public_check_value(check.key, True),
                )
            result = SafetyResult(
                safe=True,
                reason="All safety checks passed",
                confidence=max(confidence, 99.0)
                if passed_count == evaluated_total
                else confidence,
                failed_rule=None,
                checks=checks_out,
                timestamp=now,
                device_id=device_key,
                interface=name,
                cooldown_remaining_seconds=0,
                mitigation_attempts=ctx.mitigation_attempts,
                cpu_percent=ctx.cpu_percent,
                memory_percent=ctx.memory_percent,
                status="SAFE",
            )
            logger.info("Safety passed | %s", name)

        # Enrich hostname for persistence
        host = hostname or (ctx.device or {}).get("hostname") or (ctx.iface or {}).get("hostname")
        ip = ip_address or (ctx.device or {}).get("ipAddress") or (ctx.iface or {}).get("ipAddress")

        self._log(result, started)
        if persist:
            self._store(device_id, name, result, host, ip)
        return result

    def _store(
        self,
        device_id,
        interface: str,
        result: SafetyResult,
        hostname: Optional[str],
        ip_address: Optional[str],
    ) -> None:
        try:
            oid = device_id
            if isinstance(oid, str) and ObjectId.is_valid(oid):
                oid = ObjectId(oid)
            document = create_safety_document(
                device_id=oid,
                interface=interface,
                result=result,
                hostname=hostname,
                ip_address=ip_address,
            )
            _db()[SAFETY_COLLECTION].insert_one(document)
        except Exception as exc:  # noqa: BLE001
            logger.error("[SAFETY] Failed to store history: %s", exc)

    @staticmethod
    def _log(result: SafetyResult, started: float) -> None:
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        logger.info(
            "Safety | %s | safe=%s | status=%s | rule=%s | %sms",
            result.interface or "unknown",
            result.safe,
            result.status,
            result.failed_rule or "-",
            elapsed_ms,
        )


_engine: Optional[SafetyEngine] = None


def get_safety_engine(
    config: Optional[SafetyConfig] = None,
    *,
    force_new: bool = False,
) -> SafetyEngine:
    global _engine
    if force_new or _engine is None or config is not None:
        _engine = SafetyEngine(config=config)
    return _engine


def evaluate(
    device_id,
    interface: str,
    *,
    context: Optional[SafetyContext] = None,
    probe_ssh: bool = True,
    skip_check_codes: Optional[set[str]] = None,
    hostname: Optional[str] = None,
    ip_address: Optional[str] = None,
    persist: bool = False,
) -> SafetyResult:
    """
    Public entry-point for the Mitigation Engine and recovery policy::

        result = safety.evaluate(device_id, interface)
    """
    return get_safety_engine().evaluate(
        device_id,
        interface,
        context=context,
        probe_ssh=probe_ssh,
        skip_check_codes=skip_check_codes,
        hostname=hostname,
        ip_address=ip_address,
        persist=persist,
    )


def ensure_safety_indexes() -> None:
    try:
        coll = _db()[SAFETY_COLLECTION]
        coll.create_index(
            [
                ("deviceId", ASCENDING),
                ("interface", ASCENDING),
                ("timestamp", DESCENDING),
            ],
            name="idx_safety_device_iface_ts",
        )
        coll.create_index(
            [("timestamp", DESCENDING)],
            name="idx_safety_timestamp",
        )
        coll.create_index(
            [("safe", ASCENDING), ("timestamp", DESCENDING)],
            name="idx_safety_safe_ts",
        )
        coll.create_index(
            [("status", ASCENDING), ("timestamp", DESCENDING)],
            name="idx_safety_status_ts",
        )
        logger.info("[SAFETY] MongoDB indexes ensured on %s", SAFETY_COLLECTION)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SAFETY] Failed to ensure indexes: %s", exc)


def evaluate_all_safety(*, probe_ssh: bool = True) -> dict[str, Any]:
    """
    Evaluate safety for every interface with a latest CONFIRMED storm.

    Safe for APScheduler — never raises. Does not invoke Mitigation.
    """
    config = get_safety_config()
    if not config.safety_enabled:
        logger.info("[SAFETY] Skipped — safetyEnabled=false")
        return {
            "total": 0,
            "safe": 0,
            "unsafe": 0,
            "waiting": 0,
            "errors": 0,
            "disabled": True,
        }

    logger.info("[SAFETY] Bulk safety evaluation started")
    started = time.monotonic()
    engine = get_safety_engine(force_new=True)

    total = 0
    safe = 0
    unsafe = 0
    waiting = 0
    errors = 0

    try:
        pipeline = [
            {"$sort": {"timestamp": DESCENDING}},
            {
                "$group": {
                    "_id": {
                        "deviceId": "$deviceId",
                        "interface": "$interface",
                    },
                    "confirmed": {"$first": "$confirmed"},
                    "state": {"$first": "$state"},
                    "hostname": {"$first": "$hostname"},
                    "ipAddress": {"$first": "$ipAddress"},
                }
            },
            {
                "$match": {
                    "$or": [
                        {"confirmed": True},
                        {"state": "CONFIRMED"},
                    ]
                }
            },
        ]
        for row in _db().storm_confirmation_history.aggregate(pipeline):
            key = row.get("_id") or {}
            device_id = key.get("deviceId")
            name = key.get("interface")
            if device_id is None or not name:
                continue
            # Only evaluate interfaces whose latest confirmation is still CONFIRMED.
            try:
                from services.storm.confirmation_history import (  # noqa: PLC0415
                    load_latest_confirmation,
                )

                latest = load_latest_confirmation(device_id, name)
                if not latest or not (
                    latest.get("confirmed")
                    or str(latest.get("state") or "").upper() == "CONFIRMED"
                ):
                    continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("[SAFETY] Confirmation gate skipped %s: %s", name, exc)
                continue

            total += 1
            try:
                result = engine.evaluate(
                    device_id,
                    name,
                    probe_ssh=probe_ssh,
                    hostname=row.get("hostname") or (latest or {}).get("hostname"),
                    ip_address=row.get("ipAddress") or (latest or {}).get("ipAddress"),
                    persist=True,
                )
                if result.safe:
                    safe += 1
                elif result.status == "WAITING":
                    waiting += 1
                else:
                    unsafe += 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.error("[SAFETY] Failed %s: %s", name, exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[SAFETY] Bulk evaluation aborted: %s", exc)
        return {
            "total": total,
            "safe": safe,
            "unsafe": unsafe,
            "waiting": waiting,
            "errors": errors + 1,
            "disabled": False,
        }

    elapsed = round(time.monotonic() - started, 2)
    logger.info(
        "[SAFETY] Bulk complete | total=%s safe=%s unsafe=%s waiting=%s "
        "errors=%s elapsed=%.2fs",
        total,
        safe,
        unsafe,
        waiting,
        errors,
        elapsed,
    )
    return {
        "total": total,
        "safe": safe,
        "unsafe": unsafe,
        "waiting": waiting,
        "errors": errors,
        "disabled": False,
    }


def get_latest_safety_results(
    device_id: Optional[ObjectId] = None,
    interface: Optional[str] = None,
    *,
    status: Optional[str] = None,
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
    if status:
        post["status"] = status.upper()
    if search:
        regex = {"$regex": search, "$options": "i"}
        post["$or"] = [
            {"interface": regex},
            {"hostname": regex},
            {"ipAddress": regex},
            {"reason": regex},
            {"failedRule": regex},
            {"status": regex},
        ]
    if post:
        pipeline.append({"$match": post})

    pipeline.append(
        {"$sort": {"safe": DESCENDING, "timestamp": DESCENDING}}
    )

    coll = _db()[SAFETY_COLLECTION]
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


def get_safety_history(
    device_id: ObjectId,
    interface: str,
    *,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict], int]:
    coll = _db()[SAFETY_COLLECTION]
    query = {"deviceId": device_id, "interface": interface}
    total = coll.count_documents(query)
    rows = list(
        coll.find(query)
        .sort("timestamp", DESCENDING)
        .skip(max(int(skip), 0))
        .limit(max(int(limit), 1))
    )
    return rows, total
