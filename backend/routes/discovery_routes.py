import ipaddress
import re
from utils.api_errors import internal_error_response
from flask import Blueprint, jsonify, request
from bson import ObjectId

from config.database import db
from services.discovery_service import (
    ActiveNetworkScanError,
    get_local_network_hint,
    start_network_scan_job,
)
from utils.auth import require_auth
from utils.secret_crypto import encrypt_secret
from utils.serializers import format_datetime
from utils.utc import utc_now
from utils.ip_parser import parse_scan_targets

discovery_bp = Blueprint("discovery", __name__)

IPV4_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d?\d)$"
)


# ── Network Stats Helper ──────────────────────────────────────────────────────

def calculate_network_stats(cidr_str: str) -> dict:
    """Calculate device metrics for a network's CIDR dynamically."""
    try:
        net = ipaddress.IPv4Network(cidr_str.strip(), strict=False)
    except Exception:
        return {"devices": 0, "switches": 0, "online": 0}

    # Fetch active devices in inventory
    devices = list(db.devices.find({}, {"ipAddress": 1, "deviceType": 1, "status": 1}))
    
    device_count = 0
    switch_count = 0
    online_count = 0
    
    for d in devices:
        ip_str = d.get("ipAddress")
        if not ip_str:
            continue
        try:
            ip = ipaddress.IPv4Address(ip_str)
            if ip in net:
                device_count += 1
                device_type = str(d.get("deviceType") or "").lower()
                if "switch" in device_type:
                    switch_count += 1
                if d.get("status") == "Online":
                    online_count += 1
        except Exception:
            pass
            
    return {
        "devices": device_count,
        "switches": switch_count,
        "online": online_count
    }


def serialize_network(network: dict) -> dict:
    stats = calculate_network_stats(network.get("cidr", ""))
    return {
        "id": str(network["_id"]),
        "name": network.get("name"),
        "type": network.get("type", "ETHERNET"),
        "cidr": network.get("cidr"),
        "scanTargets": network.get("scanTargets"),
        "gateway": network.get("gateway"),
        "description": network.get("description", ""),
        "enabled": bool(network.get("enabled", True)),
        "sshUsername": network.get("sshUsername", ""),
        "sshPasswordSet": bool(network.get("sshPassword")),
        "snmpCommunityConfigured": bool(network.get("snmpCommunity")),
        "createdAt": format_datetime(network.get("createdAt")),
        "updatedAt": format_datetime(network.get("updatedAt")),
        "devices": stats["devices"],
        "switches": stats["switches"],
        "online": stats["online"]
    }


# ── CRUD Endpoints for Configured Networks ────────────────────────────────────

@discovery_bp.route("/networks", methods=["GET"])
@require_auth()
def list_networks():
    try:
        networks = list(db.networks.find().sort("name", 1))
        return jsonify({
            "success": True,
            "data": [serialize_network(n) for n in networks]
        }), 200
    except Exception as error:
        return internal_error_response(error, message="Failed to list networks")


