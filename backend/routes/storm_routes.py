"""
storm_routes.py
===============
REST API for Storm Protection.

Eligibility
-----------
GET  /api/storm/eligibility
GET  /api/storm/eligibility/<device_id>
GET  /api/storm/eligibility/<device_id>/<interface>
POST /api/storm/eligibility/evaluate
POST /api/storm/eligibility/evaluate-all

Risk Score
----------
GET  /api/storm/risk
GET  /api/storm/risk/<device_id>
GET  /api/storm/risk/<device_id>/<interface>
POST /api/storm/risk/calculate
POST /api/storm/risk/calculate-all

GET  /api/storm/config
"""

from __future__ import annotations

from urllib.parse import unquote

from bson import ObjectId
from flask import Blueprint, jsonify, request

from config.database import db
from services.storm.config import storm_config_as_dict
from services.storm.eligibility import (
    evaluate_all_interfaces,
    evaluate_and_store,
    get_eligibility_history,
    get_latest_eligibility_results,
)
from services.storm.exceptions import (
    InvalidInterfaceDataError,
    MissingInterfaceError,
    StormEligibilityError,
)
from services.storm.risk_engine import (
    calculate_all_risks,
    calculate_risk,
    get_latest_risk_results,
    get_risk_history,
)
from utils.auth import require_auth
from utils.pagination import clamp_page, pagination_payload, parse_pagination
from utils.serializers import serialize_eligibility_result, serialize_risk_result

storm_bp = Blueprint("storm", __name__)


def _parse_device_id(device_id: str):
    if not ObjectId.is_valid(device_id):
        return None
    return ObjectId(device_id)


def _enrich_with_classification(row: dict) -> dict:
    """Attach current port classification flags from interfaces inventory."""
    device_id = row.get("deviceId")
    name = row.get("interface")
    if device_id is None or not name:
        return serialize_eligibility_result(row)

    iface = db.interfaces.find_one(
        {"deviceId": device_id, "name": name},
        {
            "isAccess": 1,
            "isTrunk": 1,
            "isUplink": 1,
            "isInfrastructure": 1,
            "isManagement": 1,
            "isProtected": 1,
            "portMode": 1,
            "mode": 1,
            "monitoringEnabled": 1,
            "adminStatus": 1,
            "operStatus": 1,
            "hostname": 1,
            "ipAddress": 1,
        },
    )
    return serialize_eligibility_result(row, interface=iface)


@storm_bp.route("/storm/config", methods=["GET"])
@require_auth()
def get_storm_config_route():
    return jsonify({
        "success": True,
        "data": storm_config_as_dict(),
    }), 200


@storm_bp.route("/storm/eligibility/evaluate", methods=["POST"])
@require_auth(roles=["admin"])
def evaluate_single():
    """
    Evaluate a single interface.

    Body may contain either:
    - full interface metadata, or
    - ``{ "deviceId": "...", "interface": "Gi1/0/5" }`` to load from MongoDB.
    """
    try:
        body = request.get_json(silent=True) or {}
        interface_doc = None

        device_id = body.get("deviceId") or body.get("device_id")
        name = (
            body.get("interface")
            or body.get("name")
            or body.get("interfaceName")
        )

        # Prefer loading from Mongo when identifiers are provided without
        # full classification metadata.
        if device_id and name and "adminStatus" not in body and "admin_status" not in body:
            if not ObjectId.is_valid(str(device_id)):
                return jsonify({
                    "success": False,
                    "message": "Invalid device ID",
                }), 400
            interface_doc = db.interfaces.find_one({
                "deviceId": ObjectId(str(device_id)),
                "name": str(name).strip(),
            })
            if interface_doc is None:
                return jsonify({
                    "success": False,
                    "message": "Interface not found",
                }), 404
        else:
            interface_doc = body
            if not interface_doc:
                return jsonify({
                    "success": False,
                    "message": "Interface payload is required",
                }), 400

        result = evaluate_and_store(interface_doc)
        return jsonify({
            "success": True,
            "message": (
                "Eligibility Passed"
                if result.eligible
                else f"Eligibility Failed — {result.reason}"
            ),
            "data": result.to_api_dict(),
        }), 200

    except (MissingInterfaceError, InvalidInterfaceDataError) as error:
        return jsonify({
            "success": False,
            "message": str(error),
        }), 400
    except StormEligibilityError as error:
        return jsonify({
            "success": False,
            "message": str(error),
        }), 400
    except Exception as error:  # noqa: BLE001
        return jsonify({
            "success": False,
            "message": "Failed to evaluate interface eligibility",
            "error": str(error),
        }), 500


