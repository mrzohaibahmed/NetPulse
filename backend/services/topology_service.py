"""Build network topology graphs from devices and interface CDP/LLDP neighbors."""

from __future__ import annotations

from bson import ObjectId

from config.database import db


def _is_switch(device_type: str | None) -> bool:
    return "switch" in (device_type or "").lower()


def _device_node(device: dict) -> dict:
    device_type = device.get("deviceType") or device.get("type") or "Unknown"
    return {
        "id": str(device["_id"]),
        "label": device.get("hostname") or device.get("ipAddress") or "Unknown Device",
        "ip": device.get("ipAddress") or "",
        "type": device_type,
        "status": device.get("status") or "Unknown",
        "isKnownDevice": True,
    }


def _neighbor_identity(neighbor: dict) -> tuple[str, str, str]:
    n_ip = (
        neighbor.get("ip")
        or neighbor.get("managementAddress")
        or neighbor.get("management_address")
        or ""
    )
    n_hostname = neighbor.get("hostname") or ""
    n_type = neighbor.get("deviceType") or neighbor.get("platform") or "Unknown"
    return n_ip, n_hostname, n_type


def get_switches():
    """Return all devices classified as a switch."""
    devices = list(
        db.devices.find(
            {},
            {"_id": 1, "hostname": 1, "ipAddress": 1, "deviceType": 1, "status": 1, "type": 1},
        )
    )
    switches = []
    for d in devices:
        dt = d.get("deviceType") or d.get("type") or ""
        if _is_switch(dt):
            switches.append(_device_node(d))
    return switches


def _build_topology_data(device_filter=None):
    """
    Build topology nodes and edges from interface neighbors.

    If device_filter is set, only that switch and its direct neighbors (Level 1).
    Otherwise, build the full network graph (Level 2).
    """
    devices = list(
        db.devices.find(
            {},
            {"_id": 1, "hostname": 1, "ipAddress": 1, "deviceType": 1, "status": 1, "type": 1},
        )
    )

    if device_filter:
        interfaces = list(db.interfaces.find({"deviceId": ObjectId(device_filter)}))
    else:
        interfaces = list(db.interfaces.find())

    nodes = {}
    edges = []
    known_devices_by_ip = {}
    known_devices_by_hostname = {}
    devices_by_id = {}

    for d in devices:
        device_id = str(d["_id"])
        devices_by_id[device_id] = d

        if not device_filter or device_id == device_filter:
            nodes[device_id] = _device_node(d)

        if d.get("ipAddress"):
            known_devices_by_ip[d["ipAddress"]] = device_id
        if d.get("hostname"):
            known_devices_by_hostname[d["hostname"].lower()] = device_id

    for iface in interfaces:
        device_id = str(iface.get("deviceId") or "")
        if not device_id:
            continue

        if not device_filter and device_id not in devices_by_id:
            continue

        if device_filter and device_id != device_filter:
            continue

        if device_id not in nodes and device_id in devices_by_id:
            nodes[device_id] = _device_node(devices_by_id[device_id])

        neighbor = iface.get("neighbor")
        is_active = str(iface.get("operStatus") or "").lower() == "up"
        edge_label = iface.get("name") or ""

        target_id = None
        target_label = "Unknown Endpoint"
        target_ip = ""
        target_type = "Endpoint"
        protocol = "Direct"

        has_valid_neighbor = isinstance(neighbor, dict) and (
            neighbor.get("ip")
            or neighbor.get("hostname")
            or neighbor.get("managementAddress")
            or neighbor.get("management_address")
        )

        if has_valid_neighbor:
            n_ip, n_hostname, n_type = _neighbor_identity(neighbor)

            if n_ip and n_ip in known_devices_by_ip:
                target_id = known_devices_by_ip[n_ip]
            elif n_hostname and n_hostname.lower() in known_devices_by_hostname:
                target_id = known_devices_by_hostname[n_hostname.lower()]

            if not target_id:
                target_id = f"neighbor_{n_ip or n_hostname}"
                target_label = n_hostname or n_ip or "Unknown Neighbor"
                target_ip = n_ip
                target_type = n_type

            protocol = neighbor.get("protocol") or "CDP/LLDP"

        elif is_active:
            # Fallback: active interface without CDP/LLDP neighbor data
            iface_name = iface.get("name") or "unknown"
            target_id = f"endpoint_{device_id}_{iface_name}"
            target_label = f"Connected Device ({iface_name})"
            target_type = "Unknown Endpoint"
            protocol = "Active Link"

        else:
            continue

        if not target_id:
            continue

        if target_id not in nodes:
            if target_id in devices_by_id:
                nodes[target_id] = _device_node(devices_by_id[target_id])
            else:
                nodes[target_id] = {
                    "id": target_id,
                    "label": target_label,
                    "ip": target_ip,
                    "type": target_type,
                    "status": "Online",
                    "isKnownDevice": False,
                }

        edges.append(
            {
                "id": f"edge_{device_id}_{target_id}_{edge_label}",
                "source": device_id,
                "target": target_id,
                "label": edge_label,
                "protocol": protocol,
                "animated": True,
            }
        )

    return {"nodes": list(nodes.values()), "edges": edges}


def get_level_1_topology(device_id: str):
    if not ObjectId.is_valid(device_id):
        raise ValueError("Invalid device id")

    device = db.devices.find_one({"_id": ObjectId(device_id)})
    if not device:
        raise LookupError("Device not found")

    return _build_topology_data(device_filter=device_id)


def get_level_2_topology():
    return _build_topology_data(device_filter=None)