@discovery_bp.route("/networks", methods=["POST"])
@require_auth(roles=["admin"])
def create_network():
    try:
        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        net_type = (data.get("type") or "ETHERNET").strip().upper()
        cidr = (data.get("cidr") or "").strip()
        scan_targets = (data.get("scanTargets") or "").strip()
        gateway = (data.get("gateway") or "").strip()
        description = (data.get("description") or "").strip()
        enabled = bool(data.get("enabled", True))
        ssh_username = (data.get("sshUsername") or "").strip()
        ssh_password = data.get("sshPassword") or ""
        snmp_community = (data.get("snmpCommunity") or "public").strip()

        if not name:
            return jsonify({"success": False, "message": "Name is required"}), 400
        if not cidr:
            return jsonify({"success": False, "message": "CIDR is required"}), 400
        if not scan_targets:
            return jsonify({"success": False, "message": "Scan Targets is required"}), 400

        # Validate CIDR
        try:
            ipaddress.IPv4Network(cidr, strict=False)
        except ValueError as err:
            return jsonify({"success": False, "message": f"Invalid CIDR: {err}"}), 400

        # Validate Scan Targets parsing
        try:
            parse_scan_targets(scan_targets)
        except ValueError as err:
            return jsonify({"success": False, "message": str(err)}), 400

        # Validate Gateway if provided
        if gateway:
            try:
                ipaddress.IPv4Address(gateway)
            except ValueError:
                return jsonify({"success": False, "message": "Invalid Gateway IP address"}), 400

        now = utc_now()
        doc = {
            "name": name,
            "type": net_type,
            "cidr": cidr,
            "scanTargets": scan_targets,
            "gateway": gateway,
            "description": description,
            "enabled": enabled,
            "sshUsername": ssh_username,
            "sshPassword": encrypt_secret(ssh_password) if ssh_password else "",
            "snmpCommunity": encrypt_secret(snmp_community) if snmp_community else "",
            "createdAt": now,
            "updatedAt": now
        }

        result = db.networks.insert_one(doc)
        doc["_id"] = result.inserted_id

        return jsonify({
            "success": True,
            "message": "Network added successfully",
            "data": serialize_network(doc)
        }), 201

    except Exception as error:
        return internal_error_response(error, message="Failed to add network")


@discovery_bp.route("/networks/<network_id>", methods=["PUT"])
@require_auth(roles=["admin"])
def update_network(network_id):
    try:
        if not ObjectId.is_valid(network_id):
            return jsonify({"success": False, "message": "Invalid network ID"}), 400

        network = db.networks.find_one({"_id": ObjectId(network_id)})
        if not network:
            return jsonify({"success": False, "message": "Network not found"}), 404

        data = request.get_json() or {}
        update = {}

        if "name" in data:
            name = str(data["name"]).strip()
            if not name:
                return jsonify({"success": False, "message": "Name cannot be empty"}), 400
            update["name"] = name

        if "type" in data:
            update["type"] = str(data["type"]).strip().upper()

        if "cidr" in data:
            cidr = str(data["cidr"]).strip()
            if not cidr:
                return jsonify({"success": False, "message": "CIDR cannot be empty"}), 400
            try:
                ipaddress.IPv4Network(cidr, strict=False)
            except ValueError as err:
                return jsonify({"success": False, "message": f"Invalid CIDR: {err}"}), 400
            update["cidr"] = cidr

        if "scanTargets" in data:
            scan_targets = str(data["scanTargets"]).strip()
            if not scan_targets:
                return jsonify({"success": False, "message": "Scan Targets cannot be empty"}), 400
            try:
                parse_scan_targets(scan_targets)
            except ValueError as err:
                return jsonify({"success": False, "message": str(err)}), 400
            update["scanTargets"] = scan_targets

        if "gateway" in data:
            gateway = str(data["gateway"]).strip()
            if gateway:
                try:
                    ipaddress.IPv4Address(gateway)
                except ValueError:
                    return jsonify({"success": False, "message": "Invalid Gateway IP address"}), 400
            update["gateway"] = gateway

        if "description" in data:
            update["description"] = str(data["description"]).strip()

        if "enabled" in data:
            update["enabled"] = bool(data["enabled"])

        if "sshUsername" in data:
            update["sshUsername"] = str(data["sshUsername"]).strip()

        if "sshPassword" in data and data["sshPassword"]:
            update["sshPassword"] = encrypt_secret(str(data["sshPassword"]))

        if "snmpCommunity" in data:
            community = str(data["snmpCommunity"]).strip()
            update["snmpCommunity"] = (
                encrypt_secret(community) if community else ""
            )

        if update:
            update["updatedAt"] = utc_now()
            db.networks.update_one({"_id": ObjectId(network_id)}, {"$set": update})

        updated = db.networks.find_one({"_id": ObjectId(network_id)})
        return jsonify({
            "success": True,
            "message": "Network updated successfully",
            "data": serialize_network(updated)
        }), 200

    except Exception as error:
        return internal_error_response(error, message="Failed to update network")


