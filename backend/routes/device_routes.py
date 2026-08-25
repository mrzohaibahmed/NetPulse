import csv
import io
import os
import re
from datetime import datetime, timezone

from bson import ObjectId
from utils.api_errors import internal_error_response
from flask import Blueprint, jsonify, request
from pymongo.errors import DuplicateKeyError

from config.database import db
from models.device import create_device, normalize_device_credentials
from services.audit_service import log_audit
from services.discovery.identity_management import ownership_for_device_edit
from utils.auth import require_auth
from utils.pagination import clamp_page, pagination_payload, parse_pagination
from utils.serializers import serialize_device

device_bp = Blueprint("devices", __name__)

IPV4_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)$"
)


def build_device_filter():
    query = {}

    status = (request.args.get("status") or "").strip()
    if status and status.lower() != "all":
        query["status"] = status

    device_type = (request.args.get("deviceType") or "").strip()
    if device_type and device_type.lower() != "all":
        query["$and"] = query.get("$and", [])
        # "switch" matches Switch / Managed Switch / L3 Switch (storm inventory).
        if device_type.lower() == "switch":
            switch_pattern = re.compile(r"switch", re.IGNORECASE)
            query["$and"].append({
                "$or": [
                    {"deviceType": switch_pattern},
                    {"type": switch_pattern},
                ]
            })
        else:
            query["$and"].append({
                "$or": [
                    {"deviceType": device_type},
                    {"type": device_type},
                ]
            })

    critical_raw = (request.args.get("critical") or "").strip().lower()
    if critical_raw in ("true", "1", "yes"):
        query["critical"] = True
    elif critical_raw in ("false", "0", "no"):
        query["critical"] = False

    network = (request.args.get("network") or "").strip()
    if network and network.lower() != "all":
        pattern = re.compile(f"^{re.escape(network)}\.")
        query["$and"] = query.get("$and", [])
        query["$and"].append({"ipAddress": pattern})

    search = (request.args.get("q") or "").strip()
    if search:
        pattern = re.compile(re.escape(search), re.IGNORECASE)
        query["$or"] = [
            {"hostname": pattern},
            {"ipAddress": pattern},
            {"deviceType": pattern},
            {"type": pattern},
        ]

    return query


def _optional_int(value):
    if value is None or value == "":
        return None
    return int(value)


@device_bp.route("/devices", methods=["POST"])
@require_auth(roles=["admin"])
def add_device():
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                "success": False,
                "message": "Request body is required",
            }), 400

        required_fields = ["hostname", "ipAddress", "deviceType"]
        for field in required_fields:
            if not data.get(field):
                return jsonify({
                    "success": False,
                    "message": f"{field} is required",
                }), 400

        if not IPV4_RE.match(str(data["ipAddress"]).strip()):
            return jsonify({
                "success": False,
                "message": "Invalid IPv4 address",
            }), 400

        existing_device = db.devices.find_one({"ipAddress": data["ipAddress"].strip()})
        if existing_device:
            return jsonify({
                "success": False,
                "message": "Device with this IP address already exists",
            }), 409

        try:
            credentials = normalize_device_credentials(data.get("credentials"))
        except ValueError as error:
            return jsonify({
                "success": False,
                "message": str(error),
            }), 400

        device = create_device(
            hostname=data["hostname"].strip(),
            ip_address=data["ipAddress"].strip(),
            device_type=data["deviceType"].strip(),
            critical=bool(data.get("critical", False)),
            monitor=bool(data.get("monitor", True)),
            ping_interval=_optional_int(data.get("pingInterval")),
            ping_timeout_ms=_optional_int(data.get("pingTimeoutMs")),
            ping_retries=_optional_int(data.get("pingRetries")),
            credentials=credentials,
        )

        # Manually created devices should lock identity fields so background
        # discovery doesn't overwrite them.
        device["identityManagement"] = {
            "hostname": "manual",
            "deviceType": "manual",
        }
        device["classificationConfidence"] = 100
        device["classificationMethod"] = "manual"

        try:
            result = db.devices.insert_one(device)
        except DuplicateKeyError:
            return jsonify({
                "success": False,
                "message": "Device with this IP already exists",
            }), 409
        created_device = db.devices.find_one({"_id": result.inserted_id})

        log_audit(
            action="device_created",
            entity_type="device",
            entity_id=result.inserted_id,
            details={
                "hostname": device["hostname"],
                "ipAddress": device["ipAddress"],
            },
        )

        return jsonify({
            "success": True,
            "message": "Device created successfully",
            "data": serialize_device(created_device),
        }), 201

    except Exception as error:
        return internal_error_response(error, message="Failed to create device")


