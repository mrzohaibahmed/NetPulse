import time

from flask import Blueprint, jsonify

from config.database import db
from services.ping_service import (
    STATUS_NOT_REACHABLE,
    STATUS_OFFLINE_CRITICAL,
    STATUS_ONLINE,
)
from utils.auth import require_auth
from utils.serializers import format_datetime, get_device_type

dashboard_bp = Blueprint("dashboard", __name__)

# Short-lived cache for dashboard averageResponseTime (same semantics as statistics).
_avg_response_value: float | None = None
_avg_response_expires_at: float = 0.0
_AVG_RESPONSE_CACHE_TTL_SECONDS = 14.0

_SWITCH_TYPE_MATCH = {
    "$expr": {
        "$regexMatch": {
            "input": {
                "$toLower": {
                    "$ifNull": ["$deviceType", {"$ifNull": ["$type", ""]}],
                }
            },
            "regex": "switch",
        }
    }
}


def _ping_history_average_response_time() -> float | None:
    """
    Mean of all non-null pingHistory.responseTime samples.

    Matches ``dashboard_statistics`` / legacy dashboard average semantics.
    """
    global _avg_response_value, _avg_response_expires_at

    now = time.monotonic()
    if now < _avg_response_expires_at:
        return _avg_response_value

    pipeline = [
        {"$match": {"responseTime": {"$ne": None}}},
        {"$group": {"_id": None, "average": {"$avg": "$responseTime"}}},
    ]
    avg_result = list(db.pingHistory.aggregate(pipeline))
    average_response = (
        round(avg_result[0]["average"], 2) if avg_result else None
    )
    _avg_response_value = average_response
    _avg_response_expires_at = now + _AVG_RESPONSE_CACHE_TTL_SECONDS
    return average_response


def _facet_count(facet: dict, key: str) -> int:
    return int((facet.get(key) or [{}])[0].get("n") or 0)


@dashboard_bp.route("/dashboard/summary", methods=["GET"])
@require_auth()
def dashboard_summary():
    try:
        # Status / health KPIs are scoped to monitored devices only.
        # Unmonitored inventory still contributes to totalDevices.
        monitored_match = {"monitor": True}
        pipeline = [
            {
                "$facet": {
                    "total": [{"$count": "n"}],
                    "monitored": [
                        {"$match": monitored_match},
                        {"$count": "n"},
                    ],
                    "online": [
                        {"$match": {**monitored_match, "status": STATUS_ONLINE}},
                        {"$count": "n"},
                    ],
                    "notReachable": [
                        {
                            "$match": {
                                **monitored_match,
                                "status": STATUS_NOT_REACHABLE,
                            }
                        },
                        {"$count": "n"},
                    ],
                    "offlineCritical": [
                        {
                            "$match": {
                                **monitored_match,
                                "$or": [
                                    {"status": STATUS_OFFLINE_CRITICAL},
                                    {"status": "Offline", "critical": True},
                                ],
                            }
                        },
                        {"$count": "n"},
                    ],
                    "legacyOffline": [
                        {
                            "$match": {
                                **monitored_match,
                                "status": "Offline",
                                "critical": {"$ne": True},
                            }
                        },
                        {"$count": "n"},
                    ],
                    "unknown": [
                        {"$match": {**monitored_match, "status": "Unknown"}},
                        {"$count": "n"},
                    ],
                    "criticalFlag": [
                        {"$match": {**monitored_match, "critical": True}},
                        {"$count": "n"},
                    ],
                }
            }
        ]
        facet = next(db.devices.aggregate(pipeline), {})
        counts = {
            key: int((facet.get(key) or [{}])[0].get("n") or 0)
            for key in (
                "total",
                "online",
                "notReachable",
                "offlineCritical",
                "legacyOffline",
                "unknown",
                "criticalFlag",
                "monitored",
            )
        }
        total = counts["total"]
        monitored = counts["monitored"]
        not_reachable = counts["notReachable"] + counts["legacyOffline"]
        offline_critical = counts["offlineCritical"]

        def pct(count):
            return round((count / monitored) * 100, 2) if monitored else 0

        return jsonify({
            "success": True,
            "summary": {
                "totalDevices": total,
                "onlineDevices": counts["online"],
                "notReachableDevices": not_reachable,
                "criticalOfflineDevices": offline_critical,
                "unknownDevices": counts["unknown"],
                "criticalDevices": counts["criticalFlag"],
                "monitoredDevices": monitored,
                "onlinePercentage": pct(counts["online"]),
                "notReachablePercentage": pct(not_reachable),
                "criticalOfflinePercentage": pct(offline_critical),
                # Legacy fields for older clients
                "offlineDevices": not_reachable + offline_critical,
            }
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to get dashboard summary",
            "error": str(error),
        }), 500


