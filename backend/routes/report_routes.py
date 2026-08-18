import csv
import io
from datetime import datetime, timezone

from bson import ObjectId
from utils.api_errors import internal_error_response
from flask import Blueprint, Response, jsonify, request
from openpyxl import Workbook

from config.database import db
from utils.auth import require_auth
from utils.serializers import format_datetime, get_device_type
from services.report_period import resolve_report_period, timestamp_match
from services.report_service import (
    EXPORT_MAX,
    build_alerts_incidents_report,
    build_availability_report,
    build_executive_report,
    build_performance_report,
    build_storm_incident_detail,
    build_storm_report,
    export_alerts_rows,
    export_availability_rows,
    export_executive_rows,
    export_performance_rows,
    export_storm_rows,
    list_filter_options,
    parse_page,
)
from services.storm.diagnostics.serializer import serialize_incident
from services.storm.mitigation.audit import serialize_mitigation_log
from services.storm.recovery.audit import serialize_recovery_log

report_bp = Blueprint("reports", __name__)


def _parse_date(value, end_of_day=False):
    if not value:
        return None
    text = value.strip()
    try:
        if len(text) == 10:
            dt = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59)
            return dt
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Invalid date: {value}") from error


def build_report_filters():
    query = {}
    device_type = (request.args.get("deviceType") or "").strip()
    status = (request.args.get("status") or "").strip()
    start = _parse_date(request.args.get("startDate"))
    end = _parse_date(request.args.get("endDate"), end_of_day=True)

    if device_type and device_type.lower() != "all":
        query["$or"] = [
            {"deviceType": device_type},
            {"type": device_type},
        ]

    if status and status.lower() != "all":
        query["status"] = status

    if start or end:
        query["timestamp"] = {}
        if start:
            query["timestamp"]["$gte"] = start
        if end:
            query["timestamp"]["$lte"] = end

    return query


def build_device_report_filters():
    query = {}
    device_type = (request.args.get("deviceType") or "").strip()
    status = (request.args.get("status") or "").strip()

    if device_type and device_type.lower() != "all":
        query["$or"] = [
            {"deviceType": device_type},
            {"type": device_type},
        ]
    if status and status.lower() != "all":
        query["status"] = status
    return query


def compute_uptime(device_id, start=None, end=None):
    match = {"deviceId": device_id if isinstance(device_id, ObjectId) else ObjectId(device_id)}
    if start or end:
        match["timestamp"] = {}
        if start:
            match["timestamp"]["$gte"] = start
        if end:
            match["timestamp"]["$lte"] = end

    total = db.pingHistory.count_documents(match)
    if total == 0:
        return {
            "totalChecks": 0,
            "onlineChecks": 0,
            "downtimeChecks": 0,
            "uptimePercentage": None,
            "downtimePercentage": None,
        }

    online = db.pingHistory.count_documents({**match, "status": "Online"})
    downtime = total - online
    uptime_pct = round((online / total) * 100, 2)
    downtime_pct = round((downtime / total) * 100, 2)
    return {
        "totalChecks": total,
        "onlineChecks": online,
        "downtimeChecks": downtime,
        "uptimePercentage": uptime_pct,
        "downtimePercentage": downtime_pct,
    }


@report_bp.route("/reports/uptime", methods=["GET"])
@require_auth()
def uptime_report():
    try:
        start = _parse_date(request.args.get("startDate"))
        end = _parse_date(request.args.get("endDate"), end_of_day=True)
        device_type = (request.args.get("deviceType") or "").strip()
        status = (request.args.get("status") or "").strip()

        device_query = {}
        if device_type and device_type.lower() != "all":
            device_query["$or"] = [
                {"deviceType": device_type},
                {"type": device_type},
            ]
        if status and status.lower() != "all":
            device_query["status"] = status

        rows = []
        for device in db.devices.find(device_query).sort("hostname", 1):
            metrics = compute_uptime(device["_id"], start, end)
            rows.append({
                "deviceId": str(device["_id"]),
                "hostname": device.get("hostname"),
                "ipAddress": device.get("ipAddress"),
                "deviceType": get_device_type(device),
                "status": device.get("status"),
                **metrics,
            })

        return jsonify({"success": True, "count": len(rows), "data": rows}), 200

    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        return internal_error_response(error, message="Failed to build uptime report")


def _request_window():
    period = request.args.get("period")
    start = request.args.get("startDate")
    end = request.args.get("endDate")
    if not period and start and end:
        period = "custom"
    return resolve_report_period(period, start, end)


def _request_device_filters():
    return {
        "device_id": (request.args.get("deviceId") or "").strip() or None,
        "device_type": (request.args.get("deviceType") or "").strip() or None,
        "status": (request.args.get("status") or "").strip() or None,
    }


@report_bp.route("/reports/filters", methods=["GET"])
@require_auth()
def report_filters():
    try:
        return jsonify({
            "success": True,
            **list_filter_options(request.args.get("deviceId")),
        }), 200
    except Exception as error:  # noqa: BLE001
        return internal_error_response(error, message="Failed to load report filters")


