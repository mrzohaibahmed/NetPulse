"""
Port Eligibility Engine
=======================
First decision engine of the Storm Protection system.

Responsibility
--------------
Determine whether an interface is eligible for automated storm analysis
and mitigation. Operates solely on already-collected interface metadata
from MongoDB — no SSH, no CLI parsing, no risk scoring.

Public API
----------
    from services.storm import evaluate
    result = evaluate(interface)

Future Risk / Confirmation / Safety / Mitigation engines must call
``evaluate()`` (or ``EligibilityEngine.evaluate``) without modification.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING

from services.storm.config import StormConfig, get_storm_config
from services.storm.exceptions import (
    EligibilityDisabledError,
    InvalidInterfaceDataError,
    MissingInterfaceError,
    StormEligibilityError,
)
from services.storm.models import (
    EligibilityChecks,
    EligibilityResult,
    NormalizedInterface,
    create_eligibility_document,
)
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.eligibility")

COLLECTION = "eligibility_results"
REASON_ELIGIBLE = "Access Port"


def _db():
    """Lazy Mongo handle so the pure engine can be unit-tested without DB."""
    from config.database import db  # noqa: PLC0415

    return db


@dataclass(frozen=True)
class _Rule:
    """Single eligibility rule (Open/Closed — add rules without changing callers)."""

    code: str
    check_key: str
    reason: str
    predicate: Callable[[NormalizedInterface, StormConfig], bool]


def _is_up(status: str) -> bool:
    return status in ("up", "connected")


def _build_rules() -> tuple[_Rule, ...]:
    """Ordered eligibility rules. Evaluation short-circuits on first failure."""
    return (
        _Rule(
            code="RULE_1",
            check_key="monitoring",
            reason="Monitoring Disabled",
            # Preference only (AUTO vs DISABLED_BY_USER). Admin/oper down
            # remain RULE_2 / RULE_3 so temporary shutdowns never latch.
            predicate=lambda iface, _cfg: iface.monitoring_enabled,
        ),
        _Rule(
            code="RULE_2",
            check_key="admin",
            reason="Administrative Down",
            predicate=lambda iface, _cfg: _is_up(iface.admin_status),
        ),
        _Rule(
            code="RULE_3",
            check_key="oper",
            reason="Operational Down",
            predicate=lambda iface, _cfg: _is_up(iface.oper_status),
        ),
        _Rule(
            code="RULE_4",
            check_key="access",
            reason="Not an Access Port",
            predicate=lambda iface, _cfg: iface.is_access,
        ),
        _Rule(
            code="RULE_5",
            check_key="trunk",
            reason="Trunk Port",
            predicate=lambda iface, cfg: (
                cfg.allow_trunks or not iface.is_trunk
            ),
        ),
        _Rule(
            code="RULE_6",
            check_key="uplink",
            reason="Uplink Port",
            predicate=lambda iface, _cfg: not iface.is_uplink,
        ),
        _Rule(
            code="RULE_7",
            check_key="infrastructure",
            reason="Infrastructure Port",
            predicate=lambda iface, cfg: (
                cfg.allow_infrastructure_ports or not iface.is_infrastructure
            ),
        ),
        _Rule(
            code="RULE_8",
            check_key="management",
            reason="Management Port",
            predicate=lambda iface, cfg: (
                cfg.allow_management_ports or not iface.is_management
            ),
        ),
        _Rule(
            code="RULE_9",
            check_key="protected",
            reason="Protected Port",
            predicate=lambda iface, cfg: (
                cfg.allow_protected_ports or not iface.is_protected
            ),
        ),
    )


class EligibilityEngine:
    """
    Stateless, deterministic Port Eligibility Engine (SOLID).

    - Single Responsibility: eligibility decisions only
    - Open/Closed: rules list is injectable
    - Dependency Inversion: depends on StormConfig, not env literals
    """

    def __init__(
        self,
        config: Optional[StormConfig] = None,
        rules: Optional[tuple[_Rule, ...]] = None,
    ) -> None:
        self._config = config or get_storm_config()
        self._rules = rules or _build_rules()

    @property
    def config(self) -> StormConfig:
        return self._config

    def evaluate(self, interface: dict[str, Any]) -> EligibilityResult:
        """
        Evaluate a normalised (or Mongo) interface document.

        Parameters
        ----------
        interface:
            Interface metadata dict. Accepts camelCase Mongo documents
            and snake_case payloads.

        Returns
        -------
        EligibilityResult
            Strongly typed decision. Never raises for business-rule failures.
        """
        if interface is None:
            raise MissingInterfaceError("Interface payload is required")

        if not isinstance(interface, dict):
            raise InvalidInterfaceDataError(
                f"Interface payload must be a dict, got {type(interface).__name__}"
            )

        try:
            normalised = NormalizedInterface.from_raw(interface)
        except ValueError as exc:
            raise InvalidInterfaceDataError(str(exc)) from exc

        if not self._config.enable_eligibility:
            result = EligibilityResult(
                eligible=False,
                reason="Eligibility Disabled",
                confidence=self._config.confidence,
                failed_rule=None,
                checks=EligibilityChecks(),
                device_id=normalised.device_id or None,
                interface=normalised.interface,
            )
            self._log_result(result)
            return result

        checks = EligibilityChecks()
        for rule in self._rules:
            passed = bool(rule.predicate(normalised, self._config))
            setattr(checks, rule.check_key, passed)
            if not passed:
                result = EligibilityResult(
                    eligible=False,
                    reason=rule.reason,
                    confidence=self._config.confidence,
                    failed_rule=rule.code,
                    checks=checks,
                    device_id=normalised.device_id or None,
                    interface=normalised.interface,
                )
                self._log_result(result)
                return result

        result = EligibilityResult(
            eligible=True,
            reason=REASON_ELIGIBLE,
            confidence=self._config.confidence,
            failed_rule=None,
            checks=checks,
            device_id=normalised.device_id or None,
            interface=normalised.interface,
        )
        self._log_result(result)
        return result

    @staticmethod
    def _log_result(result: EligibilityResult) -> None:
        name = result.interface or "unknown"
        if result.eligible:
            logger.info("Eligibility Passed | %s", name)
        else:
            logger.info(
                "Eligibility Failed | %s | Reason | %s",
                name,
                result.reason,
            )


# ---------------------------------------------------------------------------
# Module-level reusable API (consumed by future Storm engines)
# ---------------------------------------------------------------------------

_engine: Optional[EligibilityEngine] = None


def get_eligibility_engine(
    config: Optional[StormConfig] = None,
    *,
    force_new: bool = False,
) -> EligibilityEngine:
    """Return a shared EligibilityEngine instance."""
    global _engine
    if force_new or _engine is None or config is not None:
        _engine = EligibilityEngine(config=config)
    return _engine


def evaluate(interface: dict[str, Any]) -> EligibilityResult:
    """
    Evaluate whether an interface is eligible for storm analysis.

    This is the stable entry-point for future Storm modules::

        result = eligibility.evaluate(interface)
    """
    return get_eligibility_engine().evaluate(interface)


# ---------------------------------------------------------------------------
# Persistence & bulk evaluation (scheduler / API)
# ---------------------------------------------------------------------------

def ensure_eligibility_indexes() -> None:
    """Create indexes for latest-result and history queries."""
    try:
        coll = _db()[COLLECTION]
        coll.create_index(
            [
                ("deviceId", ASCENDING),
                ("interface", ASCENDING),
                ("timestamp", DESCENDING),
            ],
            name="idx_eligibility_device_iface_ts",
        )
        coll.create_index(
            [("timestamp", DESCENDING)],
            name="idx_eligibility_timestamp",
        )
        coll.create_index(
            [("eligible", ASCENDING), ("timestamp", DESCENDING)],
            name="idx_eligibility_eligible_ts",
        )
        logger.info("[ELIGIBILITY] MongoDB indexes ensured on %s", COLLECTION)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ELIGIBILITY] Failed to ensure indexes: %s", exc)


def store_eligibility_result(
    interface_doc: dict[str, Any],
    result: EligibilityResult,
) -> Optional[ObjectId]:
    """Append an eligibility evaluation to MongoDB history. Never overwrites."""
    try:
        device_id = interface_doc.get("deviceId") or interface_doc.get("device_id")
        name = (
            result.interface
            or interface_doc.get("name")
            or interface_doc.get("interface")
        )
        if device_id is None or not name:
            logger.warning(
                "[ELIGIBILITY] Skipping persist — missing deviceId/interface"
            )
            return None

        if isinstance(device_id, str) and ObjectId.is_valid(device_id):
            device_id = ObjectId(device_id)

        document = create_eligibility_document(
            device_id=device_id,
            interface=str(name),
            result=result,
            hostname=interface_doc.get("hostname"),
            ip_address=interface_doc.get("ipAddress") or interface_doc.get("ip_address"),
        )
        inserted = _db()[COLLECTION].insert_one(document)
        return inserted.inserted_id
    except Exception as exc:  # noqa: BLE001
        logger.error("[ELIGIBILITY] Failed to store result: %s", exc)
        return None


def evaluate_and_store(interface_doc: dict[str, Any]) -> EligibilityResult:
    """Evaluate one interface document and append the result to history."""
    result = evaluate(interface_doc)
    store_eligibility_result(interface_doc, result)
    return result


def evaluate_all_interfaces() -> dict[str, Any]:
    """
    Evaluate every interface currently stored in MongoDB.

    Safe for the APScheduler thread — never raises.
    Stops after eligibility; does not invoke future Storm engines.
    """
    config = get_storm_config()
    if not config.enable_eligibility:
        logger.info("[ELIGIBILITY] Skipped — enableEligibility=false")
        return {
            "total": 0,
            "eligible": 0,
            "ineligible": 0,
            "errors": 0,
            "skipped": True,
        }

    logger.info("[ELIGIBILITY] Bulk evaluation started")
    start = datetime.now(timezone.utc)

    total = 0
    eligible_count = 0
    ineligible_count = 0
    errors = 0

    try:
        cursor = _db().interfaces.find({})
        for doc in cursor:
            total += 1
            try:
                result = evaluate_and_store(doc)
                if result.eligible:
                    eligible_count += 1
                else:
                    ineligible_count += 1
            except StormEligibilityError as exc:
                errors += 1
                logger.warning(
                    "[ELIGIBILITY] Skipped corrupt/missing interface: %s",
                    exc,
                )
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.error(
                    "[ELIGIBILITY] Unexpected error evaluating interface: %s",
                    exc,
                )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[ELIGIBILITY] Bulk evaluation aborted: %s", exc)
        return {
            "total": total,
            "eligible": eligible_count,
            "ineligible": ineligible_count,
            "errors": errors + 1,
            "skipped": False,
        }

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    logger.info(
        "[ELIGIBILITY] Bulk evaluation complete | total=%s eligible=%s "
        "ineligible=%s errors=%s elapsed=%.2fs",
        total,
        eligible_count,
        ineligible_count,
        errors,
        elapsed,
    )
    return {
        "total": total,
        "eligible": eligible_count,
        "ineligible": ineligible_count,
        "errors": errors,
        "skipped": False,
    }


def get_latest_eligibility_results(
    device_id: Optional[ObjectId] = None,
    interface: Optional[str] = None,
    *,
    eligible: Optional[bool] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict], int]:
    """
    Return the latest evaluation per (deviceId, interface).

    Historical rows are retained; this query surfaces only the newest
    decision for each port (optionally filtered).
    """
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

    post_match: dict[str, Any] = {}
    if eligible is not None:
        post_match["eligible"] = bool(eligible)
    if search:
        regex = {"$regex": search, "$options": "i"}
        post_match["$or"] = [
            {"interface": regex},
            {"hostname": regex},
            {"ipAddress": regex},
            {"reason": regex},
            {"failedRule": regex},
        ]
    if post_match:
        pipeline.append({"$match": post_match})

    pipeline.append({"$sort": {"timestamp": DESCENDING}})

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
    rows = list(coll.aggregate(pipeline))
    return rows, total


def get_eligibility_history(
    device_id: ObjectId,
    interface: str,
    *,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict], int]:
    """Return append-only history for one interface (newest first)."""
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


# Silence unused import when callers raise EligibilityDisabledError externally.
_ = EligibilityDisabledError
