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
from models.location import validate_location
from services.audit_service import log_audit
from services.discovery.identity_management import ownership_for_device_edit
from services.server_hardware import (
    deserialize_server_hardware,
    evaluate_server_hardware_health,
    get_current_server_hardware,
)
from services.server_hardware.collection_state import get_ilo_collection_state
from services.server_hardware.freshness import evaluate_telemetry_freshness
from utils.auth import require_auth
from utils.management_address import (
    IPV4_RE,
    is_ilo_only_device_type,
    normalize_ilo_address,
    normalize_os_ip,
    usable_os_ip,
)
from utils.pagination import clamp_page, pagination_payload, parse_pagination
from utils.serializers import serialize_device

device_bp = Blueprint("devices", __name__)


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

    location = (request.args.get("location") or "").strip()
    if location and location.lower() != "all":
        if location.lower() in ("none", "unassigned", "__none__"):
            query["$and"] = query.get("$and", [])
            query["$and"].append({
                "$or": [
                    {"location": {"$exists": False}},
                    {"location": None},
                    {"location": ""},
                ]
            })
        else:
            canonical = validate_location(location) or location
            if canonical == "Mills":
                query["$and"] = query.get("$and", [])
                query["$and"].append({"$or": [{"location": "Mills"}, {"location": "Mill"}]})
            else:
                query["location"] = canonical

    search = (request.args.get("q") or "").strip()
    if search:
        pattern = re.compile(re.escape(search), re.IGNORECASE)
        query["$or"] = [
            {"hostname": pattern},
            {"ipAddress": pattern},
            {"iloAddress": pattern},
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

        hostname = (data.get("hostname") or "").strip()
        device_type = (data.get("deviceType") or "").strip()
        if not hostname:
            return jsonify({"success": False, "message": "hostname is required"}), 400
        if not device_type:
            return jsonify({"success": False, "message": "deviceType is required"}), 400

        raw_ip = data.get("ipAddress")
        raw_ilo = data.get("iloAddress")
        has_ip_input = raw_ip is not None and str(raw_ip).strip() != ""
        has_ilo_input = raw_ilo is not None and str(raw_ilo).strip() != ""

        if not has_ip_input and not has_ilo_input:
            return jsonify({
                "success": False,
                "message": "At least one of ipAddress or iloAddress is required",
            }), 400

        ip_address = None
        ilo_address = None

        if has_ip_input:
            try:
                ip_address = normalize_os_ip(raw_ip)
            except ValueError as error:
                return jsonify({"success": False, "message": str(error)}), 400
            existing_device = db.devices.find_one({"ipAddress": ip_address})
            if existing_device:
                return jsonify({
                    "success": False,
                    "message": "Device with this IP address already exists",
                }), 409

        if has_ilo_input:
            try:
                ilo_address = normalize_ilo_address(raw_ilo)
            except ValueError as error:
                return jsonify({"success": False, "message": str(error)}), 400
            existing_ilo = db.devices.find_one({"iloAddress": ilo_address})
            if existing_ilo:
                return jsonify({
                    "success": False,
                    "message": "Device with this iLO address already exists",
                }), 409

        if ip_address is None:
            if not is_ilo_only_device_type(device_type):
                return jsonify({
                    "success": False,
                    "message": (
                        "iLO-only devices must use device type "
                        "Server, Linux Server, or ESXi Server"
                    ),
                }), 400

        try:
            credentials = normalize_device_credentials(data.get("credentials"))
        except ValueError as error:
            return jsonify({
                "success": False,
                "message": str(error),
            }), 400

        try:
            location = validate_location(data.get("location"))
        except ValueError as error:
            return jsonify({
                "success": False,
                "message": str(error),
            }), 400

        show_on_dashboard = bool(data.get("showOnDashboard", False))
        if device_type.lower() != "server":
            show_on_dashboard = False

        monitor = bool(data.get("monitor", True))
        if ip_address is None:
            monitor = False

        device = create_device(
            hostname=hostname,
            ip_address=ip_address,
            device_type=device_type,
            critical=bool(data.get("critical", False)),
            monitor=monitor,
            show_on_dashboard=show_on_dashboard,
            ping_interval=_optional_int(data.get("pingInterval")),
            ping_timeout_ms=_optional_int(data.get("pingTimeoutMs")),
            ping_retries=_optional_int(data.get("pingRetries")),
            credentials=credentials,
            location=location,
            ilo_address=ilo_address,
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
                "message": "Device with this IP or iLO address already exists",
            }), 409
        created_device = db.devices.find_one({"_id": result.inserted_id})

        log_audit(
            action="device_created",
            entity_type="device",
            entity_id=result.inserted_id,
            details={
                "hostname": device["hostname"],
                "ipAddress": device.get("ipAddress"),
                "iloAddress": device.get("iloAddress"),
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
            if not isinstance(ip, str) or not ip.strip():
                continue
            parts = ip.strip().split(".")
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


def _format_api_datetime(dt: datetime | None) -> str | None:
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    return None


def _format_collection_state_payload(state: dict) -> dict:
    return {
        "lastAttemptAt": _format_api_datetime(state.get("lastAttemptAt")),
        "lastSuccessAt": _format_api_datetime(state.get("lastSuccessAt")),
        "lastFailureAt": _format_api_datetime(state.get("lastFailureAt")),
        "lastPollStatus": state.get("lastPollStatus") or "NEVER_POLLED",
        "consecutiveFailures": int(state.get("consecutiveFailures") or 0),
        "lastError": state.get("lastError"),
        "updatedAt": _format_api_datetime(state.get("updatedAt")),
    }


@device_bp.route("/devices/<device_id>/server-hardware/health", methods=["GET"])
@require_auth()
def get_device_server_hardware_health(device_id: str):
    try:
        if not ObjectId.is_valid(device_id):
            return jsonify({"success": False, "message": "Invalid device ID"}), 400

        device = db.devices.find_one({"_id": ObjectId(device_id)})
        if not device:
            return jsonify({"success": False, "message": "Device not found"}), 404

        device_type = (device.get("deviceType") or device.get("type") or "").strip()
        eligible_types = ["Server", "Linux Server", "ESXi Server"]
        if device_type not in eligible_types:
            return jsonify({"success": False, "message": "Device is not an eligible server"}), 400

        collection_state = get_ilo_collection_state(device["_id"], database=db)
        snapshot = get_current_server_hardware(device["_id"])

        if not snapshot:
            freshness_eval = evaluate_telemetry_freshness(None, collection_state)
            return jsonify({
                "success": True,
                "data": {
                    "deviceId": str(device["_id"]),
                    "observedAt": None,
                    "freshness": freshness_eval.to_dict(),
                    "collection": _format_collection_state_payload(collection_state),
                    "health": None,
                },
            }), 200

        hardware = deserialize_server_hardware(snapshot)
        health_eval = evaluate_server_hardware_health(hardware)

        observed_at = snapshot.get("observedAt")
        observed_at_str = _format_api_datetime(observed_at)
        freshness_eval = evaluate_telemetry_freshness(observed_at, collection_state)

        health_dict = health_eval.to_dict()

        return jsonify({
            "success": True,
            "data": {
                "deviceId": str(device["_id"]),
                "observedAt": observed_at_str,
                "freshness": freshness_eval.to_dict(),
                "collection": _format_collection_state_payload(collection_state),
                "health": {
                    "overallHealth": health_dict["overall_health"],
                    "powerState": health_dict["power_state"],
                    "summaryReasons": health_dict["summary_reasons"],
                    "subsystems": {
                        k: {
                            "name": v["name"],
                            "status": v["status"],
                            "totalComponents": v["total_components"],
                            "healthyComponents": v["healthy_components"],
                            "warningComponents": v["warning_components"],
                            "criticalComponents": v["critical_components"],
                            "reasons": v["reasons"],
                        }
                        for k, v in health_dict["subsystems"].items()
                    },
                },
            },
        }), 200

    except Exception as error:
        return internal_error_response(error, message="Failed to evaluate server hardware health")


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
            "deviceType",
            "critical",
            "monitor",
            "showOnDashboard",
            "pingInterval",
            "pingTimeoutMs",
            "pingRetries",
            "location",
        ]

        update_data = {}
        unset_fields = {}

        for field in allowed_fields:
            if field in data:
                update_data[field] = data[field]

        if "showOnDashboard" in update_data:
            update_data["showOnDashboard"] = bool(update_data["showOnDashboard"])

        next_device_type = str(
            update_data.get("deviceType", device.get("deviceType") or device.get("type") or "")
        ).strip()
        if next_device_type.lower() != "server":
            update_data["showOnDashboard"] = False
        elif "showOnDashboard" not in update_data and "deviceType" in update_data:
            # Switching into Server without an explicit flag stays off the dashboard.
            update_data["showOnDashboard"] = False

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

        # --- Address updates (omit empty/null; never persist "") ---
        clear_ip = "ipAddress" in data and (
            data.get("ipAddress") is None or str(data.get("ipAddress")).strip() == ""
        )
        set_ip = "ipAddress" in data and not clear_ip
        clear_ilo = "iloAddress" in data and (
            data.get("iloAddress") is None or str(data.get("iloAddress")).strip() == ""
        )
        set_ilo = "iloAddress" in data and not clear_ilo

        next_ip = usable_os_ip(device.get("ipAddress"))
        next_ilo = device.get("iloAddress") if isinstance(device.get("iloAddress"), str) else None
        if next_ilo is not None:
            next_ilo = next_ilo.strip() or None

        if set_ip:
            try:
                next_ip = normalize_os_ip(data.get("ipAddress"))
            except ValueError as error:
                return jsonify({"success": False, "message": str(error)}), 400
            duplicate = db.devices.find_one({
                "ipAddress": next_ip,
                "_id": {"$ne": ObjectId(device_id)},
            })
            if duplicate:
                return jsonify({
                    "success": False,
                    "message": "Another device already uses this IP address",
                }), 409
            update_data["ipAddress"] = next_ip
        elif clear_ip:
            next_ip = None
            unset_fields["ipAddress"] = ""

        if set_ilo:
            try:
                next_ilo = normalize_ilo_address(data.get("iloAddress"))
            except ValueError as error:
                return jsonify({"success": False, "message": str(error)}), 400
            duplicate_ilo = db.devices.find_one({
                "iloAddress": next_ilo,
                "_id": {"$ne": ObjectId(device_id)},
            })
            if duplicate_ilo:
                return jsonify({
                    "success": False,
                    "message": "Another device already uses this iLO address",
                }), 409
            update_data["iloAddress"] = next_ilo
        elif clear_ilo:
            next_ilo = None
            unset_fields["iloAddress"] = ""

        if next_ip is None and next_ilo is None:
            return jsonify({
                "success": False,
                "message": "At least one of ipAddress or iloAddress is required",
            }), 400

        if next_ip is None:
            if not is_ilo_only_device_type(next_device_type):
                return jsonify({
                    "success": False,
                    "message": (
                        "iLO-only devices must use device type "
                        "Server, Linux Server, or ESXi Server"
                    ),
                }), 400
            update_data["monitor"] = False

        if not update_data and not unset_fields:
            return jsonify({
                "success": False,
                "message": "No valid fields provided for update",
            }), 400

        for key in ("pingInterval", "pingTimeoutMs", "pingRetries"):
            if key in update_data:
                if update_data[key] in ("", None):
                    update_data[key] = None
                else:
                    update_data[key] = int(update_data[key])

        if "location" in update_data:
            try:
                update_data["location"] = validate_location(update_data.get("location"))
            except ValueError as error:
                return jsonify({
                    "success": False,
                    "message": str(error),
                }), 400

        # Enabling monitor without a schedule: due immediately for first check.
        # Never enable monitor without a usable OS IP.
        if "monitor" in update_data and bool(update_data["monitor"]):
            if next_ip is None:
                update_data["monitor"] = False
            elif not device.get("monitor") or device.get("nextCheckAt") is None:
                if "nextCheckAt" not in update_data:
                    update_data["nextCheckAt"] = datetime.now(timezone.utc)

        update_data["updatedAt"] = datetime.now(timezone.utc)

        identity_updates = ownership_for_device_edit(device, update_data)
        if identity_updates is not None:
            update_data["identityManagement"] = identity_updates
            if identity_updates.get("deviceType") == "manual" and "deviceType" in update_data:
                update_data["classificationConfidence"] = 100
                update_data["classificationMethod"] = "manual"

        # Never write secrets into the audit trail.
        if "credentials" in update_data:
            audit_details = {
                k: v for k, v in update_data.items() if k != "credentials"
            }
            audit_details["credentialsUpdated"] = True
        else:
            audit_details = dict(update_data)
        if unset_fields:
            audit_details["clearedFields"] = sorted(unset_fields.keys())

        mongo_update = {}
        if update_data:
            mongo_update["$set"] = update_data
        if unset_fields:
            mongo_update["$unset"] = unset_fields

        try:
            db.devices.update_one(
                {"_id": ObjectId(device_id)},
                mongo_update,
            )
        except DuplicateKeyError:
            return jsonify({
                "success": False,
                "message": "Another device already uses this IP or iLO address",
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
