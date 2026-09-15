"""REST API for Cisco switch hardware monitoring."""

from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from flask import Blueprint, g, jsonify, request

from config.database import db
from services.audit_service import log_audit
from services.switch_hardware.collector import (
    collect_device_hardware,
    serialize_hardware_document,
)
from services.switch_hardware.vendor_detection import get_ineligibility_reason, is_eligible_switch
from utils.auth import require_auth
from utils.pagination import clamp_page, pagination_payload, parse_pagination

switch_hardware_bp = Blueprint("switch_hardware", __name__)


def _parse_object_id(device_id: str):
    if not ObjectId.is_valid(device_id):
        return None
    return ObjectId(device_id)


def _load_eligible_device(device_id: str):
    oid = _parse_object_id(device_id)
    if oid is None:
        return None, (jsonify({"success": False, "message": "Invalid device id"}), 400)
    device = db.devices.find_one({"_id": oid})
    if not device:
        return None, (jsonify({"success": False, "message": "Device not found"}), 404)
    if not is_eligible_switch(device):
        reason = get_ineligibility_reason(device) or "Device is not an eligible Cisco switch"
        return None, (jsonify({"success": False, "message": reason}), 400)
    return device, None


def _parse_iso_date(value, end_of_day=False):
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


def _serialize_outage(doc):
    if not doc:
        return None
    out = dict(doc)
    out["id"] = str(out.pop("_id"))
    out["deviceId"] = str(out.get("deviceId"))
    for field in ("startedAt", "endedAt", "createdAt", "updatedAt"):
        if out.get(field) is not None:
            out[field] = out[field].isoformat().replace("+00:00", "Z")
    return out


@switch_hardware_bp.route("/switches/<device_id>/hardware", methods=["GET"])
@require_auth()
def get_switch_hardware(device_id: str):
    device, error = _load_eligible_device(device_id)
    if error:
        return error
    doc = db.switch_hardware_current.find_one({"deviceId": device["_id"]})
    return jsonify({"success": True, "hardware": serialize_hardware_document(doc)})


@switch_hardware_bp.route("/switches/<device_id>/hardware/history", methods=["GET"])
@require_auth()
def get_switch_hardware_history(device_id: str):
    device, error = _load_eligible_device(device_id)
    if error:
        return error

    try:
        page, limit = parse_pagination(default_limit=50, max_limit=200)
        query = {"deviceId": device["_id"]}
        start = request.args.get("start")
        end = request.args.get("end")
        if start or end:
            ts_filter = {}
            if start:
                ts_filter["$gte"] = _parse_iso_date(start)
            if end:
                ts_filter["$lte"] = _parse_iso_date(end, end_of_day=True)
            query["timestamp"] = ts_filter

        total = db.switch_hardware_history.count_documents(query)
        page, skip, total_pages = clamp_page(page, total, limit)
        rows = list(
            db.switch_hardware_history.find(query)
            .sort("timestamp", -1)
            .skip(skip)
            .limit(limit)
        )
        items = []
        for row in rows:
            item = dict(row)
            item.pop("_id", None)
            item["deviceId"] = str(item.get("deviceId"))
            if item.get("timestamp") is not None:
                item["timestamp"] = item["timestamp"].isoformat().replace("+00:00", "Z")
            items.append(item)

        return jsonify(
            {
                "success": True,
                "items": items,
                "pagination": pagination_payload(total, page, limit, total_pages),
            }
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400


@switch_hardware_bp.route("/switches/<device_id>/hardware/events", methods=["GET"])
@require_auth()
def get_switch_hardware_events(device_id: str):
    device, error = _load_eligible_device(device_id)
    if error:
        return error

    page, limit = parse_pagination(default_limit=50, max_limit=200)
    query = {"deviceId": device["_id"]}
    event_type = (request.args.get("eventType") or "").strip()
    if event_type:
        query["eventType"] = event_type

    total = db.switch_hardware_events.count_documents(query)
    page, skip, total_pages = clamp_page(page, total, limit)
    rows = list(
        db.switch_hardware_events.find(query)
        .sort("timestamp", -1)
        .skip(skip)
        .limit(limit)
    )
    items = []
    for row in rows:
        item = dict(row)
        item["id"] = str(item.pop("_id"))
        item["deviceId"] = str(item.get("deviceId"))
        if item.get("timestamp") is not None:
            item["timestamp"] = item["timestamp"].isoformat().replace("+00:00", "Z")
        items.append(item)

    return jsonify(
        {
            "success": True,
            "items": items,
            "pagination": pagination_payload(total, page, limit, total_pages),
        }
    )


@switch_hardware_bp.route("/switches/<device_id>/outages", methods=["GET"])
@require_auth()
def get_switch_outages(device_id: str):
    device, error = _load_eligible_device(device_id)
    if error:
        return error

    page, limit = parse_pagination(default_limit=25, max_limit=100)
    query = {"deviceId": device["_id"]}
    status = (request.args.get("status") or "").strip()
    if status:
        query["status"] = status

    total = db.switch_outage_incidents.count_documents(query)
    page, skip, total_pages = clamp_page(page, total, limit)
    rows = list(
        db.switch_outage_incidents.find(query)
        .sort("startedAt", -1)
        .skip(skip)
        .limit(limit)
    )
    return jsonify(
        {
            "success": True,
            "items": [_serialize_outage(r) for r in rows],
            "pagination": pagination_payload(total, page, limit, total_pages),
        }
    )


@switch_hardware_bp.route("/switches/<device_id>/outages/<incident_id>", methods=["GET"])
@require_auth()
def get_switch_outage_detail(device_id: str, incident_id: str):
    device, error = _load_eligible_device(device_id)
    if error:
        return error
    if not ObjectId.is_valid(incident_id):
        return jsonify({"success": False, "message": "Invalid incident id"}), 400

    doc = db.switch_outage_incidents.find_one(
        {"_id": ObjectId(incident_id), "deviceId": device["_id"]}
    )
    if not doc:
        return jsonify({"success": False, "message": "Outage incident not found"}), 404
    return jsonify({"success": True, "outage": _serialize_outage(doc)})


@switch_hardware_bp.route("/switches/<device_id>/hardware/collect", methods=["POST"])
@require_auth(roles=["admin"])
def collect_switch_hardware(device_id: str):
    device, error = _load_eligible_device(device_id)
    if error:
        return error

    forbidden = {"command", "cli", "rawCommand", "shell"}
    if forbidden.intersection(request.args.keys()) or forbidden.intersection(
        (request.get_json(silent=True) or {}).keys()
    ):
        return jsonify({"success": False, "message": "Arbitrary command input is not allowed"}), 400

    try:
        result = collect_device_hardware(device["_id"], mode="full", source_label="manual")
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "message": "Hardware collection failed"}), 500

    log_audit(
        "switch_hardware_collect",
        entity_type="device",
        entity_id=device_id,
        details={
            "success": result.get("success"),
            "collectionStatus": (result.get("document") or {}).get("collectionStatus"),
            "userId": getattr(g, "user", {}).get("id") if hasattr(g, "user") else None,
        },
    )

    return jsonify(
        {
            "success": bool(result.get("success")),
            "hardware": serialize_hardware_document(result.get("document")),
            "errors": result.get("errors") or [],
        }
    )
