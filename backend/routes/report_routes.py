import csv
import io
from datetime import datetime, timezone

from bson import ObjectId
from flask import Blueprint, Response, jsonify, request
from openpyxl import Workbook

from config.database import db
from utils.auth import require_auth
from utils.serializers import format_datetime, get_device_type

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
        return jsonify({
            "success": False,
            "message": "Failed to build uptime report",
            "error": str(error),
        }), 500


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
        return jsonify({
            "success": False,
            "message": "Failed to export devices",
            "error": str(error),
        }), 500


@report_bp.route("/reports/export/history", methods=["GET"])
@require_auth()
def export_history():
    try:
        fmt = (request.args.get("format") or "csv").lower()
        filters = build_report_filters()

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

        history = list(db.pingHistory.find(filters).sort("timestamp", -1).limit(50000))
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
        return jsonify({
            "success": False,
            "message": "Failed to export history",
            "error": str(error),
        }), 500


def _export_response(basename, headers, rows, fmt):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if fmt in ("xlsx", "excel"):
        workbook = Workbook()
        sheet = workbook.active
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