@storm_bp.route("/storm/eligibility/evaluate-all", methods=["POST"])
@require_auth(roles=["admin"])
def evaluate_all():
    """Manually trigger bulk eligibility evaluation for all interfaces."""
    try:
        summary = evaluate_all_interfaces()
        return jsonify({
            "success": True,
            "message": (
                "Eligibility evaluation skipped (disabled)"
                if summary.get("skipped")
                else (
                    f"Eligibility evaluation completed: "
                    f"{summary['eligible']} eligible / "
                    f"{summary['ineligible']} ineligible "
                    f"({summary['total']} interface(s))"
                )
            ),
            "total": summary["total"],
            "eligible": summary["eligible"],
            "ineligible": summary["ineligible"],
            "errors": summary["errors"],
            "skipped": summary.get("skipped", False),
        }), 200
    except Exception as error:  # noqa: BLE001
        return jsonify({
            "success": False,
            "message": "Failed to run bulk eligibility evaluation",
            "error": str(error),
        }), 500


def _eligibility_filters():
    eligible_raw = (request.args.get("eligible") or "").strip().lower()
    eligible = None
    if eligible_raw in ("true", "1", "yes"):
        eligible = True
    elif eligible_raw in ("false", "0", "no"):
        eligible = False

    return {
        "search": (request.args.get("q") or "").strip() or None,
        "eligible": eligible,
    }


@storm_bp.route("/storm/eligibility", methods=["GET"])
@require_auth()
def list_eligibility():
    """Return latest eligibility evaluations (one per interface)."""
    try:
        page, limit = parse_pagination(default_limit=50, max_limit=500)
        filters = _eligibility_filters()
        _, total = get_latest_eligibility_results(skip=0, limit=1, **filters)
        page, skip, total_pages = clamp_page(page, total, limit)
        rows, total = get_latest_eligibility_results(
            skip=skip, limit=limit, **filters
        )

        return jsonify({
            "success": True,
            "count": len(rows),
            "data": [_enrich_with_classification(row) for row in rows],
            **pagination_payload(total, page, limit, total_pages),
        }), 200
    except Exception as error:  # noqa: BLE001
        return jsonify({
            "success": False,
            "message": "Failed to list eligibility results",
            "error": str(error),
        }), 500


@storm_bp.route("/storm/eligibility/<device_id>", methods=["GET"])
@require_auth()
def list_device_eligibility(device_id: str):
    """Return latest eligibility evaluations for one device."""
    try:
        oid = _parse_device_id(device_id)
        if oid is None:
            return jsonify({
                "success": False,
                "message": "Invalid device ID",
            }), 400

        page, limit = parse_pagination(default_limit=50, max_limit=500)
        _, total = get_latest_eligibility_results(device_id=oid, skip=0, limit=1)
        page, skip, total_pages = clamp_page(page, total, limit)
        rows, total = get_latest_eligibility_results(
            device_id=oid, skip=skip, limit=limit
        )

        return jsonify({
            "success": True,
            "count": len(rows),
            "data": [_enrich_with_classification(row) for row in rows],
            **pagination_payload(total, page, limit, total_pages),
        }), 200
    except Exception as error:  # noqa: BLE001
        return jsonify({
            "success": False,
            "message": "Failed to list device eligibility results",
            "error": str(error),
        }), 500