@discovery_bp.route("/networks/<network_id>", methods=["DELETE"])
@require_auth(roles=["admin"])
def delete_network(network_id):
    try:
        if not ObjectId.is_valid(network_id):
            return jsonify({"success": False, "message": "Invalid network ID"}), 400

        res = db.networks.delete_one({"_id": ObjectId(network_id)})
        if res.deleted_count == 0:
            return jsonify({"success": False, "message": "Network not found"}), 404

        return jsonify({
            "success": True,
            "message": "Network deleted successfully"
        }), 200

    except Exception as error:
        return internal_error_response(error, message="Failed to delete network")


# ── Scan Range & Scan Networks Routing ────────────────────────────────────────

@discovery_bp.route("/discovery/network-hint", methods=["GET"])
@require_auth()
def network_hint():
    try:
        hint = get_local_network_hint()
        if hint is None:
            return jsonify({
                "success": False,
                "message": "Could not detect local network",
            }), 404
        return jsonify({
            "success": True,
            "hint": hint,
        }), 200
    except Exception as error:
        return internal_error_response(error, message="Failed to detect local network")


@discovery_bp.route("/discovery/scan-range", methods=["POST"])
@require_auth(roles=["admin"])
def scan_range():
    try:
        data = request.get_json() or {}
        start_ip = data.get("startIP")
        end_ip = data.get("endIP")

        if not start_ip or not end_ip:
            return jsonify({
                "success": False,
                "message": "startIP and endIP are required",
            }), 400

        from services.discovery_service import discover_devices
        scan_id = data.get("scanId")
        devices = discover_devices(start_ip, end_ip, scan_id=scan_id)

        online = sum(1 for device in devices if device["status"] == "Online")
        offline = sum(1 for device in devices if device["status"] == "Offline")
        newly_saved = sum(1 for device in devices if device["saved"])

        return jsonify({
            "success": True,
            "summary": {
                "totalScanned": len(devices),
                "online": online,
                "offline": offline,
                "newlySaved": newly_saved,
            },
            "devices": devices,
        }), 200

    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error),
        }), 400
    except Exception as error:
        return internal_error_response(error, message="Failed to scan IP range")


@discovery_bp.route("/discovery/discover-device", methods=["POST"])
@require_auth(roles=["admin"])
def discover_device():
    """Ping a single IP and auto-save if online (Nmap enrichment runs in background)."""
    try:
        data = request.get_json() or {}
        ip_address = (data.get("ipAddress") or "").strip()

        if not ip_address:
            return jsonify({
                "success": False,
                "message": "ipAddress is required",
            }), 400

        if not IPV4_RE.match(ip_address):
            return jsonify({
                "success": False,
                "message": "Invalid IPv4 address",
            }), 400

        from services.discovery_service import scan_single_ip

        device_row = scan_single_ip(ip_address)
        online = 1 if device_row.get("status") == "Online" else 0
        offline = 0 if online else 1
        newly_saved = 1 if device_row.get("saved") else 0

        return jsonify({
            "success": True,
            "summary": {
                "totalScanned": 1,
                "online": online,
                "offline": offline,
                "newlySaved": newly_saved,
            },
            "devices": [device_row],
        }), 200
    except Exception as error:
        return internal_error_response(error, message="Failed to discover device")