@dashboard_bp.route("/dashboard/ops-metrics", methods=["GET"])
@require_auth(roles=["admin"])
def dashboard_ops_metrics():
    """Production operational snapshot (no secrets)."""
    try:
        from services.ops_health import ops_metrics_snapshot  # noqa: PLC0415

        return jsonify({
            "success": True,
            "metrics": ops_metrics_snapshot(),
        }), 200
    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to collect operational metrics",
            "error": str(error),
        }), 500



@dashboard_bp.route("/dashboard/recent-history", methods=["GET"])
@require_auth()
def recent_history():
    try:
        history = []
        for record in db.pingHistory.find().sort("timestamp", -1).limit(20):
            history.append({
                "_id": str(record["_id"]),
                "deviceId": str(record["deviceId"]),
                "hostname": record.get("hostname"),
                "ipAddress": record.get("ipAddress"),
                "status": record.get("status"),
                "responseTime": record.get("responseTime"),
                "scanType": record.get("scanType", "Manual"),
                "timestamp": format_datetime(record.get("timestamp")),
            })

        return jsonify({
            "success": True,
            "count": len(history),
            "history": history,
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to get recent history",
            "error": str(error),
        }), 500


@dashboard_bp.route("/dashboard/device-metrics", methods=["GET"])
@require_auth()
def dashboard_device_metrics():
    """
    Lightweight dashboard metrics: switch counts + average response time.

    Replaces polling full device inventory and the statistics endpoint on
    the Dashboard refresh path while preserving displayed metric semantics.
    """
    try:
        facet = next(
            db.devices.aggregate([
                {
                    "$facet": {
                        "managedSwitches": [
                            {"$match": _SWITCH_TYPE_MATCH},
                            {"$count": "n"},
                        ],
                        "monitoredSwitches": [
                            {
                                "$match": {
                                    **_SWITCH_TYPE_MATCH,
                                    "monitor": True,
                                }
                            },
                            {"$count": "n"},
                        ],
                    }
                }
            ]),
            {},
        )

        return jsonify({
            "success": True,
            "metrics": {
                "managedSwitches": _facet_count(facet, "managedSwitches"),
                "monitoredSwitches": _facet_count(facet, "monitoredSwitches"),
                "averageResponseTime": _ping_history_average_response_time(),
            },
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to get dashboard device metrics",
            "error": str(error),
        }), 500


@dashboard_bp.route("/dashboard/device-status", methods=["GET"])
@require_auth()
def device_status():
    try:
        devices = []
        for device in db.devices.find().sort("hostname", 1):
            devices.append({
                "_id": str(device["_id"]),
                "hostname": device.get("hostname"),
                "ipAddress": device.get("ipAddress"),
                "deviceType": get_device_type(device),
                "status": device.get("status"),
                "responseTime": device.get("responseTime"),
                "lastSeen": format_datetime(device.get("lastSeen")),
                "critical": device.get("critical", False),
                "monitor": device.get("monitor", True),
                "consecutiveFailures": device.get("consecutiveFailures", 0),
            })

        return jsonify({
            "success": True,
            "count": len(devices),
            "devices": devices,
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to get device status",
            "error": str(error),
        }), 500