@storm_bp.route(
    "/storm/eligibility/<device_id>/<path:interface>",
    methods=["GET"],
)
@require_auth()
def get_interface_eligibility(device_id: str, interface: str):
    """Return latest evaluation (and optional history) for one interface."""
    try:
        oid = _parse_device_id(device_id)
        if oid is None:
            return jsonify({
                "success": False,
                "message": "Invalid device ID",
            }), 400

        name = unquote(interface).strip()
        if not name:
            return jsonify({
                "success": False,
                "message": "Interface name is required",
            }), 400

        include_history = (
            (request.args.get("history") or "").strip().lower()
            in ("1", "true", "yes")
        )
        page, limit = parse_pagination(default_limit=25, max_limit=200)

        latest_rows, _ = get_latest_eligibility_results(
            device_id=oid, interface=name, skip=0, limit=1
        )
        if not latest_rows:
            return jsonify({
                "success": False,
                "message": "No eligibility evaluation found for this interface",
            }), 404

        payload = {
            "success": True,
            "data": _enrich_with_classification(latest_rows[0]),
        }

        if include_history:
            history, total = get_eligibility_history(
                oid, name, skip=(page - 1) * limit, limit=limit
            )
            total_pages = (total + limit - 1) // limit if total else 0
            payload["history"] = [
                serialize_eligibility_result(row) for row in history
            ]
            payload.update(pagination_payload(total, page, limit, total_pages))

        return jsonify(payload), 200
    except Exception as error:  # noqa: BLE001
        return jsonify({
            "success": False,
            "message": "Failed to fetch interface eligibility",
            "error": str(error),
        }), 500


# ---------------------------------------------------------------------------
# Risk Score Engine
# ---------------------------------------------------------------------------


def _risk_filters():
    severity = (request.args.get("severity") or "").strip() or None
    return {
        "search": (request.args.get("q") or "").strip() or None,
        "severity": severity,
    }


@storm_bp.route("/storm/risk/calculate", methods=["POST"])
@require_auth(roles=["admin"])
def calculate_single_risk():
    """Calculate risk for one interface from MongoDB stats + eligibility."""
    try:
        body = request.get_json(silent=True) or {}
        device_id = body.get("deviceId") or body.get("device_id")
        name = (
            body.get("interface")
            or body.get("name")
            or body.get("interfaceName")
        )
        if not device_id or not name:
            return jsonify({
                "success": False,
                "message": "deviceId and interface are required",
            }), 400
        if not ObjectId.is_valid(str(device_id)):
            return jsonify({
                "success": False,
                "message": "Invalid device ID",
            }), 400

        oid = ObjectId(str(device_id))
        name = str(name).strip()

        eligible = body.get("eligible")
        if eligible is None:
            latest, _ = get_latest_eligibility_results(
                device_id=oid, interface=name, skip=0, limit=1
            )
            eligible = bool(latest[0].get("eligible")) if latest else False

        iface = db.interfaces.find_one(
            {"deviceId": oid, "name": name},
            {"hostname": 1, "ipAddress": 1},
        )
        result = calculate_risk(
            oid,
            name,
            eligible=bool(eligible),
            hostname=(iface or {}).get("hostname"),
            ip_address=(iface or {}).get("ipAddress"),
            persist=True,
        )
        return jsonify({
            "success": True,
            "message": (
                f"Risk score {result.risk_score} ({result.severity})"
            ),
            "data": result.to_api_dict(),
        }), 200
    except Exception as error:  # noqa: BLE001
        return jsonify({
            "success": False,
            "message": "Failed to calculate interface risk",
            "error": str(error),
        }), 500