@discovery_bp.route("/discovery/enrichment-status", methods=["POST"])
@require_auth(roles=["admin"])
def enrichment_status():
    """Return discovery enrichment status for a set of IP addresses."""
    try:
        data = request.get_json() or {}
        ip_addresses = data.get("ipAddresses") or []

        if not isinstance(ip_addresses, list) or not ip_addresses:
            return jsonify({
                "success": False,
                "message": "ipAddresses must be a non-empty array",
            }), 400

        normalized = []
        for raw in ip_addresses:
            ip = str(raw).strip()
            if ip and IPV4_RE.match(ip):
                normalized.append(ip)

        if not normalized:
            return jsonify({
                "success": False,
                "message": "No valid IPv4 addresses provided",
            }), 400

        cursor = db.devices.find(
            {"ipAddress": {"$in": normalized}},
            {
                "ipAddress": 1,
                "hostname": 1,
                "deviceType": 1,
                "vendor": 1,
                "operatingSystem": 1,
                "classificationConfidence": 1,
                "classificationMethod": 1,
                "discoveryStatus": 1,
                "discoveryEnrichmentError": 1,
            },
        )

        devices = []
        for doc in cursor:
            devices.append({
                "ipAddress": doc.get("ipAddress"),
                "hostname": doc.get("hostname"),
                "deviceType": doc.get("deviceType"),
                "vendor": doc.get("vendor"),
                "operatingSystem": doc.get("operatingSystem"),
                "classificationConfidence": doc.get("classificationConfidence"),
                "classificationMethod": doc.get("classificationMethod"),
                "discoveryStatus": doc.get("discoveryStatus"),
                "discoveryEnrichmentError": doc.get("discoveryEnrichmentError"),
            })

        return jsonify({
            "success": True,
            "devices": devices,
        }), 200
    except Exception as error:
        return internal_error_response(error, message="Failed to fetch enrichment status")


@discovery_bp.route("/discovery/scan-progress/<scan_id>", methods=["GET"])
@require_auth(roles=["admin"])
def scan_progress(scan_id):
    """Return live ping-sweep progress for an in-flight discovery scan."""
    try:
        from services.discovery_service import get_scan_progress, is_valid_scan_id

        if not is_valid_scan_id(scan_id):
            return jsonify({
                "success": False,
                "message": "Invalid scanId",
            }), 400

        progress = get_scan_progress(scan_id)
        if progress is None:
            return jsonify({
                "success": True,
                "progress": {
                    "scanId": scan_id,
                    "status": "pending",
                    "total": 0,
                    "completed": 0,
                    "online": 0,
                    "newlySaved": 0,
                    "elapsedSeconds": 0,
                    "percent": 0,
                },
            }), 200

        return jsonify({"success": True, "progress": progress}), 200
    except Exception as error:
        return internal_error_response(error, message="Failed to fetch scan progress")


@discovery_bp.route("/discovery/scan-networks", methods=["POST"])
@require_auth(roles=["admin"])
def scan_networks():
    """Start a background network scan and return immediately (HTTP 202)."""
    try:
        data = request.get_json() or {}
        network_ids = data.get("networkIds")
        scan_all_enabled = bool(data.get("scanAllEnabled", False))

        target_networks = []
        if scan_all_enabled:
            target_networks = list(db.networks.find({"enabled": True}))
        elif network_ids:
            # Map strings to ObjectIds safely
            valid_ids = [ObjectId(nid) for nid in network_ids if ObjectId.is_valid(nid)]
            if valid_ids:
                target_networks = list(db.networks.find({"_id": {"$in": valid_ids}}))

        if not target_networks:
            return jsonify({
                "success": False,
                "message": "No valid networks found to scan",
            }), 400

        # Collect and parse all targets
        combined_targets = []
        for net in target_networks:
            targets_str = net.get("scanTargets", "")
            if targets_str:
                combined_targets.append(targets_str)

        # Resolve to flat unique IP list
        try:
            resolved_ips = parse_scan_targets(",".join(combined_targets))
        except ValueError as err:
            return jsonify({
                "success": False,
                "message": str(err),
            }), 400

        if not resolved_ips:
            return jsonify({
                "success": False,
                "message": "No IP addresses resolved from the selected scan targets",
            }), 400

        if len(resolved_ips) > 1024:
            return jsonify({
                "success": False,
                "message": "Scan target list is too large. Maximum 1024 addresses per scan.",
            }), 400

        try:
            scan_id = start_network_scan_job(resolved_ips)
        except ActiveNetworkScanError as conflict:
            return jsonify({
                "success": False,
                "code": "scan_in_progress",
                "scanId": conflict.scan_id,
                "status": "running",
                "message": "A network scan is already in progress",
            }), 409

        return jsonify({
            "success": True,
            "scanId": scan_id,
            "status": "running",
            "message": "Network scan started",
        }), 202

    except Exception as error:
        return internal_error_response(error, message="Failed to scan networks")