@device_bp.route("/devices/import", methods=["POST"])
@require_auth(roles=["admin"])
def import_devices_csv():
    """Bulk import devices from CSV (FR1.3)."""
    try:
        if "file" not in request.files:
            return jsonify({
                "success": False,
                "message": "CSV file is required (form field: file)",
            }), 400

        upload = request.files["file"]
        if not upload.filename:
            return jsonify({"success": False, "message": "Empty filename"}), 400

        # Explicit CSV size cap (also covered by Flask MAX_CONTENT_LENGTH).
        max_csv_bytes = int(os.getenv("MAX_CSV_UPLOAD_BYTES", str(1024 * 1024)))
        max_csv_bytes = max(max_csv_bytes, 1024)
        # Prefer Content-Length when present to reject before buffering.
        content_length = request.content_length
        if content_length is not None and content_length > max_csv_bytes:
            return jsonify({
                "success": False,
                "message": f"CSV upload exceeds maximum size ({max_csv_bytes} bytes)",
            }), 413

        raw = upload.read(max_csv_bytes + 1)
        if len(raw) > max_csv_bytes:
            return jsonify({
                "success": False,
                "message": f"CSV upload exceeds maximum size ({max_csv_bytes} bytes)",
            }), 413
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return jsonify({"success": False, "message": "CSV has no header row"}), 400

        # Normalize headers
        field_map = {name.strip().lower(): name for name in reader.fieldnames}
        required = ["hostname", "ipaddress", "devicetype"]
        for key in required:
            if key not in field_map:
                return jsonify({
                    "success": False,
                    "message": (
                        "CSV must include hostname, ipAddress, deviceType columns "
                        "(critical and monitor optional)"
                    ),
                }), 400

        created = 0
        skipped = 0
        errors = []

        for index, row in enumerate(reader, start=2):
            hostname = (row.get(field_map["hostname"]) or "").strip()
            ip_address = (row.get(field_map["ipaddress"]) or "").strip()
            device_type = (row.get(field_map["devicetype"]) or "").strip()

            critical_raw = ""
            monitor_raw = ""
            if "critical" in field_map:
                critical_raw = (row.get(field_map["critical"]) or "").strip().lower()
            if "monitor" in field_map:
                monitor_raw = (row.get(field_map["monitor"]) or "").strip().lower()

            if not hostname or not ip_address or not device_type:
                errors.append({"row": index, "error": "Missing required fields"})
                skipped += 1
                continue

            if not IPV4_RE.match(ip_address):
                errors.append({"row": index, "error": f"Invalid IP: {ip_address}"})
                skipped += 1
                continue

            if db.devices.find_one({"ipAddress": ip_address}):
                errors.append({"row": index, "error": f"Duplicate IP: {ip_address}"})
                skipped += 1
                continue

            critical = critical_raw in ("1", "true", "yes", "y")
            monitor = True if monitor_raw == "" else monitor_raw in ("1", "true", "yes", "y")

            device = create_device(
                hostname=hostname,
                ip_address=ip_address,
                device_type=device_type,
                critical=critical,
                monitor=monitor,
            )
            try:
                db.devices.insert_one(device)
                created += 1
            except DuplicateKeyError:
                errors.append({"row": index, "error": f"Duplicate IP: {ip_address}"})
                skipped += 1
                continue

        log_audit(
            action="devices_imported",
            entity_type="device",
            details={"created": created, "skipped": skipped},
        )

        return jsonify({
            "success": True,
            "message": f"Import complete: {created} created, {skipped} skipped",
            "created": created,
            "skipped": skipped,
            "errors": errors[:50],
        }), 200

    except Exception as error:
        return internal_error_response(error, message="Failed to import devices")


@device_bp.route("/devices", methods=["GET"])
@require_auth()
def get_devices():
    try:
        page, limit = parse_pagination(default_limit=25, max_limit=500)
        filters = build_device_filter()
        total = db.devices.count_documents(filters)
        page, skip, total_pages = clamp_page(page, total, limit)

        import ipaddress

        def ip_sort_key(doc):
            try:
                return int(ipaddress.IPv4Address(doc.get("ipAddress", "0.0.0.0")))
            except Exception:
                return 0

        all_devices = list(db.devices.find(filters))
        all_devices.sort(key=ip_sort_key)
        devices = all_devices[skip : skip + limit]

        return jsonify({
            "success": True,
            "count": len(devices),
            "data": [serialize_device(device) for device in devices],
            **pagination_payload(total, page, limit, total_pages),
        }), 200

    except Exception as error:
        return internal_error_response(error, message="Failed to get devices")


@device_bp.route("/devices/networks", methods=["GET"])
@require_auth()
def get_device_networks():
    try:
        ips = db.devices.distinct("ipAddress")
        subnets = set()
        for ip in ips:
            parts = ip.split(".")
            if len(parts) == 4:
                subnets.add(f"{parts[0]}.{parts[1]}.{parts[2]}")
        
        import ipaddress
        def subnet_sort_key(s):
            try:
                return int(ipaddress.IPv4Address(f"{s}.0"))
            except Exception:
                return 0
                
        sorted_subnets = sorted(list(subnets), key=subnet_sort_key)
        
        return jsonify({
            "success": True,
            "data": sorted_subnets
        }), 200
    except Exception as error:
        return internal_error_response(error, message="Failed to get device networks")


