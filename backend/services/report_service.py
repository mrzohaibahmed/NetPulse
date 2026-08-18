"""
Bounded MongoDB aggregations for the Reports module.

Does not change monitoring, storm scoring, or dispatcher behavior.
All historical queries require an explicit time window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any

from bson import ObjectId

from services.ping_service import (
    STATUS_NOT_REACHABLE,
    STATUS_OFFLINE_CRITICAL,
    STATUS_ONLINE,
)
from services.report_period import timestamp_match
from utils.serializers import format_datetime, get_device_type
from utils.utc import ensure_utc

UNREACHABLE_STATUSES = (
    STATUS_NOT_REACHABLE,
    STATUS_OFFLINE_CRITICAL,
    "Offline",
)
STORM_CLOSED_STATUSES = ("RESOLVED", "CANCELLED", "CLOSED", "RECOVERED")
STALE_INTERVAL_MULTIPLIER = 2
MIN_STALE_SECONDS = 120
EXPORT_MAX = 5000
TABLE_MAX = 100
DETAIL_RELATED_LIMIT = 50
HIGH_RISK_SEVERITIES = ("HIGH", "CRITICAL")
DEVICE_OFFLINE_ALERT_TYPES = ("Device Offline",)
STORM_ALERT_TYPES = ("Storm Protection",)

RTT_BOUNDARIES = [
    0, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000,
]

AVAILABILITY_LIMITATIONS = [
    "Probe Success Ratio is online_scans / total_scans in pingHistory. "
    "It is not time-based availability and is not an SLA.",
    "A failed probe is not a confirmed outage. Device status uses consecutive-failure hysteresis.",
    "Confirmed outage events are limited to critical-device offline alerts. "
    "Non-critical failures do not create alerts.",
    "Time-based availability, SLA, MTTR, MTTD, and MTBF are not available "
    "from current monitoring data.",
]

PERFORMANCE_LIMITATIONS = [
    "Successful ICMP Scan RTT uses the final scan result after retries are collapsed. "
    "It is not an end-user latency SLA.",
    "Packet loss is not available because per-attempt ICMP results are not stored.",
    "Interface utilization is shown only for samples with a valid speed and computed utilization.",
    "Bits/sec, packets/sec, and CRC rates are not persisted as time series and are omitted.",
]


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _oid(value) -> ObjectId | None:
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    return None


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round((numerator / denominator) * 100.0, 2)


def _round(value: Any, digits: int = 2) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _is_missing_hostname(hostname: Any) -> bool:
    text = str(hostname or "").strip()
    return text == "" or text.lower() == "unknown"


def _as_utc(value: Any) -> datetime | None:
    """Coerce Mongo timestamps to aware UTC. Naive values are treated as UTC."""
    if not isinstance(value, datetime):
        return None
    return ensure_utc(value)


def _ping_interval_seconds() -> int:
    try:
        from services.settings_service import get_settings  # noqa: PLC0415

        settings = get_settings() or {}
        return max(int(settings.get("pingInterval") or 60), 5)
    except Exception:  # noqa: BLE001
        return 60


def _stale_cutoff(now: datetime) -> datetime:
    interval = _ping_interval_seconds()
    seconds = max(interval * STALE_INTERVAL_MULTIPLIER, MIN_STALE_SECONDS)
    return now - timedelta(seconds=seconds)


def period_payload(window: dict[str, Any]) -> dict[str, Any]:
    return {
        "period": window["period"],
        "label": window["label"],
        "start": format_datetime(window["start"]),
        "end": format_datetime(window["end"]),
    }


def parse_page(page: Any, limit: Any, *, default_limit: int = 25) -> tuple[int, int]:
    try:
        page_n = int(page or 1)
    except (TypeError, ValueError):
        page_n = 1
    try:
        limit_n = int(limit or default_limit)
    except (TypeError, ValueError):
        limit_n = default_limit
    page_n = max(page_n, 1)
    limit_n = min(max(limit_n, 1), TABLE_MAX)
    return page_n, limit_n


def paginate(rows: list, page: int, limit: int) -> tuple[list, dict[str, int]]:
    total = len(rows)
    total_pages = ceil(total / limit) if total else 0
    if total_pages and page > total_pages:
        page = total_pages
    skip = (page - 1) * limit if total_pages else 0
    return rows[skip : skip + limit], {
        "page": page,
        "limit": limit,
        "total": total,
        "totalPages": total_pages,
    }


def _device_query(
    *,
    device_id: str | None = None,
    device_type: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if device_id:
        oid = _oid(device_id)
        if oid is None:
            raise ValueError("Invalid deviceId")
        query["_id"] = oid
    dtype = (device_type or "").strip()
    if dtype and dtype.lower() != "all":
        query["$or"] = [{"deviceType": dtype}, {"type": dtype}]
    st = (status or "").strip()
    if st and st.lower() != "all":
        query["status"] = st
    return query


_DEVICE_PROJECTION = {
    "_id": 1,
    "hostname": 1,
    "ipAddress": 1,
    "deviceType": 1,
    "type": 1,
    "status": 1,
    "monitor": 1,
    "critical": 1,
    "lastSeen": 1,
    "lastCheckedAt": 1,
    "consecutiveFailures": 1,
    "responseTime": 1,
}


def _load_devices(query: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return list(
        _db()
        .devices.find(query or {}, _DEVICE_PROJECTION)
        .sort("hostname", 1)
        .limit(2000)
    )


def _device_ids(devices: list[dict[str, Any]]) -> list[ObjectId]:
    return [d["_id"] for d in devices]


def _serialize_device_brief(device: dict[str, Any]) -> dict[str, Any]:
    return {
        "deviceId": str(device["_id"]),
        "hostname": device.get("hostname") or "Unknown",
        "ipAddress": device.get("ipAddress"),
        "deviceType": get_device_type(device),
        "status": device.get("status") or "Unknown",
        "monitor": bool(device.get("monitor", True)),
        "critical": bool(device.get("critical", False)),
        "lastSeen": format_datetime(device.get("lastSeen")),
        "lastCheckedAt": format_datetime(device.get("lastCheckedAt")),
        "consecutiveFailures": int(device.get("consecutiveFailures") or 0),
        "responseTime": device.get("responseTime"),
    }


def _ping_match(
    start: datetime,
    end: datetime,
    device_oids: list[ObjectId] | None = None,
) -> dict[str, Any]:
    match = timestamp_match(start, end, "timestamp")
    if device_oids is not None:
        match["deviceId"] = {"$in": device_oids}
    return match


def _trend_format(start: datetime, end: datetime) -> str:
    hours = max((end - start).total_seconds() / 3600.0, 0)
    if hours <= 48:
        return "%Y-%m-%d %H:00"
    return "%Y-%m-%d"


def _percentile_from_histogram(
    buckets: list[dict[str, Any]],
    total: int,
    pct: float,
) -> float | None:
    if total <= 0 or not buckets:
        return None
    target = (pct / 100.0) * total
    cumulative = 0
    for bucket in buckets:
        count = int(bucket.get("count") or 0)
        lo = float(bucket["lo"])
        hi = float(bucket["hi"])
        previous = cumulative
        cumulative += count
        if count <= 0:
            continue
        if cumulative >= target:
            frac = (target - previous) / count
            return round(lo + (hi - lo) * frac, 2)
    last = buckets[-1]
    return round(float(last["hi"]), 2)


def _normalize_rtt_buckets(raw_buckets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    overflow_count = 0
    for item in raw_buckets:
        key = item.get("_id")
        count = int(item.get("count") or 0)
        if key == "overflow":
            overflow_count += count
            continue
        try:
            lo = float(key)
        except (TypeError, ValueError):
            continue
        try:
            idx = RTT_BOUNDARIES.index(int(lo) if lo == int(lo) else lo)
        except ValueError:
            idx = -1
        if 0 <= idx < len(RTT_BOUNDARIES) - 1:
            hi = float(RTT_BOUNDARIES[idx + 1])
        else:
            hi = lo + 1
        normalized.append({"lo": lo, "hi": hi, "count": count})
    if overflow_count:
        normalized.append(
            {"lo": float(RTT_BOUNDARIES[-1]), "hi": float(RTT_BOUNDARIES[-1]) * 1.5, "count": overflow_count}
        )
    normalized.sort(key=lambda row: row["lo"])
    return normalized


def _rtt_stats(match: dict[str, Any], start: datetime, end: datetime) -> dict[str, Any]:
    empty = {
        "successfulScans": 0,
        "failedScans": 0,
        "averageRttMs": None,
        "minRttMs": None,
        "maxRttMs": None,
        "p50RttMs": None,
        "p95RttMs": None,
        "p99RttMs": None,
        "trend": [],
        "topDevices": [],
    }
    pipeline = [
        {"$match": match},
        {
            "$facet": {
                "totals": [
                    {
                        "$group": {
                            "_id": None,
                            "successfulScans": {
                                "$sum": {
                                    "$cond": [{"$ne": ["$responseTime", None]}, 1, 0]
                                }
                            },
                            "failedScans": {
                                "$sum": {
                                    "$cond": [{"$eq": ["$responseTime", None]}, 1, 0]
                                }
                            },
                            "averageRttMs": {"$avg": "$responseTime"},
                            "minRttMs": {"$min": "$responseTime"},
                            "maxRttMs": {"$max": "$responseTime"},
                        }
                    }
                ],
                "histogram": [
                    {"$match": {"responseTime": {"$ne": None, "$gte": 0}}},
                    {
                        "$bucket": {
                            "groupBy": "$responseTime",
                            "boundaries": RTT_BOUNDARIES,
                            "default": "overflow",
                            "output": {"count": {"$sum": 1}},
                        }
                    },
                ],
                "trend": [
                    {
                        "$group": {
                            "_id": {
                                "$dateToString": {
                                    "format": _trend_format(start, end),
                                    "date": "$timestamp",
                                }
                            },
                            "successfulScans": {
                                "$sum": {
                                    "$cond": [{"$ne": ["$responseTime", None]}, 1, 0]
                                }
                            },
                            "failedScans": {
                                "$sum": {
                                    "$cond": [{"$eq": ["$responseTime", None]}, 1, 0]
                                }
                            },
                            "averageRttMs": {"$avg": "$responseTime"},
                        }
                    },
                    {"$sort": {"_id": 1}},
                ],
                "topDevices": [
                    {"$match": {"responseTime": {"$ne": None}}},
                    {
                        "$group": {
                            "_id": "$deviceId",
                            "averageRttMs": {"$avg": "$responseTime"},
                            "maxRttMs": {"$max": "$responseTime"},
                            "successfulScans": {"$sum": 1},
                            "hostname": {"$last": "$hostname"},
                            "ipAddress": {"$last": "$ipAddress"},
                        }
                    },
                    {"$sort": {"averageRttMs": -1}},
                    {"$limit": 10},
                ],
            }
        },
    ]
    row = next(_db().pingHistory.aggregate(pipeline, allowDiskUse=True), None)
    if not row:
        return empty
    totals = (row.get("totals") or [{}])[0]
    successful = int(totals.get("successfulScans") or 0)
    buckets = _normalize_rtt_buckets(row.get("histogram") or [])
    trend = [
        {
            "bucket": item.get("_id"),
            "successfulScans": int(item.get("successfulScans") or 0),
            "failedScans": int(item.get("failedScans") or 0),
            "averageRttMs": _round(item.get("averageRttMs")),
        }
        for item in (row.get("trend") or [])
        if item.get("_id")
    ]
    top_devices = [
        {
            "deviceId": str(item["_id"]) if item.get("_id") is not None else None,
            "hostname": item.get("hostname") or "Unknown",
            "ipAddress": item.get("ipAddress"),
            "averageRttMs": _round(item.get("averageRttMs")),
            "maxRttMs": _round(item.get("maxRttMs")),
            "successfulScans": int(item.get("successfulScans") or 0),
        }
        for item in (row.get("topDevices") or [])
    ]
    return {
        "successfulScans": successful,
        "failedScans": int(totals.get("failedScans") or 0),
        "averageRttMs": _round(totals.get("averageRttMs")),
        "minRttMs": _round(totals.get("minRttMs")),
        "maxRttMs": _round(totals.get("maxRttMs")),
        "p50RttMs": _percentile_from_histogram(buckets, successful, 50),
        "p95RttMs": _percentile_from_histogram(buckets, successful, 95),
        "p99RttMs": _percentile_from_histogram(buckets, successful, 99),
        "trend": trend,
        "topDevices": top_devices,
    }


def _probe_ratio_by_device(match: dict[str, Any]) -> dict[str, dict[str, Any]]:
    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": "$deviceId",
                "totalChecks": {"$sum": 1},
                "onlineChecks": {
                    "$sum": {"$cond": [{"$eq": ["$status", STATUS_ONLINE]}, 1, 0]}
                },
                "firstCheckAt": {"$min": "$timestamp"},
                "lastCheckAt": {"$max": "$timestamp"},
                "firstFailedProbeAt": {
                    "$min": {
                        "$cond": [
                            {"$ne": ["$status", STATUS_ONLINE]},
                            "$timestamp",
                            None,
                        ]
                    }
                },
                "hostname": {"$last": "$hostname"},
                "ipAddress": {"$last": "$ipAddress"},
            }
        },
    ]
    out: dict[str, dict[str, Any]] = {}
    for item in _db().pingHistory.aggregate(pipeline, allowDiskUse=True):
        key = str(item["_id"]) if item.get("_id") is not None else ""
        if not key:
            continue
        total = int(item.get("totalChecks") or 0)
        online = int(item.get("onlineChecks") or 0)
        out[key] = {
            "totalChecks": total,
            "onlineChecks": online,
            "failedChecks": max(total - online, 0),
            "probeSuccessRatio": _pct(online, total),
            "firstCheckAt": format_datetime(item.get("firstCheckAt")),
            "lastCheckAt": format_datetime(item.get("lastCheckAt")),
            "firstFailedProbeAt": format_datetime(item.get("firstFailedProbeAt")),
            "hostname": item.get("hostname"),
            "ipAddress": item.get("ipAddress"),
        }
    return out


def _fleet_probe_ratio(match: dict[str, Any]) -> dict[str, Any]:
    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": None,
                "totalChecks": {"$sum": 1},
                "onlineChecks": {
                    "$sum": {"$cond": [{"$eq": ["$status", STATUS_ONLINE]}, 1, 0]}
                },
            }
        },
    ]
    row = next(_db().pingHistory.aggregate(pipeline, allowDiskUse=True), None) or {}
    total = int(row.get("totalChecks") or 0)
    online = int(row.get("onlineChecks") or 0)
    return {
        "totalChecks": total,
        "onlineChecks": online,
        "failedChecks": max(total - online, 0),
        "probeSuccessRatio": _pct(online, total),
    }


def _duration_seconds(start, end) -> int | None:
    start_dt = _as_utc(start)
    end_dt = _as_utc(end)
    if start_dt is None or end_dt is None:
        return None
    try:
        delta = (end_dt - start_dt).total_seconds()
    except (TypeError, ValueError):
        return None
    if delta < 0:
        return None
    return int(delta)


def _current_issue_duration(device: dict[str, Any], now: datetime) -> dict[str, Any]:
    status = device.get("status") or "Unknown"
    if status not in UNREACHABLE_STATUSES:
        return {
            "currentlyUnreachable": False,
            "timeSinceLastSuccessfulPingSeconds": None,
            "timeSinceLastSuccessfulPingLabel": None,
        }
    last_seen = device.get("lastSeen")
    seconds = _duration_seconds(last_seen, now)
    if seconds is None:
        return {
            "currentlyUnreachable": True,
            "timeSinceLastSuccessfulPingSeconds": None,
            "timeSinceLastSuccessfulPingLabel": "Insufficient data",
        }
    return {
        "currentlyUnreachable": True,
        "timeSinceLastSuccessfulPingSeconds": seconds,
        "timeSinceLastSuccessfulPingLabel": _format_duration(seconds),
    }


def _format_duration(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    rem_m = minutes % 60
    if hours < 48:
        return f"{hours}h {rem_m}m" if rem_m else f"{hours}h"
    days = hours // 24
    rem_h = hours % 24
    return f"{days}d {rem_h}h" if rem_h else f"{days}d"


# ---------------------------------------------------------------------------
# Filter options
# ---------------------------------------------------------------------------


def list_filter_options(device_id: str | None = None) -> dict[str, Any]:
    devices = [
        {
            "deviceId": str(d["_id"]),
            "hostname": d.get("hostname") or "Unknown",
            "ipAddress": d.get("ipAddress"),
            "deviceType": get_device_type(d),
            "status": d.get("status") or "Unknown",
        }
        for d in _load_devices()
    ]
    interfaces: list[str] = []
    oid = _oid(device_id) if device_id else None
    if oid is not None:
        interfaces = sorted(
            {
                str(row.get("name") or "").strip()
                for row in _db().interfaces.find(
                    {"deviceId": oid},
                    {"name": 1},
                ).limit(1000)
                if str(row.get("name") or "").strip()
            }
        )
    return {"devices": devices, "interfaces": interfaces}


# ---------------------------------------------------------------------------
# Report 1 — Executive
# ---------------------------------------------------------------------------


def _scoped(filters: dict[str, Any]) -> bool:
    dtype = (filters.get("device_type") or "").strip().lower()
    status = (filters.get("status") or "").strip().lower()
    return bool(
        filters.get("device_id")
        or (dtype and dtype != "all")
        or (status and status != "all")
    )


def build_executive_report(window: dict[str, Any], filters: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    devices = _load_devices(_device_query(**filters))
    device_oids = _device_ids(devices)
    scoped = _scoped(filters)
    ping_match = _ping_match(
        window["start"], window["end"], device_oids if scoped else None
    )
    if scoped and not device_oids:
        ping_match = None

    total = len(devices)
    monitored = sum(1 for d in devices if d.get("monitor", True))
    online = sum(1 for d in devices if d.get("status") == STATUS_ONLINE)
    unreachable = sum(1 for d in devices if d.get("status") in UNREACHABLE_STATUSES)
    unknown = sum(1 for d in devices if d.get("status") not in (STATUS_ONLINE, *UNREACHABLE_STATUSES))
    stale_cutoff = _stale_cutoff(now)
    stale = 0
    missing_hostname = 0
    for device in devices:
        if _is_missing_hostname(device.get("hostname")):
            missing_hostname += 1
        if not device.get("monitor", True):
            continue
        checked = _as_utc(device.get("lastCheckedAt"))
        if checked is None or checked < stale_cutoff:
            stale += 1

    iface_query: dict[str, Any] = {
        "$or": [
            {"speedMbps": {"$in": [None, 0, ""]}},
            {"speedMbps": {"$exists": False}},
        ]
    }
    if device_oids and scoped:
        iface_query["deviceId"] = {"$in": device_oids}
    missing_speed = _db().interfaces.count_documents(iface_query)
    total_interfaces = _db().interfaces.count_documents(
        {"deviceId": {"$in": device_oids}} if device_oids and scoped else {}
    )

    alert_created = timestamp_match(window["start"], window["end"], "createdAt")
    if device_oids and scoped:
        alert_created["deviceId"] = {"$in": device_oids}
    critical_alerts_period = _db().alerts.count_documents(
        {
            **alert_created,
            "$or": [
                {"severity": "CRITICAL"},
                {"alertType": {"$in": list(DEVICE_OFFLINE_ALERT_TYPES)}},
            ],
        }
    )
    open_critical_alerts = _db().alerts.count_documents(
        {
            "severity": "CRITICAL",
            "resolved": {"$ne": True},
            "dismissed": {"$ne": True},
            **({"deviceId": {"$in": device_oids}} if device_oids and scoped else {}),
        }
    )

    incident_query: dict[str, Any] = {}
    if device_oids and scoped:
        incident_query["deviceId"] = {"$in": device_oids}
    open_incidents = _db().storm_incidents.count_documents(
        {
            **incident_query,
            "status": {"$nin": list(STORM_CLOSED_STATUSES)},
        }
    )
    period_incidents = _db().storm_incidents.count_documents(
        {**incident_query, **timestamp_match(window["start"], window["end"], "createdAt")}
    )

    risk_query: dict[str, Any] = {"severity": {"$in": list(HIGH_RISK_SEVERITIES)}}
    if device_oids and scoped:
        risk_query["deviceId"] = {"$in": device_oids}
    high_risk_count = _db().storm_risk_latest.count_documents(risk_query)
    high_risk_rows = [
        {
            "deviceId": str(row["deviceId"]) if row.get("deviceId") is not None else None,
            "hostname": row.get("hostname") or "Unknown",
            "interface": row.get("interface"),
            "riskScore": _round(row.get("riskScore")),
            "severity": row.get("severity") or "LOW",
        }
        for row in _db()
        .storm_risk_latest.find(risk_query)
        .sort("riskScore", -1)
        .limit(10)
    ]

    empty_probe = {
        "totalChecks": 0,
        "onlineChecks": 0,
        "failedChecks": 0,
        "probeSuccessRatio": None,
    }
    probe = _fleet_probe_ratio(ping_match) if ping_match is not None else empty_probe
    rtt = (
        _rtt_stats(ping_match, window["start"], window["end"])
        if ping_match is not None
        else {
            "successfulScans": 0,
            "failedScans": 0,
            "averageRttMs": None,
            "minRttMs": None,
            "maxRttMs": None,
            "p50RttMs": None,
            "p95RttMs": None,
            "p99RttMs": None,
            "trend": [],
            "topDevices": [],
        }
    )

    return {
        "success": True,
        "report": "executive",
        "period": period_payload(window),
        "snapshot": {
            "totalDevices": total,
            "monitoredDevices": monitored,
            "onlineDevices": online,
            "unreachableDevices": unreachable,
            "unknownDevices": unknown,
            "monitoringCoveragePercent": _pct(monitored, total),
            "openCriticalAlerts": open_critical_alerts,
            "openStormIncidents": open_incidents,
            "highCriticalRiskInterfaces": high_risk_count,
        },
        "periodMetrics": {
            "criticalAlertsCreated": critical_alerts_period,
            "stormIncidentsCreated": period_incidents,
            "probeSuccessRatio": probe["probeSuccessRatio"],
            "totalChecks": probe["totalChecks"],
            "onlineChecks": probe["onlineChecks"],
            "failedChecks": probe["failedChecks"],
            "successfulIcmpScanRtt": {
                "averageRttMs": rtt["averageRttMs"],
                "p50RttMs": rtt["p50RttMs"],
                "p95RttMs": rtt["p95RttMs"],
                "p99RttMs": rtt["p99RttMs"],
                "successfulScans": rtt["successfulScans"],
                "failedScans": rtt["failedScans"],
            },
        },
        "dataQuality": {
            "monitoredDevices": monitored,
            "totalDevices": total,
            "staleMonitoringDevices": stale,
            "staleThresholdSeconds": max(
                _ping_interval_seconds() * STALE_INTERVAL_MULTIPLIER, MIN_STALE_SECONDS
            ),
            "missingHostnameDevices": missing_hostname,
            "interfacesMissingSpeed": missing_speed,
            "totalInterfaces": total_interfaces,
        },
        "highRisk": high_risk_rows,
        "definitions": {
            "monitoringCoveragePercent": "Monitored devices / total devices (current inventory).",
            "probeSuccessRatio": "Online pingHistory rows / total pingHistory rows in the period. Not availability.",
            "successfulIcmpScanRtt": "RTT in milliseconds from successful final ICMP scans only.",
            "dataFreshness": "A monitored device is stale when lastCheckedAt is older than 2× ping interval.",
        },
        "limitations": AVAILABILITY_LIMITATIONS[:2] + PERFORMANCE_LIMITATIONS[:2],
    }


# ---------------------------------------------------------------------------
# Report 2 — Device availability & outage
# ---------------------------------------------------------------------------


def _confirmed_outages(
    start: datetime,
    end: datetime,
    device_oids: list[ObjectId] | None,
) -> dict[str, list[dict[str, Any]]]:
    query: dict[str, Any] = {
        **timestamp_match(start, end, "createdAt"),
        "$or": [
            {"alertType": {"$in": list(DEVICE_OFFLINE_ALERT_TYPES)}},
            {"category": "Device Monitoring", "status": STATUS_OFFLINE_CRITICAL},
        ],
    }
    if device_oids is not None:
        query["deviceId"] = {"$in": device_oids}
    grouped: dict[str, list[dict[str, Any]]] = {}
    cursor = (
        _db()
        .alerts.find(query)
        .sort("createdAt", 1)
        .limit(EXPORT_MAX)
    )
    now = datetime.now(timezone.utc)
    for alert in cursor:
        key = str(alert["deviceId"]) if alert.get("deviceId") is not None else ""
        if not key:
            continue
        created = alert.get("createdAt")
        resolved = alert.get("resolvedAt") if alert.get("resolved") else None
        seconds = _duration_seconds(created, resolved or now) if alert.get("resolved") else _duration_seconds(created, now)
        grouped.setdefault(key, []).append(
            {
                "alertId": str(alert["_id"]),
                "createdAt": format_datetime(created),
                "resolvedAt": format_datetime(resolved),
                "resolved": bool(alert.get("resolved")),
                "durationSeconds": seconds if alert.get("resolved") else None,
                "durationLabel": _format_duration(seconds) if alert.get("resolved") else None,
                "status": "resolved" if alert.get("resolved") else "open",
            }
        )
    return grouped


def build_availability_report(
    window: dict[str, Any],
    filters: dict[str, Any],
    *,
    page: int = 1,
    limit: int = 25,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    devices = _load_devices(_device_query(**filters))
    device_oids = _device_ids(devices)
    ping_match = _ping_match(window["start"], window["end"], device_oids)
    probe_map = _probe_ratio_by_device(ping_match) if device_oids else {}
    fleet = _fleet_probe_ratio(ping_match) if device_oids else {
        "totalChecks": 0,
        "onlineChecks": 0,
        "failedChecks": 0,
        "probeSuccessRatio": None,
    }
    outages = _confirmed_outages(window["start"], window["end"], device_oids or None)

    rows = []
    for device in devices:
        key = str(device["_id"])
        probe = probe_map.get(key) or {
            "totalChecks": 0,
            "onlineChecks": 0,
            "failedChecks": 0,
            "probeSuccessRatio": None,
            "firstFailedProbeAt": None,
            "lastCheckAt": None,
        }
        events = outages.get(key) or []
        issue = _current_issue_duration(device, now)
        rows.append(
            {
                **_serialize_device_brief(device),
                "firstFailedProbeAt": probe.get("firstFailedProbeAt"),
                "lastCheckInPeriod": probe.get("lastCheckAt"),
                "probeSuccessRatio": probe.get("probeSuccessRatio"),
                "totalChecks": probe.get("totalChecks") or 0,
                "onlineChecks": probe.get("onlineChecks") or 0,
                "failedChecks": probe.get("failedChecks") or 0,
                "confirmedOutageEvents": len(events),
                "confirmedOutages": events,
                **issue,
            }
        )

    page_rows, pagination = paginate(rows, page, limit)
    current_status = {
        "totalDevices": len(devices),
        "onlineDevices": sum(1 for d in devices if d.get("status") == STATUS_ONLINE),
        "unreachableDevices": sum(
            1 for d in devices if d.get("status") in UNREACHABLE_STATUSES
        ),
        "unknownDevices": sum(
            1
            for d in devices
            if d.get("status") not in (STATUS_ONLINE, *UNREACHABLE_STATUSES)
        ),
    }
    return {
        "success": True,
        "report": "availability",
        "period": period_payload(window),
        "currentStatus": current_status,
        "probeSuccess": fleet,
        "confirmedOutageEventCount": sum(len(v) for v in outages.values()),
        "devices": page_rows,
        "pagination": pagination,
        "definitions": {
            "probeSuccessRatio": "Online pingHistory rows / total pingHistory rows in the period.",
            "confirmedOutageEvents": "Critical-device offline alerts created in the period.",
            "timeSinceLastSuccessfulPing": "For currently unreachable devices, now − lastSeen when lastSeen exists.",
        },
        "limitations": AVAILABILITY_LIMITATIONS,
    }


# ---------------------------------------------------------------------------
# Report 3 — Network performance
# ---------------------------------------------------------------------------


def build_performance_report(
    window: dict[str, Any],
    filters: dict[str, Any],
    *,
    interface: str | None = None,
) -> dict[str, Any]:
    devices = _load_devices(
        _device_query(
            device_id=filters.get("device_id"),
            device_type=filters.get("device_type"),
        )
    )
    device_oids = _device_ids(devices)
    if (filters.get("device_id") or filters.get("device_type")) and not device_oids:
        rtt = {
            "successfulScans": 0,
            "failedScans": 0,
            "averageRttMs": None,
            "minRttMs": None,
            "maxRttMs": None,
            "p50RttMs": None,
            "p95RttMs": None,
            "p99RttMs": None,
            "trend": [],
            "topDevices": [],
        }
        return {
            "success": True,
            "report": "performance",
            "period": period_payload(window),
            "ping": rtt,
            "interfaces": {"validSamples": 0, "topUtilization": []},
            "definitions": {
                "successfulIcmpScanRtt": "Final successful ICMP scan RTT in milliseconds. Retries are collapsed.",
            },
            "limitations": PERFORMANCE_LIMITATIONS,
        }

    scoped = _scoped({k: v for k, v in filters.items() if k != "status"})
    ping_match = _ping_match(
        window["start"], window["end"], device_oids if scoped else None
    )
    rtt = _rtt_stats(ping_match, window["start"], window["end"])

    stats_match: dict[str, Any] = {
        **timestamp_match(window["start"], window["end"], "timestamp"),
        "utilization": {"$ne": None, "$gte": 0},
        "speedBps": {"$gt": 0},
    }
    if device_oids and scoped:
        stats_match["deviceId"] = {"$in": device_oids}
    iface = (interface or "").strip()
    if iface and iface.lower() != "all":
        stats_match["interfaceName"] = iface

    util_pipeline = [
        {"$match": stats_match},
        {
            "$group": {
                "_id": {
                    "deviceId": "$deviceId",
                    "interfaceName": "$interfaceName",
                },
                "hostname": {"$last": "$hostname"},
                "averageUtilization": {"$avg": "$utilization"},
                "peakUtilization": {"$max": "$utilization"},
                "validSamples": {"$sum": 1},
                "speedBps": {"$last": "$speedBps"},
            }
        },
        {"$match": {"validSamples": {"$gte": 1}}},
        {"$sort": {"peakUtilization": -1}},
        {"$limit": 15},
    ]
    top_util = []
    valid_total = 0
    try:
        count_row = next(
            _db().interface_stats.aggregate(
                [
                    {"$match": stats_match},
                    {"$count": "n"},
                ],
                allowDiskUse=True,
            ),
            {"n": 0},
        )
        valid_total = int(count_row.get("n") or 0)
        for item in _db().interface_stats.aggregate(util_pipeline, allowDiskUse=True):
            key = item.get("_id") or {}
            top_util.append(
                {
                    "deviceId": str(key.get("deviceId")) if key.get("deviceId") is not None else None,
                    "hostname": item.get("hostname") or "Unknown",
                    "interface": key.get("interfaceName"),
                    "averageUtilizationPercent": _round(item.get("averageUtilization"), 3),
                    "peakUtilizationPercent": _round(item.get("peakUtilization"), 3),
                    "validSamples": int(item.get("validSamples") or 0),
                    "speedBps": item.get("speedBps"),
                }
            )
    except Exception:  # noqa: BLE001
        valid_total = 0
        top_util = []

    return {
        "success": True,
        "report": "performance",
        "period": period_payload(window),
        "ping": rtt,
        "interfaces": {
            "validSamples": valid_total,
            "topUtilization": top_util,
        },
        "definitions": {
            "successfulIcmpScanRtt": "RTT from successful final ICMP scans only (milliseconds).",
            "p50P95P99": "Approximate percentiles from a bounded RTT histogram of successful scans.",
            "interfaceUtilization": "Stored utilization % for samples with speedBps > 0. Full-duplex max(rx, tx).",
        },
        "limitations": PERFORMANCE_LIMITATIONS,
        "unavailable": {
            "packetLossPercent": "Not available from current monitoring data",
            "bitsPerSecond": "Not persisted as a time series",
            "packetsPerSecond": "Not persisted as a time series",
            "crcRate": "CRC counters are not stored",
        },
    }


# ---------------------------------------------------------------------------
# Report 4 — Alerts & incidents
# ---------------------------------------------------------------------------


def _alert_is_open(alert: dict[str, Any]) -> bool:
    return not alert.get("resolved") and not alert.get("dismissed")


def build_alerts_incidents_report(
    window: dict[str, Any],
    filters: dict[str, Any],
    *,
    page: int = 1,
    limit: int = 25,
    severity: str | None = None,
    alert_type: str | None = None,
    alert_status: str | None = None,
) -> dict[str, Any]:
    devices = _load_devices(
        _device_query(
            device_id=filters.get("device_id"),
            device_type=filters.get("device_type"),
        )
    )
    device_oids = _device_ids(devices)
    empty_filter = bool(
        (filters.get("device_id") or filters.get("device_type")) and not device_oids
    )

    alert_match: dict[str, Any] = timestamp_match(window["start"], window["end"], "createdAt")
    if device_oids and (filters.get("device_id") or filters.get("device_type")):
        alert_match["deviceId"] = {"$in": device_oids}
    sev = (severity or "").strip()
    if sev and sev.lower() != "all":
        alert_match["severity"] = sev.upper()
    atype = (alert_type or "").strip()
    if atype and atype.lower() != "all":
        alert_match["alertType"] = atype
    ast = (alert_status or "").strip().lower()
    if ast == "open":
        alert_match["resolved"] = {"$ne": True}
        alert_match["dismissed"] = {"$ne": True}
    elif ast == "resolved":
        alert_match["resolved"] = True
    elif ast == "acknowledged":
        alert_match["acknowledged"] = True
    elif ast == "dismissed":
        alert_match["dismissed"] = True

    if empty_filter:
        alert_match["_id"] = {"$exists": False}

    facet = [
        {"$match": alert_match},
        {
            "$facet": {
                "totals": [
                    {
                        "$group": {
                            "_id": None,
                            "total": {"$sum": 1},
                            "critical": {
                                "$sum": {"$cond": [{"$eq": ["$severity", "CRITICAL"]}, 1, 0]}
                            },
                            "warning": {
                                "$sum": {"$cond": [{"$eq": ["$severity", "WARNING"]}, 1, 0]}
                            },
                            "info": {
                                "$sum": {"$cond": [{"$eq": ["$severity", "INFO"]}, 1, 0]}
                            },
                            "open": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {"$ne": ["$resolved", True]},
                                                {"$ne": ["$dismissed", True]},
                                            ]
                                        },
                                        1,
                                        0,
                                    ]
                                }
                            },
                            "resolved": {
                                "$sum": {"$cond": [{"$eq": ["$resolved", True]}, 1, 0]}
                            },
                            "acknowledged": {
                                "$sum": {"$cond": [{"$eq": ["$acknowledged", True]}, 1, 0]}
                            },
                        }
                    }
                ],
                "bySeverity": [
                    {"$group": {"_id": {"$ifNull": ["$severity", "UNKNOWN"]}, "count": {"$sum": 1}}},
                    {"$sort": {"count": -1}},
                ],
                "byType": [
                    {
                        "$group": {
                            "_id": {"$ifNull": ["$alertType", "Unknown"]},
                            "count": {"$sum": 1},
                        }
                    },
                    {"$sort": {"count": -1}},
                ],
                "trend": [
                    {
                        "$group": {
                            "_id": {
                                "$dateToString": {
                                    "format": _trend_format(window["start"], window["end"]),
                                    "date": "$createdAt",
                                }
                            },
                            "count": {"$sum": 1},
                            "critical": {
                                "$sum": {"$cond": [{"$eq": ["$severity", "CRITICAL"]}, 1, 0]}
                            },
                        }
                    },
                    {"$sort": {"_id": 1}},
                ],
                "topDevices": [
                    {"$match": {"deviceId": {"$ne": None}}},
                    {
                        "$group": {
                            "_id": "$deviceId",
                            "count": {"$sum": 1},
                            "hostname": {"$last": "$hostname"},
                            "ipAddress": {"$last": "$ipAddress"},
                        }
                    },
                    {"$sort": {"count": -1}},
                    {"$limit": 10},
                ],
            }
        },
    ]
    agg = next(_db().alerts.aggregate(facet, allowDiskUse=True), {})
    totals = (agg.get("totals") or [{}])[0]
    total_alerts = int(totals.get("total") or 0)
    skip = (page - 1) * limit
    alert_docs = list(
        _db()
        .alerts.find(alert_match)
        .sort("createdAt", -1)
        .skip(skip)
        .limit(limit)
    )
    total_pages = ceil(total_alerts / limit) if total_alerts else 0

    alert_rows = [
        {
            "id": str(a["_id"]),
            "createdAt": format_datetime(a.get("createdAt")),
            "resolvedAt": format_datetime(a.get("resolvedAt")),
            "acknowledgedAt": format_datetime(a.get("acknowledgedAt")),
            "deviceId": str(a["deviceId"]) if a.get("deviceId") is not None else None,
            "hostname": a.get("hostname") or a.get("deviceName") or "Unknown",
            "ipAddress": a.get("ipAddress"),
            "interface": a.get("interface"),
            "alertType": a.get("alertType") or a.get("category") or "Unknown",
            "category": a.get("category"),
            "severity": a.get("severity") or "UNKNOWN",
            "status": (
                "resolved"
                if a.get("resolved")
                else "dismissed"
                if a.get("dismissed")
                else "acknowledged"
                if a.get("acknowledged")
                else "open"
            ),
            "title": a.get("title") or a.get("message"),
            "family": (
                "storm"
                if (a.get("alertType") in STORM_ALERT_TYPES or a.get("category") in STORM_ALERT_TYPES)
                else "device"
            ),
        }
        for a in alert_docs
    ]

    incident_match: dict[str, Any] = timestamp_match(
        window["start"], window["end"], "createdAt"
    )
    if device_oids and (filters.get("device_id") or filters.get("device_type")):
        incident_match["deviceId"] = {"$in": device_oids}
    if empty_filter:
        incident_match["_id"] = {"$exists": False}

    incident_facet = [
        {"$match": incident_match},
        {
            "$facet": {
                "totals": [
                    {
                        "$group": {
                            "_id": None,
                            "total": {"$sum": 1},
                            "open": {
                                "$sum": {
                                    "$cond": [
                                        {"$not": {"$in": ["$status", list(STORM_CLOSED_STATUSES)]}},
                                        1,
                                        0,
                                    ]
                                }
                            },
                            "resolved": {
                                "$sum": {
                                    "$cond": [{"$in": ["$status", list(STORM_CLOSED_STATUSES)]}, 1, 0]
                                }
                            },
                        }
                    }
                ]
            }
        },
    ]
    inc_agg = next(_db().storm_incidents.aggregate(incident_facet, allowDiskUse=True), {})
    inc_totals = (inc_agg.get("totals") or [{}])[0]

    return {
        "success": True,
        "report": "alerts_incidents",
        "period": period_payload(window),
        "alerts": {
            "total": total_alerts,
            "critical": int(totals.get("critical") or 0),
            "warning": int(totals.get("warning") or 0),
            "info": int(totals.get("info") or 0),
            "open": int(totals.get("open") or 0),
            "resolved": int(totals.get("resolved") or 0),
            "acknowledged": int(totals.get("acknowledged") or 0),
            "bySeverity": [
                {"severity": r.get("_id") or "UNKNOWN", "count": int(r.get("count") or 0)}
                for r in (agg.get("bySeverity") or [])
            ],
            "byType": [
                {"alertType": r.get("_id") or "Unknown", "count": int(r.get("count") or 0)}
                for r in (agg.get("byType") or [])
            ],
            "trend": [
                {
                    "bucket": r.get("_id"),
                    "count": int(r.get("count") or 0),
                    "critical": int(r.get("critical") or 0),
                }
                for r in (agg.get("trend") or [])
                if r.get("_id")
            ],
            "topDevices": [
                {
                    "deviceId": str(r["_id"]) if r.get("_id") is not None else None,
                    "hostname": r.get("hostname") or "Unknown",
                    "ipAddress": r.get("ipAddress"),
                    "count": int(r.get("count") or 0),
                }
                for r in (agg.get("topDevices") or [])
            ],
            "rows": alert_rows,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total_alerts,
                "totalPages": total_pages,
            },
        },
        "stormIncidents": {
            "total": int(inc_totals.get("total") or 0),
            "open": int(inc_totals.get("open") or 0),
            "resolved": int(inc_totals.get("resolved") or 0),
        },
        "definitions": {
            "deviceAlerts": "Reachability and storm-protection alerts stored in the alerts collection.",
            "stormIncidents": "Storm pipeline incidents (separate from device alerts). Counts are not merged.",
        },
        "limitations": [
            "MTTR, MTTD, and MTBF are not calculated; timestamps do not support a defensible formula for all event types.",
            "Device-offline alerts are created only for devices marked critical after the failure threshold.",
        ],
        "unavailable": {
            "mttr": "Not available from current monitoring data",
            "mttd": "Not available from current monitoring data",
            "mtbf": "Not available from current monitoring data",
        },
    }


# ---------------------------------------------------------------------------
# Report 5 — Storm / risk
# ---------------------------------------------------------------------------


def _incident_risk_score(doc: dict[str, Any]) -> float | None:
    risk = doc.get("risk") or {}
    trigger = doc.get("trigger") or {}
    value = risk.get("riskScore") if isinstance(risk, dict) else None
    if value is None:
        value = trigger.get("risk")
    return _round(value)


def _latest_related_status(rows: list[dict[str, Any]], field: str) -> str | None:
    if not rows:
        return None
    return rows[0].get(field)


def build_storm_report(
    window: dict[str, Any],
    filters: dict[str, Any],
    *,
    page: int = 1,
    limit: int = 25,
    severity: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    devices = _load_devices(
        _device_query(
            device_id=filters.get("device_id"),
            device_type=filters.get("device_type"),
        )
    )
    device_oids = _device_ids(devices)
    empty_filter = bool(
        (filters.get("device_id") or filters.get("device_type")) and not device_oids
    )

    match: dict[str, Any] = timestamp_match(window["start"], window["end"], "createdAt")
    if device_oids and (filters.get("device_id") or filters.get("device_type")):
        match["deviceId"] = {"$in": device_oids}
    sev = (severity or "").strip()
    if sev and sev.lower() != "all":
        match["severity"] = sev.upper()
    st = (status or "").strip()
    if st and st.lower() != "all":
        match["status"] = st.upper()
    if empty_filter:
        match["_id"] = {"$exists": False}

    summary_pipeline = [
        {"$match": match},
        {
            "$facet": {
                "totals": [
                    {
                        "$group": {
                            "_id": None,
                            "total": {"$sum": 1},
                            "open": {
                                "$sum": {
                                    "$cond": [
                                        {"$not": {"$in": ["$status", list(STORM_CLOSED_STATUSES)]}},
                                        1,
                                        0,
                                    ]
                                }
                            },
                            "resolved": {
                                "$sum": {
                                    "$cond": [{"$eq": ["$status", "RESOLVED"]}, 1, 0]
                                }
                            },
                            "escalated": {
                                "$sum": {
                                    "$cond": [{"$eq": ["$status", "ESCALATED"]}, 1, 0]
                                }
                            },
                            "critical": {
                                "$sum": {
                                    "$cond": [{"$eq": ["$severity", "CRITICAL"]}, 1, 0]
                                }
                            },
                        }
                    }
                ],
                "trend": [
                    {
                        "$group": {
                            "_id": {
                                "$dateToString": {
                                    "format": _trend_format(window["start"], window["end"]),
                                    "date": "$createdAt",
                                }
                            },
                            "count": {"$sum": 1},
                            "critical": {
                                "$sum": {
                                    "$cond": [{"$eq": ["$severity", "CRITICAL"]}, 1, 0]
                                }
                            },
                        }
                    },
                    {"$sort": {"_id": 1}},
                ],
            }
        },
    ]
    summary = next(_db().storm_incidents.aggregate(summary_pipeline, allowDiskUse=True), {})
    totals = (summary.get("totals") or [{}])[0]
    total = int(totals.get("total") or 0)

    risk_match: dict[str, Any] = {
        **timestamp_match(window["start"], window["end"], "timestamp"),
        "eligible": True,
        "riskScore": {"$gt": 0},
    }
    if device_oids and (filters.get("device_id") or filters.get("device_type")):
        risk_match["deviceId"] = {"$in": device_oids}
    if empty_filter:
        risk_match["_id"] = {"$exists": False}
    risk_row = next(
        _db().storm_risk_history.aggregate(
            [
                {"$match": risk_match},
                {
                    "$group": {
                        "_id": None,
                        "averageRisk": {"$avg": "$riskScore"},
                        "maximumRisk": {"$max": "$riskScore"},
                        "samples": {"$sum": 1},
                    }
                },
            ],
            allowDiskUse=True,
        ),
        None,
    )
    average_risk = _round(risk_row.get("averageRisk")) if risk_row else None
    maximum_risk = _round(risk_row.get("maximumRisk")) if risk_row else None

    skip = (page - 1) * limit
    docs = list(
        _db()
        .storm_incidents.find(match)
        .sort("createdAt", -1)
        .skip(skip)
        .limit(limit)
    )
    incident_ids = [d.get("incidentId") for d in docs if d.get("incidentId")]

    mitigations_by: dict[str, list[dict[str, Any]]] = {}
    recoveries_by: dict[str, list[dict[str, Any]]] = {}
    if incident_ids:
        for row in (
            _db()
            .storm_mitigation_history.find({"incidentId": {"$in": incident_ids}})
            .sort("timestamp", -1)
            .limit(500)
        ):
            mitigations_by.setdefault(row.get("incidentId"), []).append(row)
        for row in (
            _db()
            .storm_recovery_history.find({"incidentId": {"$in": incident_ids}})
            .sort("timestamp", -1)
            .limit(500)
        ):
            recoveries_by.setdefault(row.get("incidentId"), []).append(row)

    table = []
    for doc in docs:
        iid = doc.get("incidentId")
        mits = mitigations_by.get(iid) or []
        recs = recoveries_by.get(iid) or []
        end_time = doc.get("recoveredAt") or (
            doc.get("updatedAt") if doc.get("status") in STORM_CLOSED_STATUSES else None
        )
        table.append(
            {
                "incidentId": iid,
                "deviceId": str(doc["deviceId"]) if doc.get("deviceId") is not None else None,
                "hostname": doc.get("hostname") or "Unknown",
                "ipAddress": doc.get("ipAddress"),
                "interface": doc.get("interface"),
                "riskScore": _incident_risk_score(doc),
                "severity": doc.get("severity") or "LOW",
                "startTime": format_datetime(doc.get("createdAt")),
                "endTime": format_datetime(end_time),
                "status": doc.get("status"),
                "mitigationStatus": _latest_related_status(mits, "status"),
                "recoveryStatus": _latest_related_status(recs, "recoveryStatus"),
                "incidentType": doc.get("incidentType") or "STORM",
            }
        )

    return {
        "success": True,
        "report": "storm",
        "period": period_payload(window),
        "summary": {
            "totalIncidents": total,
            "openIncidents": int(totals.get("open") or 0),
            "resolvedIncidents": int(totals.get("resolved") or 0),
            "escalatedIncidents": int(totals.get("escalated") or 0),
            "criticalIncidents": int(totals.get("critical") or 0),
            "averageRiskScore": average_risk,
            "maximumRiskScore": maximum_risk,
            "riskSamples": int(risk_row.get("samples") or 0) if risk_row else 0,
        },
        "trend": [
            {
                "bucket": r.get("_id"),
                "count": int(r.get("count") or 0),
                "critical": int(r.get("critical") or 0),
            }
            for r in (summary.get("trend") or [])
            if r.get("_id")
        ],
        "incidents": table,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "totalPages": ceil(total / limit) if total else 0,
        },
        "definitions": {
            "averageRiskScore": "Mean riskScore from eligible storm_risk_history rows with score > 0 in the period.",
            "riskScore": "Score snapshotted on the incident at creation (not a live value).",
        },
        "limitations": [
            "Risk weights/thresholds are current configuration; historical policy versions are not stored.",
            "CRC and unknown-unicast analyzers are unsupported unless those counters exist on samples.",
        ],
    }


def build_storm_incident_detail(incident_id: str) -> dict[str, Any] | None:
    iid = (incident_id or "").strip()
    if not iid:
        return None
    doc = _db().storm_incidents.find_one({"incidentId": iid})
    if not doc:
        return None
    from services.storm.diagnostics.serializer import serialize_incident  # noqa: PLC0415
    from services.storm.mitigation.audit import serialize_mitigation_log  # noqa: PLC0415
    from services.storm.recovery.audit import serialize_recovery_log  # noqa: PLC0415
    from utils.serializers import (  # noqa: PLC0415
        serialize_confirmation_result,
        serialize_risk_result,
        serialize_safety_result,
    )

    created = doc.get("createdAt")
    ended = doc.get("recoveredAt") or doc.get("updatedAt") or datetime.now(timezone.utc)
    window_start = created - timedelta(minutes=15) if created else None
    window_end = ended + timedelta(minutes=15) if ended else None
    device_id = doc.get("deviceId")
    interface = doc.get("interface")

    mitigations = list(
        _db()
        .storm_mitigation_history.find({"incidentId": iid})
        .sort("timestamp", 1)
        .limit(DETAIL_RELATED_LIMIT)
    )
    recoveries = list(
        _db()
        .storm_recovery_history.find({"incidentId": iid})
        .sort("timestamp", 1)
        .limit(DETAIL_RELATED_LIMIT)
    )

    related_match: dict[str, Any] = {}
    if device_id is not None:
        related_match["deviceId"] = device_id
    if interface:
        related_match["interface"] = interface
    if window_start and window_end:
        related_match["timestamp"] = {"$gte": window_start, "$lte": window_end}

    risk_rows = []
    confirm_rows = []
    safety_rows = []
    if related_match.get("deviceId") and related_match.get("interface"):
        risk_rows = list(
            _db()
            .storm_risk_history.find(related_match)
            .sort("timestamp", -1)
            .limit(DETAIL_RELATED_LIMIT)
        )
        confirm_rows = list(
            _db()
            .storm_confirmation_history.find(related_match)
            .sort("timestamp", -1)
            .limit(DETAIL_RELATED_LIMIT)
        )
        safety_rows = list(
            _db()
            .storm_safety_history.find(related_match)
            .sort("timestamp", -1)
            .limit(DETAIL_RELATED_LIMIT)
        )

    serialized = serialize_incident(doc)
    latest_risk = serialize_risk_result(risk_rows[0]) if risk_rows else serialized.get("risk")
    return {
        "success": True,
        "incident": serialized,
        "timeline": serialized.get("timeline") or [],
        "riskEvidence": latest_risk,
        "contributors": (latest_risk or {}).get("contributors") if isinstance(latest_risk, dict) else [],
        "rawMetrics": (latest_risk or {}).get("rawMetrics") if isinstance(latest_risk, dict) else {},
        "confirmation": serialized.get("confirmation"),
        "safety": serialized.get("safety"),
        "mitigations": [serialize_mitigation_log(row) for row in mitigations],
        "recoveries": [serialize_recovery_log(row) for row in recoveries],
        "relatedRiskHistory": [serialize_risk_result(row) for row in risk_rows[:20]],
        "relatedConfirmation": [serialize_confirmation_result(row) for row in confirm_rows[:10]],
        "relatedSafety": [serialize_safety_result(row) for row in safety_rows[:10]],
    }


# ---------------------------------------------------------------------------
# Export row builders (bounded)
# ---------------------------------------------------------------------------


def export_availability_rows(window: dict[str, Any], filters: dict[str, Any]) -> tuple[list[str], list[list]]:
    payload = build_availability_report(window, filters, page=1, limit=EXPORT_MAX)
    headers = [
        "hostname",
        "ipAddress",
        "deviceType",
        "status",
        "probeSuccessRatio",
        "totalChecks",
        "onlineChecks",
        "failedChecks",
        "firstFailedProbeAt",
        "confirmedOutageEvents",
        "currentlyUnreachable",
        "timeSinceLastSuccessfulPing",
    ]
    rows = [
        [
            r.get("hostname"),
            r.get("ipAddress"),
            r.get("deviceType"),
            r.get("status"),
            r.get("probeSuccessRatio"),
            r.get("totalChecks"),
            r.get("onlineChecks"),
            r.get("failedChecks"),
            r.get("firstFailedProbeAt"),
            r.get("confirmedOutageEvents"),
            r.get("currentlyUnreachable"),
            r.get("timeSinceLastSuccessfulPingLabel"),
        ]
        for r in payload.get("devices") or []
    ]
    return headers, rows


def export_performance_rows(window: dict[str, Any], filters: dict[str, Any], interface: str | None) -> tuple[list[str], list[list]]:
    payload = build_performance_report(window, filters, interface=interface)
    ping = payload.get("ping") or {}
    headers = ["metric", "value"]
    rows = [
        ["period", payload["period"]["label"]],
        ["successfulScans", ping.get("successfulScans")],
        ["failedScans", ping.get("failedScans")],
        ["averageRttMs", ping.get("averageRttMs")],
        ["minRttMs", ping.get("minRttMs")],
        ["maxRttMs", ping.get("maxRttMs")],
        ["p50RttMs", ping.get("p50RttMs")],
        ["p95RttMs", ping.get("p95RttMs")],
        ["p99RttMs", ping.get("p99RttMs")],
    ]
    for item in (payload.get("interfaces") or {}).get("topUtilization") or []:
        rows.append(
            [
                f"util:{item.get('hostname')}/{item.get('interface')}",
                item.get("peakUtilizationPercent"),
            ]
        )
    return headers, rows


def export_alerts_rows(window: dict[str, Any], filters: dict[str, Any], extra: dict[str, Any]) -> tuple[list[str], list[list]]:
    payload = build_alerts_incidents_report(
        window,
        filters,
        page=1,
        limit=EXPORT_MAX,
        severity=extra.get("severity"),
        alert_type=extra.get("alert_type"),
        alert_status=extra.get("status"),
    )
    headers = [
        "createdAt",
        "hostname",
        "ipAddress",
        "alertType",
        "severity",
        "status",
        "family",
        "resolvedAt",
    ]
    rows = [
        [
            r.get("createdAt"),
            r.get("hostname"),
            r.get("ipAddress"),
            r.get("alertType"),
            r.get("severity"),
            r.get("status"),
            r.get("family"),
            r.get("resolvedAt"),
        ]
        for r in (payload.get("alerts") or {}).get("rows") or []
    ]
    return headers, rows


def export_storm_rows(window: dict[str, Any], filters: dict[str, Any], extra: dict[str, Any]) -> tuple[list[str], list[list]]:
    payload = build_storm_report(
        window,
        filters,
        page=1,
        limit=EXPORT_MAX,
        severity=extra.get("severity"),
        status=extra.get("status"),
    )
    headers = [
        "incidentId",
        "hostname",
        "interface",
        "riskScore",
        "severity",
        "startTime",
        "endTime",
        "status",
        "mitigationStatus",
        "recoveryStatus",
    ]
    rows = [
        [
            r.get("incidentId"),
            r.get("hostname"),
            r.get("interface"),
            r.get("riskScore"),
            r.get("severity"),
            r.get("startTime"),
            r.get("endTime"),
            r.get("status"),
            r.get("mitigationStatus"),
            r.get("recoveryStatus"),
        ]
        for r in payload.get("incidents") or []
    ]
    return headers, rows


def export_executive_rows(window: dict[str, Any], filters: dict[str, Any]) -> tuple[list[str], list[list]]:
    payload = build_executive_report(window, filters)
    snap = payload.get("snapshot") or {}
    period = payload.get("periodMetrics") or {}
    quality = payload.get("dataQuality") or {}
    headers = ["metric", "value"]
    rows = [[k, v] for k, v in {**snap, **period, **quality}.items() if not isinstance(v, (dict, list))]
    rtt = period.get("successfulIcmpScanRtt") or {}
    for k, v in rtt.items():
        rows.append([f"rtt.{k}", v])
    return headers, rows