@report_bp.route("/reports/executive", methods=["GET"])
@require_auth()
def executive_report():
    try:
        window = _request_window()
        payload = build_executive_report(window, _request_device_filters())
        return jsonify(payload), 200
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:  # noqa: BLE001
        return internal_error_response(error, message="Failed to build executive report")


@report_bp.route("/reports/availability", methods=["GET"])
@require_auth()
def availability_report():
    try:
        window = _request_window()
        page, limit = parse_page(request.args.get("page"), request.args.get("limit"))
        payload = build_availability_report(
            window, _request_device_filters(), page=page, limit=limit
        )
        return jsonify(payload), 200
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:  # noqa: BLE001
        return internal_error_response(error, message="Failed to build availability report")


@report_bp.route("/reports/performance", methods=["GET"])
@require_auth()
def performance_report():
    try:
        window = _request_window()
        filters = _request_device_filters()
        payload = build_performance_report(
            window,
            filters,
            interface=(request.args.get("interface") or "").strip() or None,
        )
        return jsonify(payload), 200
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:  # noqa: BLE001
        return internal_error_response(error, message="Failed to build performance report")


@report_bp.route("/reports/alerts-incidents", methods=["GET"])
@require_auth()
def alerts_incidents_report():
    try:
        window = _request_window()
        page, limit = parse_page(request.args.get("page"), request.args.get("limit"))
        payload = build_alerts_incidents_report(
            window,
            _request_device_filters(),
            page=page,
            limit=limit,
            severity=request.args.get("severity"),
            alert_type=request.args.get("alertType"),
            alert_status=request.args.get("alertStatus"),
        )
        return jsonify(payload), 200
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:  # noqa: BLE001
        return internal_error_response(error, message="Failed to build alerts report")


@report_bp.route("/reports/storm", methods=["GET"])
@require_auth()
def storm_report():
    try:
        window = _request_window()
        page, limit = parse_page(request.args.get("page"), request.args.get("limit"))
        payload = build_storm_report(
            window,
            _request_device_filters(),
            page=page,
            limit=limit,
            severity=request.args.get("severity"),
            status=request.args.get("incidentStatus"),
        )
        return jsonify(payload), 200
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:  # noqa: BLE001
        return internal_error_response(error, message="Failed to build storm report")


@report_bp.route("/reports/storm/incidents/<incident_id>", methods=["GET"])
@require_auth()
def storm_incident_detail(incident_id):
    try:
        payload = build_storm_incident_detail(incident_id)
        if not payload:
            return jsonify({"success": False, "message": "Incident not found"}), 404
        return jsonify(payload), 200
    except Exception as error:  # noqa: BLE001
        return internal_error_response(error, message="Failed to load incident detail")


@report_bp.route("/reports/export/devices", methods=["GET"])
@require_auth()
def export_devices():
    try:
        fmt = (request.args.get("format") or "csv").lower()
        devices = list(db.devices.find(build_device_report_filters()).sort("hostname", 1))

        headers = [
            "hostname", "ipAddress", "deviceType", "status", "critical",
            "monitor", "responseTime", "lastSeen", "consecutiveFailures",
        ]
        rows = [
            [
                d.get("hostname"),
                d.get("ipAddress"),
                get_device_type(d),
                d.get("status"),
                d.get("critical", False),
                d.get("monitor", True),
                d.get("responseTime"),
                format_datetime(d.get("lastSeen")),
                d.get("consecutiveFailures", 0),
            ]
            for d in devices
        ]

        return _export_response("devices", headers, rows, fmt)

    except Exception as error:
        return internal_error_response(error, message="Failed to export devices")


@report_bp.route("/reports/export/history", methods=["GET"])
@require_auth()
def export_history():
    try:
        fmt = (request.args.get("format") or "csv").lower()
        window = _request_window()
        limit = _parse_limit(max_default=5000, max_limit=5000)
        filters = build_report_filters()
        filters["timestamp"] = timestamp_match(window["start"], window["end"])["timestamp"]

        # History docs may not have deviceType; filter via join-like lookup when needed
        device_type = (request.args.get("deviceType") or "").strip()
        if device_type and device_type.lower() != "all":
            type_device_ids = [
                d["_id"]
                for d in db.devices.find({
                    "$or": [{"deviceType": device_type}, {"type": device_type}]
                })
            ]
            filters.pop("$or", None)
            filters["deviceId"] = {"$in": type_device_ids}

        history = list(
            db.pingHistory.find(filters).sort("timestamp", -1).limit(limit)
        )
        headers = [
            "hostname", "ipAddress", "status", "responseTime", "scanType", "timestamp",
        ]
        rows = [
            [
                h.get("hostname"),
                h.get("ipAddress"),
                h.get("status"),
                h.get("responseTime"),
                h.get("scanType"),
                format_datetime(h.get("timestamp")),
            ]
            for h in history
        ]

        return _export_response("status_logs", headers, rows, fmt)

    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        return internal_error_response(error, message="Failed to export history")


