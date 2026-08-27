from flask import Blueprint, jsonify, request

from services.topology_layout_service import (
    get_layout,
    normalize_view_key,
    save_layout,
    validate_layout_payload,
)
from services.topology_service import get_level_1_topology, get_level_2_topology, get_switches
from utils.api_errors import internal_error_response
from utils.auth import require_auth

topology_bp = Blueprint("topology", __name__)


@topology_bp.route("/switches", methods=["GET"])
@require_auth()
def api_get_switches():
    try:
        switches = get_switches()
        return jsonify({"success": True, "data": switches}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@topology_bp.route("/switch/<device_id>", methods=["GET"])
@require_auth()
def api_get_level_1_topology(device_id):
    try:
        topology = get_level_1_topology(device_id)
        return jsonify({"success": True, "data": topology}), 200
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    except LookupError as e:
        return jsonify({"success": False, "message": str(e)}), 404
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@topology_bp.route("/full", methods=["GET"])
@require_auth()
def api_get_level_2_topology():
    try:
        topology = get_level_2_topology()
        return jsonify({"success": True, "data": topology}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@topology_bp.route("/layout", methods=["GET"])
@require_auth()
def api_get_topology_layout():
    """Return the saved canvas layout for a view (positions only)."""
    try:
        view_key = normalize_view_key(request.args.get("view") or request.args.get("viewKey"))
        layout = get_layout(view_key)
        return jsonify({"success": True, "layout": layout}), 200
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as error:
        return internal_error_response(error, message="Failed to load topology layout")


@topology_bp.route("/layout", methods=["PUT"])
@require_auth()
def api_put_topology_layout():
    """Persist the user's canvas node positions for a view."""
    try:
        data = request.get_json(silent=True) or {}
        view_key = normalize_view_key(
            data.get("viewKey") or data.get("view") or request.args.get("view")
        )
        nodes, edges = validate_layout_payload(data)
        layout = save_layout(view_key, nodes, edges)
        return jsonify({
            "success": True,
            "message": "Topology layout saved",
            "layout": layout,
        }), 200
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as error:
        return internal_error_response(error, message="Failed to save topology layout")
