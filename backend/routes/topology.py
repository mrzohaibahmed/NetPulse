from flask import Blueprint, jsonify
from services.topology_service import get_full_topology, get_switch_topology

topology_bp = Blueprint("topology_bp", __name__)

@topology_bp.route("/topology/full", methods=["GET"])
def get_full():
    data = get_full_topology()
    return jsonify({"success": True, "data": data})

@topology_bp.route("/topology/switch/<device_id>", methods=["GET"])
def get_switch(device_id):
    data = get_switch_topology(device_id)
    return jsonify({"success": True, "data": data})