def _parse_limit(max_default: int = 5000, max_limit: int = 5000) -> int:
    """
    Simple limit parser for report exports.

    We keep it conservative to avoid memory blow-ups when exporting big collections.
    """
    raw = (request.args.get("limit") or "").strip()
    if not raw:
        return max_default
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ValueError("Invalid limit") from exc
    limit = max(1, limit)
    return min(limit, max_limit)


@report_bp.route("/reports/export/storm/incidents", methods=["GET"])
@require_auth()
def export_storm_incidents():
    try:
        fmt = (request.args.get("format") or "csv").lower()
        limit = _parse_limit()
        window = _request_window()
        query = timestamp_match(window["start"], window["end"], "createdAt")

        docs = list(
            db["storm_incidents"]
            .find(query)
            .sort("createdAt", -1)
            .limit(limit)
        )

        headers = ["Incident", "Switch", "Interface", "Severity", "Status", "Created"]
        rows = [
            [
                d.get("incidentId"),
                d.get("hostname"),
                d.get("interface"),
                d.get("severity"),
                d.get("status"),
                format_datetime(d.get("createdAt")),
            ]
            for d in map(serialize_incident, docs)
        ]
        return _export_response("storm_incidents", headers, rows, fmt)
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:  # noqa: BLE001
        return internal_error_response(error, message="Failed to export storm incidents")


@report_bp.route("/reports/export/storm/mitigations", methods=["GET"])
@require_auth()
def export_storm_mitigations():
    try:
        fmt = (request.args.get("format") or "csv").lower()
        limit = _parse_limit()
        window = _request_window()
        query = timestamp_match(window["start"], window["end"])

        docs = list(
            db["storm_mitigation_history"]
            .find(query)
            .sort("timestamp", -1)
            .limit(limit)
        )

        headers = ["Incident", "Interface", "Strategy", "Status", "Operator", "Time"]
        rows = [
            [
                d.get("incidentId"),
                d.get("interface"),
                d.get("strategy"),
                d.get("status"),
                d.get("operator"),
                d.get("timestamp"),
            ]
            for d in map(serialize_mitigation_log, docs)
        ]
        return _export_response("storm_mitigations", headers, rows, fmt)
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:  # noqa: BLE001
        return internal_error_response(error, message="Failed to export storm mitigations")


@report_bp.route("/reports/export/storm/recoveries", methods=["GET"])
@require_auth()
def export_storm_recoveries():
    try:
        fmt = (request.args.get("format") or "csv").lower()
        limit = _parse_limit()
        window = _request_window()
        query = timestamp_match(window["start"], window["end"])

        docs = list(
            db["storm_recovery_history"]
            .find(query)
            .sort("timestamp", -1)
            .limit(limit)
        )

        headers = ["Incident", "Interface", "Status", "Executed By", "Time"]
        rows = [
            [
                d.get("incidentId"),
                d.get("interface"),
                d.get("recoveryStatus"),
                d.get("executedBy") or "",
                d.get("timestamp"),
            ]
            for d in map(serialize_recovery_log, docs)
        ]
        return _export_response("storm_recoveries", headers, rows, fmt)
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:  # noqa: BLE001
        return internal_error_response(error, message="Failed to export storm recoveries")


@report_bp.route("/reports/export/<report_type>", methods=["GET"])
@require_auth()
def export_named_report(report_type):
    try:
        kind = (report_type or "").strip().lower()
        if kind in {"devices", "history"}:
            return jsonify({"success": False, "message": "Unknown report type"}), 404
        window = _request_window()
        filters = _request_device_filters()
        fmt = (request.args.get("format") or "csv").lower()
        extra = {
            "severity": request.args.get("severity"),
            "alert_type": request.args.get("alertType"),
            "status": request.args.get("alertStatus")
            or request.args.get("incidentStatus"),
        }
        if kind == "executive":
            headers, rows = export_executive_rows(window, filters)
        elif kind in ("availability", "devices-availability"):
            headers, rows = export_availability_rows(window, filters)
        elif kind == "performance":
            headers, rows = export_performance_rows(
                window,
                filters,
                (request.args.get("interface") or "").strip() or None,
            )
        elif kind in ("alerts", "alerts-incidents"):
            headers, rows = export_alerts_rows(window, filters, extra)
        elif kind == "storm":
            headers, rows = export_storm_rows(window, filters, extra)
        else:
            return jsonify({"success": False, "message": "Unknown report type"}), 400
        return _export_response(f"report_{kind}", headers, rows[:EXPORT_MAX], fmt)
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:  # noqa: BLE001
        return internal_error_response(error, message="Failed to export report")


def _export_response(basename, headers, rows, fmt):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if fmt in ("xlsx", "excel"):
        workbook = Workbook()
        sheet = workbook.active
        if sheet is None:
            sheet = workbook.create_sheet()
        sheet.title = basename
        sheet.append(headers)
        for row in rows:
            sheet.append(row)

        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        return Response(
            buffer.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{basename}_{timestamp}.xlsx"',
            },
        )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{basename}_{timestamp}.csv"',
        },
    )
