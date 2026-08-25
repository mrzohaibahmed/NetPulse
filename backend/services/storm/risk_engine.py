"""
Advanced Risk Score Engine
==========================
Estimates the probability that an eligible interface is experiencing a
Layer-2 network storm.

Responsibility
--------------
Risk calculation only — no SSH, SNMP, confirmation, or mitigation.

Public API
----------
    from services.storm.risk_engine import calculate_risk
    result = calculate_risk(device_id, interface, eligible=True)

Consumes MongoDB only:
- Eligibility result (caller-supplied or latest stored)
- Interface statistics history
- Interface metadata
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING

from services.storm.aggregator import aggregate_analyzer_results
from services.storm.analyzers.broadcast import BroadcastAnalyzer
from services.storm.analyzers.crc import CrcAnalyzer
from services.storm.analyzers.discards import DiscardAnalyzer
from services.storm.analyzers.errors import ErrorAnalyzer
from services.storm.analyzers.multicast import MulticastAnalyzer
from services.storm.analyzers.unknown_unicast import UnknownUnicastAnalyzer
from services.storm.analyzers.utilization import UtilizationAnalyzer
from services.storm.history import load_stats_pair
from services.storm.models import RiskScoreResult, create_risk_document
from services.settings_service import get_storm_risk_threshold
from services.storm.source_classification import classify_storm_source
from services.storm.thresholds import RiskConfig, get_risk_config
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.risk")

COLLECTION = "storm_risk_history"

DEFAULT_ANALYZERS = (
    BroadcastAnalyzer(),
    MulticastAnalyzer(),
    UnknownUnicastAnalyzer(),
    UtilizationAnalyzer(),
    ErrorAnalyzer(),
    DiscardAnalyzer(),
    CrcAnalyzer(),
)


def _db():
    from config.database import db  # noqa: PLC0415

    return db


class RiskScoreEngine:
    """
    Stateless risk calculator (SOLID).

    - SRP: risk scoring only
    - OCP: analyzers are injectable
    - DIP: depends on RiskConfig + analyzer protocol
    """

    def __init__(
        self,
        config: Optional[RiskConfig] = None,
        analyzers=None,
    ) -> None:
        self._config = config or get_risk_config()
        self._analyzers = tuple(analyzers) if analyzers is not None else DEFAULT_ANALYZERS

    @property
    def config(self) -> RiskConfig:
        return self._config

    def calculate(
        self,
        *,
        device_id: Any,
        interface: str,
        eligible: bool = True,
        current_stats: Optional[dict] = None,
        previous_stats: Optional[dict] = None,
        hostname: Optional[str] = None,
        ip_address: Optional[str] = None,
        persist: bool = False,
        cycle_id: Optional[str] = None,
        interface_context: Optional[dict[str, Any]] = None,
        stats_loaded: bool = False,
    ) -> RiskScoreResult:
        """
        Calculate risk for one interface.

        When ``current_stats`` is omitted, the two newest Mongo samples
        are loaded. When ``eligible`` is False, returns a zero-risk result
        without running analyzers.
        """
        started = time.monotonic()
        now = datetime.now(timezone.utc)
        name = str(interface or "").strip()
        device_key = str(device_id) if device_id is not None else None

        if not self._config.enable_risk:
            result = RiskScoreResult(
                risk_score=0.0,
                severity="LOW",
                confidence=0.0,
                contributors=[],
                eligible=eligible,
                timestamp=now,
                device_id=device_key,
                interface=name or None,
                skipped_reason="Risk scoring disabled",
            )
            self._log(result, started)
            return result

        if not eligible:
            result = RiskScoreResult(
                risk_score=0.0,
                severity="LOW",
                confidence=100.0,
                contributors=[],
                eligible=False,
                timestamp=now,
                device_id=device_key,
                interface=name or None,
                skipped_reason="Interface not eligible",
            )
            if persist:
                self._store(
                    device_id, name, result, hostname, ip_address, cycle_id=cycle_id
                )
            self._log(result, started)
            return result

        if not name:
            result = RiskScoreResult(
                risk_score=0.0,
                severity="LOW",
                confidence=0.0,
                contributors=[],
                eligible=True,
                timestamp=now,
                device_id=device_key,
                interface=None,
                skipped_reason="Missing interface name",
            )
            self._log(result, started)
            return result

        interface_context = interface_context if interface_context is not None else (
            self._load_interface_context(device_id, name)
        )

        current = current_stats
        previous = previous_stats
        if current is None and not stats_loaded:
            current, previous = load_stats_pair(
                device_id, name, cycle_id=cycle_id
            )

        if current is None:
            result = RiskScoreResult(
                risk_score=0.0,
                severity="LOW",
                confidence=0.0,
                contributors=[],
                eligible=True,
                timestamp=now,
                device_id=device_key,
                interface=name,
                skipped_reason="Missing statistics history",
            )
            if persist:
                self._store(
                    device_id, name, result, hostname, ip_address, cycle_id=cycle_id
                )
            self._log(result, started)
            return result

        analyzer_outputs = [
            analyzer.analyze(
                current,
                previous,
                self._config,
                interface_context=interface_context,
            )
            for analyzer in self._analyzers
        ]
        result = aggregate_analyzer_results(
            analyzer_outputs,
            eligible=True,
            device_id=device_key,
            interface=name,
            timestamp=now,
        )

        source = classify_storm_source(
            current=current,
            previous=previous,
            interface_context=interface_context,
            risk_score=result.risk_score,
            min_risk_for_analysis=get_storm_risk_threshold(),
        )
        result.source_classification = source.get("sourceClassification")
        result.source_confidence = float(source.get("sourceConfidence") or 0.0)
        result.source_rationale = source.get("sourceRationale")
        if source.get("sourceMetrics"):
            result.raw_metrics["sourceAnalysis"] = source["sourceMetrics"]

        if persist:
            self._store(
                device_id,
                name,
                result,
                hostname or current.get("hostname"),
                ip_address or current.get("ipAddress"),
                cycle_id=cycle_id,
            )

        self._log(result, started)
        return result

    def _store(
        self,
        device_id,
        interface: str,
        result: RiskScoreResult,
        hostname: Optional[str],
        ip_address: Optional[str],
        *,
        cycle_id: Optional[str] = None,
    ) -> None:
        try:
            oid = device_id
            if isinstance(oid, str) and ObjectId.is_valid(oid):
                oid = ObjectId(oid)
            document = create_risk_document(
                device_id=oid,
                interface=interface,
                result=result,
                hostname=hostname,
                ip_address=ip_address,
                cycle_id=cycle_id,
            )
            inserted = _db()[COLLECTION].insert_one(document)
            document["_id"] = inserted.inserted_id
            try:
                from services.storm.risk_latest import (  # noqa: PLC0415
                    risk_latest_enabled,
                    upsert_risk_latest_from_history_doc,
                )

                if risk_latest_enabled():
                    upsert_risk_latest_from_history_doc(document)
            except Exception as proj_exc:  # noqa: BLE001
                logger.warning(
                    "[RISK] Latest projection update failed (history kept): %s",
                    proj_exc,
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("[RISK] Failed to store risk history: %s", exc)

    @staticmethod
    def _load_interface_context(device_id: Any, interface: str) -> Optional[dict[str, Any]]:
        """Load port mode / topology fields for directional analyzers."""
        try:
            oid = device_id
            if isinstance(oid, str) and ObjectId.is_valid(oid):
                oid = ObjectId(oid)
            row = _db().interfaces.find_one(
                {"deviceId": oid, "name": interface},
                {
                    "portMode": 1,
                    "mode": 1,
                    "isAccess": 1,
                    "isTrunk": 1,
                    "isUplink": 1,
                    "isInfrastructure": 1,
                    "neighbor": 1,
                },
            )
            return row
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RISK] Interface context unavailable for %s: %s", interface, exc)
            return None

    @staticmethod
    def _log(result: RiskScoreResult, started: float) -> None:
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        top = result.contributors[0]["metric"] if result.contributors else "none"
        logger.info(
            "Risk Calculated | %s | score=%s | severity=%s | top=%s | %sms",
            result.interface or "unknown",
            result.risk_score,
            result.severity,
            top,
            elapsed_ms,
        )


_engine: Optional[RiskScoreEngine] = None


def get_risk_engine(
    config: Optional[RiskConfig] = None,
    *,
    force_new: bool = False,
) -> RiskScoreEngine:
    global _engine
    if force_new or _engine is None or config is not None:
        _engine = RiskScoreEngine(config=config)
    return _engine


def calculate_risk(
    device_id,
    interface: str,
    *,
    eligible: bool = True,
    current_stats: Optional[dict] = None,
    previous_stats: Optional[dict] = None,
    hostname: Optional[str] = None,
    ip_address: Optional[str] = None,
    persist: bool = False,
    cycle_id: Optional[str] = None,
    interface_context: Optional[dict[str, Any]] = None,
) -> RiskScoreResult:
    """
    Public entry-point for future Storm modules::

        result = calculate_risk(device_id, interface, eligible=True)
    """
    return get_risk_engine().calculate(
        device_id=device_id,
        interface=interface,
        eligible=eligible,
        current_stats=current_stats,
        previous_stats=previous_stats,
        hostname=hostname,
        ip_address=ip_address,
        persist=persist,
        cycle_id=cycle_id,
        interface_context=interface_context,
    )


def ensure_risk_indexes() -> None:
    try:
        coll = _db()[COLLECTION]
        coll.create_index(
            [
                ("deviceId", ASCENDING),
                ("interface", ASCENDING),
                ("timestamp", DESCENDING),
            ],
            name="idx_risk_device_iface_ts",
        )
        coll.create_index(
            [("timestamp", DESCENDING)],
            name="idx_risk_timestamp",
        )
        coll.create_index(
            [("severity", ASCENDING), ("timestamp", DESCENDING)],
            name="idx_risk_severity_ts",
        )
        coll.create_index(
            [("cycleId", ASCENDING)],
            name="idx_risk_cycle",
        )
        logger.info("[RISK] MongoDB indexes ensured on %s", COLLECTION)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RISK] Failed to ensure indexes: %s", exc)


def _latest_eligibility_map(*, cycle_id: Optional[str] = None) -> dict[tuple[str, str], bool]:
    """
    Map (deviceId, interface) → eligible from eligibility_results.

    When ``cycle_id`` is provided (Job B), prefer that cycle's rows — avoids
    a full-history ``$sort``+``$group`` scan. Falls back to latest-per-iface
    aggregate when cycle rows are missing.
    """
    mapping: dict[tuple[str, str], bool] = {}
    try:
        if cycle_id:
            for row in _db().eligibility_results.find({"cycleId": str(cycle_id)}):
                device_id = row.get("deviceId")
                iface = row.get("interface")
                if device_id is None or not iface:
                    continue
                mapping[(str(device_id), str(iface))] = bool(row.get("eligible"))
            if mapping:
                return mapping
            logger.warning(
                "[RISK] No eligibility rows for cycleId=%s — falling back to latest map",
                cycle_id,
            )

        pipeline = [
            {"$sort": {"timestamp": DESCENDING}},
            {
                "$group": {
                    "_id": {
                        "deviceId": "$deviceId",
                        "interface": "$interface",
                    },
                    "eligible": {"$first": "$eligible"},
                }
            },
        ]
        for row in _db().eligibility_results.aggregate(pipeline, allowDiskUse=True):
            key = row.get("_id") or {}
            device_id = key.get("deviceId")
            iface = key.get("interface")
            if device_id is None or not iface:
                continue
            mapping[(str(device_id), str(iface))] = bool(row.get("eligible"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RISK] Failed to load eligibility map: %s", exc)
    return mapping


def _bulk_interface_context_map() -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        cursor = _db().interfaces.find(
            {},
            {
                "deviceId": 1,
                "name": 1,
                "portMode": 1,
                "mode": 1,
                "isAccess": 1,
                "isTrunk": 1,
                "isUplink": 1,
                "isInfrastructure": 1,
                "neighbor": 1,
            },
        )
        for row in cursor:
            device_id = row.get("deviceId")
            name = row.get("name")
            if device_id is None or not name:
                continue
            out[(str(device_id), str(name))] = row
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RISK] Failed to load interface context map: %s", exc)
    return out


def _bulk_stats_pairs_map(
    *,
    cycle_id: Optional[str] = None,
) -> dict[tuple[str, str], tuple[Optional[dict], Optional[dict]]]:
    """
    Prefetch (current, previous) stats pairs for all interfaces.

    Matches ``load_stats_pair`` semantics, including cycle binding.
    """
    from services.storm.history import bulk_load_stats_pairs  # noqa: PLC0415

    return bulk_load_stats_pairs(cycle_id=cycle_id)


def calculate_all_risks(*, cycle_id: Optional[str] = None) -> dict[str, Any]:
    """
    Score every interface that has a latest eligibility result.

    Safe for APScheduler — never raises. Stops after risk storage;
    does not invoke confirmation/mitigation.
    Optional ``cycle_id`` stamps risk rows and binds stats pair lookups.
    """
    config = get_risk_config()
    if not config.enable_risk:
        logger.info("[RISK] Skipped — enableRisk=false")
        return {
            "total": 0,
            "scored": 0,
            "skipped": 0,
            "errors": 0,
            "disabled": True,
        }

    logger.info("[RISK] Bulk risk calculation started | cycleId=%s", cycle_id or "-")
    started = time.monotonic()
    eligibility = _latest_eligibility_map(cycle_id=cycle_id)
    context_map = _bulk_interface_context_map()
    stats_map = _bulk_stats_pairs_map(cycle_id=cycle_id)

    total = 0
    scored = 0
    skipped = 0
    errors = 0
    engine = get_risk_engine(force_new=True)
    documents: list[dict[str, Any]] = []

    # Prefer interfaces inventory so we still score eligible ports even if
    # eligibility map is empty after a fresh install (treat missing as False).
    try:
        cursor = _db().interfaces.find(
            {},
            {
                "deviceId": 1,
                "name": 1,
                "hostname": 1,
                "ipAddress": 1,
            },
        )
        for iface in cursor:
            total += 1
            device_id = iface.get("deviceId")
            name = iface.get("name")
            if device_id is None or not name:
                skipped += 1
                continue
            map_key = (str(device_id), str(name))
            eligible = eligibility.get(map_key, False)
            current_stats, previous_stats = stats_map.get(map_key, (None, None))
            try:
                result = engine.calculate(
                    device_id=device_id,
                    interface=name,
                    eligible=eligible,
                    current_stats=current_stats if eligible else None,
                    previous_stats=previous_stats if eligible else None,
                    hostname=iface.get("hostname"),
                    ip_address=iface.get("ipAddress"),
                    persist=False,
                    cycle_id=cycle_id,
                    interface_context=context_map.get(map_key),
                    stats_loaded=bool(eligible),
                )
                oid = device_id
                if isinstance(oid, str) and ObjectId.is_valid(oid):
                    oid = ObjectId(oid)
                documents.append(
                    create_risk_document(
                        device_id=oid,
                        interface=name,
                        result=result,
                        hostname=iface.get("hostname"),
                        ip_address=iface.get("ipAddress"),
                        cycle_id=cycle_id,
                    )
                )
                if eligible:
                    scored += 1
                    try:
                        from services.alert_service import maybe_send_high_risk_alert
                        if result.risk_score >= 60.0:
                            maybe_send_high_risk_alert(
                                device_id=oid,
                                interface=name,
                                risk_score=result.risk_score,
                                hostname=iface.get("hostname") or "Unknown",
                                ip_address=iface.get("ipAddress") or "Unknown"
                            )
                    except Exception as err:
                        logger.warning("[RISK] Failed to evaluate high risk alert: %s", err)
                else:
                    skipped += 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.error(
                    "[RISK] Failed scoring %s: %s",
                    name,
                    exc,
                )

        if documents:
            result = _db()[COLLECTION].insert_many(documents, ordered=False)
            # Attach inserted ids for projection mirroring.
            for doc, inserted_id in zip(documents, result.inserted_ids):
                doc["_id"] = inserted_id
            try:
                from services.storm.risk_latest import (  # noqa: PLC0415
                    risk_latest_enabled,
                    upsert_risk_latest_many,
                )

                if risk_latest_enabled():
                    upsert_risk_latest_many(documents)
            except Exception as proj_exc:  # noqa: BLE001
                logger.warning(
                    "[RISK] Latest projection batch update failed (history kept): %s",
                    proj_exc,
                )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[RISK] Bulk calculation aborted: %s", exc)
        return {
            "total": total,
            "scored": scored,
            "skipped": skipped,
            "errors": errors + 1,
            "disabled": False,
        }

    elapsed = round(time.monotonic() - started, 2)
    logger.info(
        "[RISK] Bulk complete | total=%s scored=%s skipped=%s errors=%s "
        "elapsed=%.2fs | cycleId=%s",
        total,
        scored,
        skipped,
        errors,
        elapsed,
        cycle_id or "-",
    )
    return {
        "total": total,
        "scored": scored,
        "skipped": skipped,
        "errors": errors,
        "disabled": False,
    }


def get_latest_risk_results(
    device_id: Optional[ObjectId] = None,
    interface: Optional[str] = None,
    *,
    severity: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict], int]:
    """Latest risk document per (deviceId, interface)."""
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
    if severity:
        post["severity"] = severity.upper()
    if search:
        from utils.mongo_safe import regex_filter  # noqa: PLC0415
        regex = regex_filter(search)
        post["$or"] = [
            {"interface": regex},
            {"hostname": regex},
            {"ipAddress": regex},
            {"severity": regex},
        ]
    if post:
        pipeline.append({"$match": post})

    pipeline.append({"$sort": {"riskScore": DESCENDING, "timestamp": DESCENDING}})

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


def get_risk_history(
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