@dashboard_bp.route("/dashboard/statistics", methods=["GET"])
@require_auth()
def dashboard_statistics():
    try:
        total_devices = db.devices.count_documents({})
        online_devices = db.devices.count_documents({"status": STATUS_ONLINE})
        not_reachable = db.devices.count_documents({"status": STATUS_NOT_REACHABLE})
        critical_offline = db.devices.count_documents({
            "$or": [
                {"status": STATUS_OFFLINE_CRITICAL},
                {"status": "Offline", "critical": True},
            ]
        })
        legacy_nr = db.devices.count_documents({
            "status": "Offline",
            "critical": {"$ne": True},
        })
        not_reachable += legacy_nr
        unknown_devices = db.devices.count_documents({"status": "Unknown"})
        critical_online = db.devices.count_documents({
            "critical": True,
            "status": STATUS_ONLINE,
        })
        total_scans = db.pingHistory.count_documents({})
        average_response = _ping_history_average_response_time()

        def pct(count):
            return round((count / total_devices) * 100, 2) if total_devices else 0

        return jsonify({
            "success": True,
            "statistics": {
                "totalScans": total_scans,
                "averageResponseTime": average_response,
                "onlinePercentage": pct(online_devices),
                "notReachablePercentage": pct(not_reachable),
                "criticalOfflinePercentage": pct(critical_offline),
                "offlinePercentage": pct(not_reachable + critical_offline),
                "unknownPercentage": pct(unknown_devices),
                "criticalOnline": critical_online,
                "criticalOffline": critical_offline,
            },
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to get dashboard statistics",
            "error": str(error),
        }), 500


@dashboard_bp.route("/dashboard/charts/device-status", methods=["GET"])
@require_auth()
def device_status_chart():
    try:
        online = db.devices.count_documents({"status": STATUS_ONLINE})
        offline_critical = db.devices.count_documents({
            "$or": [
                {"status": STATUS_OFFLINE_CRITICAL},
                {"status": "Offline", "critical": True},
            ]
        })
        not_reachable = db.devices.count_documents({
            "$or": [
                {"status": STATUS_NOT_REACHABLE},
                {"status": "Offline", "critical": {"$ne": True}},
            ]
        })
        unknown = db.devices.count_documents({"status": "Unknown"})

        return jsonify({
            "success": True,
            "chart": [
                {"name": STATUS_ONLINE, "value": online},
                {"name": STATUS_NOT_REACHABLE, "value": not_reachable},
                {"name": STATUS_OFFLINE_CRITICAL, "value": offline_critical},
                {"name": "Unknown", "value": unknown},
            ],
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to get device status chart",
            "error": str(error),
        }), 500


@dashboard_bp.route("/dashboard/charts/device-type", methods=["GET"])
@require_auth()
def device_type_chart():
    try:
        pipeline = [
            {
                "$group": {
                    "_id": {
                        "$ifNull": ["$deviceType", {"$ifNull": ["$type", "Unknown"]}]
                    },
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"count": -1}},
        ]

        chart = [
            {"name": item["_id"] or "Unknown", "value": item["count"]}
            for item in db.devices.aggregate(pipeline)
        ]

        return jsonify({"success": True, "chart": chart}), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to get device type chart",
            "error": str(error),
        }), 500


@dashboard_bp.route("/dashboard/charts/response-time", methods=["GET"])
@require_auth()
def response_time_chart():
    try:
        pipeline = [
            {
                "$group": {
                    "_id": "$hostname",
                    "averageResponseTime": {"$avg": "$responseTime"},
                }
            },
            {"$sort": {"_id": 1}},
        ]

        chart = [
            {
                "hostname": item["_id"],
                "responseTime": round(item["averageResponseTime"], 2),
            }
            for item in db.pingHistory.aggregate(pipeline)
            if item.get("averageResponseTime") is not None
        ]

        return jsonify({"success": True, "chart": chart}), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to get response time chart",
            "error": str(error),
        }), 500


@dashboard_bp.route("/dashboard/site-monitoring", methods=["GET"])
@require_auth()
def dashboard_site_monitoring():
    """Grouped ISP and server monitoring by site/location."""
    try:
        from services.site_monitoring_service import build_site_monitoring_payload  # noqa: PLC0415

        payload = build_site_monitoring_payload(db)
        return jsonify({
            "success": True,
            **payload,
        }), 200
    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to get site monitoring data",
            "error": str(error),
        }), 500


@dashboard_bp.route("/dashboard/charts/scan-activity", methods=["GET"])
@require_auth()
def scan_activity_chart():
    try:
        pipeline = [
            {
                "$group": {
                    "_id": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$timestamp",
                        }
                    },
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"_id": 1}},
        ]

        chart = [
            {"date": item["_id"], "scans": item["count"]}
            for item in db.pingHistory.aggregate(pipeline)
        ]

        return jsonify({"success": True, "chart": chart}), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to get scan activity chart",
            "error": str(error),
        }), 500