@storm_bp.route("/storm/risk/calculate-all", methods=["POST"])
@require_auth(roles=["admin"])
def calculate_all_risk_route():
    try:
        summary = calculate_all_risks()
        return jsonify({
            "success": True,
            "message": (
                "Risk scoring skipped (disabled)"
                if summary.get("disabled")
                else (
                    f"Risk scoring completed: "
                    f"{summary['scored']} scored / "
                    f"{summary['skipped']} skipped "
                    f"({summary['total']} interface(s))"
                )
            ),
            **summary,
        }), 200
    except Exception as error:  # noqa: BLE001
        return jsonify({
            "success": False,
            "message": "Failed to run bulk risk calculation",
            "error": str(error),
        }), 500


@storm_bp.route("/storm/risk", methods=["GET"])
@require_auth()
def list_risk():
    try:
        page, limit = parse_pagination(default_limit=50, max_limit=500)
        filters = _risk_filters()
        _, total = get_latest_risk_results(skip=0, limit=1, **filters)
        page, skip, total_pages = clamp_page(page, total, limit)
        rows, total = get_latest_risk_results(skip=skip, limit=limit, **filters)
        return jsonify({
            "success": True,
            "count": len(rows),
            "data": [serialize_risk_result(row) for row in rows],
            **pagination_payload(total, page, limit, total_pages),
        }), 200
    except Exception as error:  # noqa: BLE001
        return jsonify({
            "success": False,
            "message": "Failed to list risk results",
            "error": str(error),
        }), 500


@storm_bp.route("/storm/risk/<device_id>", methods=["GET"])
@require_auth()
def list_device_risk(device_id: str):
    try:
        oid = _parse_device_id(device_id)
        if oid is None:
            return jsonify({"success": False, "message": "Invalid device ID"}), 400

        page, limit = parse_pagination(default_limit=50, max_limit=500)
        filters = _risk_filters()
        _, total = get_latest_risk_results(
            device_id=oid, skip=0, limit=1, **filters
        )
        page, skip, total_pages = clamp_page(page, total, limit)
        rows, total = get_latest_risk_results(
            device_id=oid, skip=skip, limit=limit, **filters
        )
        return jsonify({
            "success": True,
            "count": len(rows),
            "data": [serialize_risk_result(row) for row in rows],
            **pagination_payload(total, page, limit, total_pages),
        }), 200
    except Exception as error:  # noqa: BLE001
        return jsonify({
            "success": False,
            "message": "Failed to list device risk results",
            "error": str(error),
        }), 500


@storm_bp.route("/storm/risk/<device_id>/<path:interface>", methods=["GET"])
@require_auth()
def get_interface_risk(device_id: str, interface: str):
    try:
        oid = _parse_device_id(device_id)
        if oid is None:
            return jsonify({"success": False, "message": "Invalid device ID"}), 400

        name = unquote(interface).strip()
        if not name:
            return jsonify({
                "success": False,
                "message": "Interface name is required",
            }), 400

        include_history = (
            (request.args.get("history") or "").strip().lower()
            in ("1", "true", "yes")
        )
        page, limit = parse_pagination(default_limit=50, max_limit=200)

        latest_rows, _ = get_latest_risk_results(
            device_id=oid, interface=name, skip=0, limit=1
        )
        if not latest_rows:
            return jsonify({
                "success": False,
                "message": "No risk score found for this interface",
            }), 404

        payload = {
            "success": True,
            "data": serialize_risk_result(latest_rows[0]),
        }
        if include_history:
            history, total = get_risk_history(
                oid, name, skip=(page - 1) * limit, limit=limit
            )
            total_pages = (total + limit - 1) // limit if total else 0
            payload["history"] = [serialize_risk_result(row) for row in history]
            payload.update(pagination_payload(total, page, limit, total_pages))

        return jsonify(payload), 200
    except Exception as error:  # noqa: BLE001
        return jsonify({
            "success": False,
            "message": "Failed to fetch interface risk",
            "error": str(error),
        }), 500