@device_bp.route("/devices/<device_id>", methods=["GET"])
@require_auth()
def get_device(device_id):
    try:
        if not ObjectId.is_valid(device_id):
            return jsonify({"success": False, "message": "Invalid device ID"}), 400

        device = db.devices.find_one({"_id": ObjectId(device_id)})
        if not device:
            return jsonify({"success": False, "message": "Device not found"}), 404

        return jsonify({
            "success": True,
            "data": serialize_device(device),
        }), 200

    except Exception as error:
        return internal_error_response(error, message="Failed to get device")


@device_bp.route("/devices/<device_id>", methods=["PUT"])
@require_auth(roles=["admin"])
def update_device(device_id):
    try:
        if not ObjectId.is_valid(device_id):
            return jsonify({
                "success": False,
                "message": "Invalid device ID",
            }), 400

        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "message": "Request body is required",
            }), 400

        device = db.devices.find_one({"_id": ObjectId(device_id)})
        if not device:
            return jsonify({
                "success": False,
                "message": "Device not found",
            }), 404

        allowed_fields = [
            "hostname",
            "ipAddress",
            "deviceType",
            "critical",
            "monitor",
            "pingInterval",
            "pingTimeoutMs",
            "pingRetries",
        ]

        update_data = {}
        for field in allowed_fields:
            if field in data:
                update_data[field] = data[field]

        if "credentials" in data:
            try:
                update_data["credentials"] = normalize_device_credentials(
                    data.get("credentials"),
                    existing=device.get("credentials"),
                )
            except ValueError as error:
                return jsonify({
                    "success": False,
                    "message": str(error),
                }), 400

            # Never write secrets into the audit trail.
            audit_details = {
                k: v for k, v in update_data.items() if k != "credentials"
            }
            audit_details["credentialsUpdated"] = True
        else:
            audit_details = update_data

        if not update_data:
            return jsonify({
                "success": False,
                "message": "No valid fields provided for update",
            }), 400

        if "ipAddress" in update_data:
            ip_address = str(update_data["ipAddress"]).strip()
            if not IPV4_RE.match(ip_address):
                return jsonify({
                    "success": False,
                    "message": "Invalid IPv4 address",
                }), 400
            update_data["ipAddress"] = ip_address

            duplicate = db.devices.find_one({
                "ipAddress": ip_address,
                "_id": {"$ne": ObjectId(device_id)},
            })
            if duplicate:
                return jsonify({
                    "success": False,
                    "message": "Another device already uses this IP address",
                }), 409

        for key in ("pingInterval", "pingTimeoutMs", "pingRetries"):
            if key in update_data:
                if update_data[key] in ("", None):
                    update_data[key] = None
                else:
                    update_data[key] = int(update_data[key])

        # Enabling monitor without a schedule: due immediately for first check.
        if "monitor" in update_data and bool(update_data["monitor"]):
            if not device.get("monitor") or device.get("nextCheckAt") is None:
                if "nextCheckAt" not in update_data:
                    update_data["nextCheckAt"] = datetime.now(timezone.utc)

        update_data["updatedAt"] = datetime.now(timezone.utc)

        identity_updates = ownership_for_device_edit(device, update_data)
        if identity_updates is not None:
            update_data["identityManagement"] = identity_updates
            if identity_updates.get("deviceType") == "manual" and "deviceType" in update_data:
                update_data["classificationConfidence"] = 100
                update_data["classificationMethod"] = "manual"

        try:
            db.devices.update_one(
                {"_id": ObjectId(device_id)},
                {"$set": update_data},
            )
        except DuplicateKeyError:
            return jsonify({
                "success": False,
                "message": "Another device already uses this IP address",
            }), 409

        updated_device = db.devices.find_one({"_id": ObjectId(device_id)})

        log_audit(
            action="device_updated",
            entity_type="device",
            entity_id=device_id,
            details=audit_details,
        )

        return jsonify({
            "success": True,
            "message": "Device updated successfully",
            "data": serialize_device(updated_device),
        }), 200

    except Exception as error:
        return internal_error_response(error, message="Failed to update device")


@device_bp.route("/devices/<device_id>", methods=["DELETE"])
@require_auth(roles=["admin"])
def delete_device(device_id):
    try:
        if not ObjectId.is_valid(device_id):
            return jsonify({
                "success": False,
                "message": "Invalid device ID",
            }), 400

        oid = ObjectId(device_id)
        device = db.devices.find_one({"_id": oid})
        if not device:
            return jsonify({
                "success": False,
                "message": "Device not found",
            }), 404

        from services.device_cleanup import cascade_delete_device  # noqa: PLC0415

        cascade = cascade_delete_device(oid)
        if cascade.get("deviceDeleted", 0) < 1:
            return jsonify({
                "success": False,
                "message": "Device not found",
            }), 404

        log_audit(
            action="device_deleted",
            entity_type="device",
            entity_id=device_id,
            details={
                "hostname": device.get("hostname"),
                "ipAddress": device.get("ipAddress"),
                "cascadeMode": cascade.get("mode"),
                "relatedDeleted": cascade.get("relatedDeleted"),
                "cascadeErrors": cascade.get("errors") or [],
            },
        )

        return jsonify({
            "success": True,
            "message": "Device deleted successfully",
        }), 200

    except Exception as error:
        return internal_error_response(error, message="Failed to delete device")
