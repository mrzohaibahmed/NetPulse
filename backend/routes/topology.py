from flask import Blueprint, jsonify

from services.topology_service import get_level_1_topology, get_level_2_topology, get_switches
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
