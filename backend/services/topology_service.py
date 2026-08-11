"""Build network topology graphs from devices and interface CDP/LLDP neighbors."""

from __future__ import annotations

import re

from bson import ObjectId

from config.database import db
from services.interface_collection.port_resolution import attach_resolved_ips

_DEVICE_PROJECTION = {
    "_id": 1,
    "hostname": 1,
    "ipAddress": 1,
    "macAddress": 1,
    "mac_address": 1,
    "deviceType": 1,
    "type": 1,
    "status": 1,
    "vendor": 1,
    "operatingSystem": 1,
    "lastSeen": 1,
}

_IPV4_RE = re.compile(
    r"(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)"
)
_VLAN_IFACE_RE = re.compile(r"^Vl(?:an)?(\d+)$", re.IGNORECASE)


def _is_switch(device_type: str | None) -> bool:
    return "switch" in (device_type or "").lower()


def _is_infrastructure_device(device_type: str | None) -> bool:
    dt = (device_type or "").lower()
    return any(x in dt for x in ["switch", "router", "firewall", "gateway", "wlc", "ap", "controller", "hub"])


def _device_type(device: dict) -> str:
    return device.get("deviceType") or device.get("type") or "Unknown"


def _normalize_hostname(value: str | None) -> str:
    return (value or "").strip().lower()


def _hostname_keys(hostname: str | None) -> set[str]:
    text = _normalize_hostname(hostname)
    if not text:
        return set()
    keys = {text}
    short = text.split(".")[0]
    if short:
        keys.add(short)
    return keys


def _is_trunk_link(iface: dict) -> bool:
    if iface.get("isTrunk"):
        return True
    mode = str(iface.get("portMode") or iface.get("mode") or "").lower()
    return "trunk" in mode


def _link_type(iface: dict) -> str:
    if _is_trunk_link(iface):
        return "trunk"
    mode = str(iface.get("portMode") or iface.get("mode") or "").lower()
    if "access" in mode:
        return "access"
    return "unknown"


def _neighbor_ip(neighbor: dict) -> str:
    return (
        neighbor.get("ip")
        or neighbor.get("managementAddress")
        or neighbor.get("management_address")
        or ""
    )


def _neighbor_remote_port(neighbor: dict) -> str:
    return neighbor.get("interface") or neighbor.get("port") or neighbor.get("remote_port") or ""


def _extract_ipv4(text: str | None) -> str:
    if not text:
        return ""
    match = _IPV4_RE.search(str(text))
    return match.group(0) if match else ""


def _neighbor_has_identity(neighbor: dict | None) -> bool:
    """True when CDP/LLDP neighbor dict carries any usable identity signal."""
    if not neighbor:
        return False
    return bool(
        neighbor.get("ip")
        or neighbor.get("hostname")
        or neighbor.get("managementAddress")
        or neighbor.get("management_address")
        or neighbor.get("platform")
        or neighbor.get("deviceType")
        or neighbor.get("device_type")
        or neighbor.get("interface")
        or neighbor.get("port")
        or neighbor.get("systemDescription")
        or neighbor.get("system_description")
        or neighbor.get("capabilities")
    )


def _neighbor_has_inventory_keys(neighbor: dict) -> bool:
    return bool(
        neighbor.get("ip")
        or neighbor.get("hostname")
        or neighbor.get("managementAddress")
        or neighbor.get("management_address")
    )


def _resolve_known_device_id(
    neighbor: dict,
    *,
    by_ip: dict[str, str],
    by_hostname: dict[str, str],
) -> str | None:
    n_ip = _neighbor_ip(neighbor)
    if n_ip and n_ip in by_ip:
        return by_ip[n_ip]

    n_hostname = neighbor.get("hostname") or ""
    for key in _hostname_keys(n_hostname):
        if key in by_hostname:
            return by_hostname[key]

    return None


def _synthetic_neighbor_id(device_id: str, iface_name: str, neighbor: dict) -> str:
    n_ip = _neighbor_ip(neighbor)
    n_hostname = neighbor.get("hostname") or ""
    platform = neighbor.get("platform") or ""
    remote_port = _neighbor_remote_port(neighbor)
    slug = n_ip or n_hostname or platform or remote_port or iface_name
    slug = re.sub(r"[^\w.\-]+", "_", slug)
    return f"neighbor_{device_id}_{iface_name}_{slug}"


def _is_vlan_interface(name: str) -> bool:
    return bool(_VLAN_IFACE_RE.match((name or "").strip()))


def _description_hostname_hint(description: str) -> str:
    text = (description or "").strip()
    if not text:
        return ""
    if _IPV4_RE.fullmatch(text):
        return ""
    if len(text) > 64:
        return text[:64]
    return text


def _match_inventory_by_text(
    text: str,
    *,
    by_ip: dict[str, str],
    by_hostname: dict[str, str],
) -> str | None:
    ip = _extract_ipv4(text)
    if ip and ip in by_ip:
        return by_ip[ip]

    hint = _description_hostname_hint(text)
    for key in _hostname_keys(hint):
        if key in by_hostname:
            return by_hostname[key]
    return None


def _build_details(
    *,
    hostname: str = "",
    ip: str = "",
    device_type: str = "Unknown",
    status: str = "Unknown",
    vendor: str = "",
    platform: str = "",
    protocol: str = "",
    management_address: str = "",
    operating_system: str = "",
    last_seen=None,
    system_description: str = "",
    capabilities: list | None = None,
    is_known_device: bool = True,
) -> dict:
    return {
        "hostname": hostname or "",
        "ip": ip or "",
        "type": device_type or "Unknown",
        "status": status or "Unknown",
        "vendor": vendor or "",
        "platform": platform or "",
        "protocol": protocol or "",
        "managementAddress": management_address or "",
        "operatingSystem": operating_system or "",
        "lastSeen": last_seen.isoformat() if hasattr(last_seen, "isoformat") else (last_seen or ""),
        "systemDescription": system_description or "",
        "capabilities": list(capabilities or []),
        "isKnownDevice": is_known_device,
    }


def _device_node(device: dict) -> dict:
    device_type = _device_type(device)
    hostname = device.get("hostname") or ""
    ip = device.get("ipAddress") or ""
    label = hostname or ip or "Unknown Device"
    return {
        "id": str(device["_id"]),
        "hostname": hostname,
        "label": label,
        "ip": ip,
        "mac": device.get("macAddress") or device.get("mac_address") or "",
        "type": device_type,
        "status": device.get("status") or "Unknown",
        "vendor": device.get("vendor") or "",
        "platform": "",
        "protocol": "",
        "managementAddress": ip,
        "isKnownDevice": True,
        "details": _build_details(
            hostname=hostname,
            ip=ip,
            device_type=device_type,
            status=device.get("status") or "Unknown",
            vendor=device.get("vendor") or "",
            operating_system=device.get("operatingSystem") or "",
            last_seen=device.get("lastSeen"),
            is_known_device=True,
        ),
    }


def _neighbor_node(
    node_id: str,
    neighbor: dict | None,
    *,
    fallback_label: str,
    fallback_ip: str = "",
    fallback_type: str = "Unknown",
    protocol: str = "",
) -> dict:
    n_hostname = (neighbor or {}).get("hostname") or ""
    n_ip = _neighbor_ip(neighbor or {})
    n_type = (neighbor or {}).get("deviceType") or (neighbor or {}).get("platform") or fallback_type
    mac = (neighbor or {}).get("portId") or (neighbor or {}).get("port_id") or (neighbor or {}).get("macAddress") or ""
    platform = (neighbor or {}).get("platform") or ""
    remote_port = _neighbor_remote_port(neighbor or {})
    mgmt = (neighbor or {}).get("managementAddress") or (neighbor or {}).get("management_address") or n_ip
    label = (
        n_hostname
        or n_ip
        or platform
        or (f"Neighbor ({remote_port})" if remote_port else "")
        or fallback_label
        or "Unknown Neighbor"
    )

    return {
        "id": node_id,
        "hostname": n_hostname,
        "label": label,
        "ip": n_ip or fallback_ip,
        "mac": mac,
        "type": n_type,
        "status": "Online",
        "vendor": "",
        "platform": platform,
        "protocol": (neighbor or {}).get("protocol") or protocol,
        "managementAddress": mgmt,
        "isKnownDevice": False,
        "details": _build_details(
            hostname=n_hostname,
            ip=n_ip or fallback_ip,
            device_type=n_type,
            status="Online",
            platform=platform,
            protocol=(neighbor or {}).get("protocol") or protocol,
            management_address=mgmt,
            system_description=(neighbor or {}).get("systemDescription") or "",
            capabilities=(neighbor or {}).get("capabilities") or [],
            is_known_device=False,
        ),
    }


def _endpoint_node(device_id: str, iface: dict) -> dict:
    iface_name = iface.get("name") or "unknown"
    description = iface.get("description") or ""
    node_id = f"endpoint_{device_id}_{iface_name}"

    ip = iface.get("resolvedDeviceIp") or _extract_ipv4(description)
    mac = iface.get("resolvedDeviceMac") or ""
    hostname = _description_hostname_hint(description)
    vlan_match = _VLAN_IFACE_RE.match(iface_name.strip())

    if vlan_match:
        vlan_id = vlan_match.group(1)
        label = f"Vlan {vlan_id}"
        device_type = "Vlan Interface"
        if not hostname:
            hostname = label
    elif hostname:
        label = hostname
        device_type = "Connected Endpoint"
    elif ip:
        label = ip
        device_type = "Connected Endpoint"
    else:
        label = f"Port {iface_name}"
        device_type = "Connected Endpoint"

    return {
        "id": node_id,
        "hostname": hostname,
        "label": label,
        "ip": ip,
        "mac": mac,
        "type": device_type,
        "status": "Online",
        "vendor": "",
        "platform": "",
        "protocol": "Active Link",
        "managementAddress": ip,
        "isKnownDevice": False,
        "details": _build_details(
            hostname=hostname,
            ip=ip,
            device_type=device_type,
            status="Online",
            protocol="Active Link",
            management_address=ip,
            system_description=description,
            is_known_device=False,
        ),
    }


def _ensure_target_node(
    nodes: dict[str, dict],
    target_id: str,
    devices_by_id: dict[str, dict],
    neighbor: dict | None = None,
    *,
    fallback_label: str = "",
    fallback_ip: str = "",
    fallback_type: str = "Unknown",
    protocol: str = "",
    iface: dict | None = None,
    device_id: str = "",
) -> None:
    if target_id in nodes:
        return

    if target_id in devices_by_id:
        nodes[target_id] = _device_node(devices_by_id[target_id])
        return

    if neighbor and _neighbor_has_identity(neighbor):
        nodes[target_id] = _neighbor_node(
            target_id,
            neighbor,
            fallback_label=fallback_label,
            fallback_ip=fallback_ip,
            fallback_type=fallback_type,
            protocol=protocol,
        )
        return

    if iface is not None:
        nodes[target_id] = _endpoint_node(device_id, iface)


def _register_device_indexes(device: dict, by_ip: dict, by_hostname: dict) -> None:
    device_id = str(device["_id"])
    ip = device.get("ipAddress") or ""
    if ip:
        by_ip[ip] = device_id
    for key in _hostname_keys(device.get("hostname")):
        by_hostname[key] = device_id


def _resolve_target_id(
    neighbor: dict,
    *,
    by_ip: dict[str, str],
    by_hostname: dict[str, str],
) -> str | None:
    """Resolve inventory match or synthetic id for neighbors with IP/hostname."""
    known = _resolve_known_device_id(neighbor, by_ip=by_ip, by_hostname=by_hostname)
    if known:
        return known

    n_ip = _neighbor_ip(neighbor)
    n_hostname = neighbor.get("hostname") or ""
    if n_hostname:
        return f"neighbor_{n_ip or n_hostname}"
    if n_ip:
        return f"neighbor_{n_ip}"
    return None


def _resolve_connected_target(
    iface: dict,
    device_id: str,
    neighbor: dict | None,
    *,
    by_ip: dict[str, str],
    by_hostname: dict[str, str],
    devices_by_id: dict[str, dict],
) -> tuple[str, str, str] | None:
    """
    Resolve target node id, remote port, and protocol for an interface link.
    Returns None when the interface should not produce an edge.
    """
    iface_name = iface.get("name") or ""
    description = iface.get("description") or ""
    is_active = str(iface.get("operStatus") or "").lower() == "up"

    if neighbor and _neighbor_has_identity(neighbor):
        protocol = neighbor.get("protocol") or "CDP/LLDP"
        target_port = _neighbor_remote_port(neighbor)

        known_id = _resolve_known_device_id(
            neighbor,
            by_ip=by_ip,
            by_hostname=by_hostname,
        )
        if known_id:
            return known_id, target_port, protocol

        if _neighbor_has_inventory_keys(neighbor):
            target_id = _resolve_target_id(neighbor, by_ip=by_ip, by_hostname=by_hostname)
            if target_id:
                return target_id, target_port, protocol

        target_id = _synthetic_neighbor_id(device_id, iface_name, neighbor)
        return target_id, target_port, protocol

    if not is_active:
        return None

    # Active link without CDP/LLDP identity — infer from description or inventory.
    inventory_id = _match_inventory_by_text(
        description,
        by_ip=by_ip,
        by_hostname=by_hostname,
    )
    if inventory_id:
        return inventory_id, "", "Active Link"

    ip_from_desc = _extract_ipv4(description)
    if ip_from_desc and ip_from_desc in by_ip:
        return by_ip[ip_from_desc], "", "Active Link"

    target_id = f"endpoint_{device_id}_{iface_name or 'unknown'}"
    return target_id, "", "Active Link"


def _canonical_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _merge_raw_edges(raw_edges: list[dict]) -> list[dict]:
    """Merge bidirectional known-device links into a single undirected edge."""
    merged: dict[tuple[str, str], dict] = {}
    passthrough: list[dict] = []

    for raw in raw_edges:
        source = raw["source"]
        target = raw["target"]
        source_known = not source.startswith(("neighbor_", "endpoint_"))
        target_known = not target.startswith(("neighbor_", "endpoint_"))

        if not (source_known and target_known):
            passthrough.append(raw)
            continue

        pair = _canonical_pair(source, target)
        existing = merged.get(pair)

        if not existing:
            merged[pair] = raw.copy()
            continue

        # Fill ports from the reverse direction when missing.
        if pair[0] == raw["source"]:
            if not existing.get("sourcePort"):
                existing["sourcePort"] = raw.get("sourcePort") or ""
            if not existing.get("targetPort"):
                existing["targetPort"] = raw.get("targetPort") or ""
        else:
            if not existing.get("targetPort"):
                existing["targetPort"] = raw.get("sourcePort") or ""
            if not existing.get("sourcePort"):
                existing["sourcePort"] = raw.get("targetPort") or ""

        existing["isTrunk"] = bool(existing.get("isTrunk") or raw.get("isTrunk"))
        if existing.get("linkType") != "trunk" and raw.get("linkType") == "trunk":
            existing["linkType"] = "trunk"
        if not existing.get("protocol") and raw.get("protocol"):
            existing["protocol"] = raw["protocol"]

    normalized: list[dict] = []
    for pair, edge in merged.items():
        left, right = pair
        if edge.get("source") == left:
            source_port = edge.get("sourcePort") or ""
            target_port = edge.get("targetPort") or ""
        else:
            source_port = edge.get("targetPort") or ""
            target_port = edge.get("sourcePort") or ""

        normalized.append(
            {
                "id": f"edge_{left}_{right}",
                "source": left,
                "target": right,
                "label": source_port or edge.get("label") or "",
                "sourcePort": source_port,
                "targetPort": target_port,
                "isTrunk": bool(edge.get("isTrunk")),
                "linkType": edge.get("linkType") or "unknown",
                "protocol": edge.get("protocol") or "CDP/LLDP",
                "description": edge.get("description") or "",
                "speed": edge.get("speed") or "",
                "animated": True,
            }
        )

    for raw in passthrough:
        normalized.append(
            {
                "id": raw["id"],
                "source": raw["source"],
                "target": raw["target"],
                "label": raw.get("sourcePort") or raw.get("label") or "",
                "sourcePort": raw.get("sourcePort") or "",
                "targetPort": raw.get("targetPort") or "",
                "isTrunk": bool(raw.get("isTrunk")),
                "linkType": raw.get("linkType") or "unknown",
                "protocol": raw.get("protocol") or "Direct",
                "description": raw.get("description") or "",
                "speed": raw.get("speed") or "",
                "animated": True,
            }
        )

    return normalized


def get_switches():
    """Return all devices classified as a switch."""
    devices = list(db.devices.find({}, _DEVICE_PROJECTION))
    switches = []
    for d in devices:
        if _is_switch(_device_type(d)):
            switches.append(_device_node(d))
    return switches


def _build_topology_data(device_filter=None):
    """
    Build topology nodes and edges from interface neighbors.

    If device_filter is set, only that switch and its direct neighbors (Level 1).
    Otherwise, build the full network graph (Level 2).
    """
    devices = list(db.devices.find({}, _DEVICE_PROJECTION))

    if device_filter:
        interfaces = list(db.interfaces.find({"deviceId": ObjectId(device_filter)}))
    else:
        interfaces = list(db.interfaces.find())
        
    attach_resolved_ips(interfaces)

    nodes: dict[str, dict] = {}
    raw_edges: list[dict] = []
    known_devices_by_ip: dict[str, str] = {}
    known_devices_by_hostname: dict[str, str] = {}
    devices_by_id: dict[str, dict] = {}

    for d in devices:
        device_id = str(d["_id"])
        devices_by_id[device_id] = d
        _register_device_indexes(d, known_devices_by_ip, known_devices_by_hostname)

        # Only proactively add infrastructure devices as standalone nodes.
        # Endpoints/Ping devices will only be added if a switch connects to them.
        if not device_filter or device_id == device_filter:
            if _is_infrastructure_device(_device_type(d)) or device_id == device_filter:
                nodes[device_id] = _device_node(d)

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

        neighbor = iface.get("neighbor") if isinstance(iface.get("neighbor"), dict) else None
        source_port = iface.get("name") or ""
        is_trunk = _is_trunk_link(iface)
        link_type = _link_type(iface)

        resolved = _resolve_connected_target(
            iface,
            device_id,
            neighbor,
            by_ip=known_devices_by_ip,
            by_hostname=known_devices_by_hostname,
            devices_by_id=devices_by_id,
        )
        if not resolved:
            continue

        target_id, target_port, protocol = resolved

        fallback_label = ""
        fallback_ip = ""
        fallback_type = "Unknown"
        if neighbor:
            fallback_label = (
                neighbor.get("hostname")
                or neighbor.get("platform")
                or _neighbor_ip(neighbor)
                or _description_hostname_hint(iface.get("description") or "")
                or ""
            )
            fallback_ip = _neighbor_ip(neighbor) or _extract_ipv4(iface.get("description") or "")
            fallback_type = (
                neighbor.get("deviceType")
                or neighbor.get("platform")
                or "Connected Endpoint"
            )

        _ensure_target_node(
            nodes,
            target_id,
            devices_by_id,
            neighbor if neighbor and _neighbor_has_identity(neighbor) else None,
            fallback_label=fallback_label,
            fallback_ip=fallback_ip,
            fallback_type=fallback_type,
            protocol=protocol,
            iface=iface,
            device_id=device_id,
        )

        raw_edges.append(
            {
                "id": f"edge_{device_id}_{target_id}_{source_port}",
                "source": device_id,
                "target": target_id,
                "label": source_port,
                "sourcePort": source_port,
                "targetPort": target_port,
                "isTrunk": is_trunk,
                "linkType": link_type,
                "protocol": protocol,
                "description": iface.get("description") or "",
                "speed": iface.get("speed") or "",
            }
        )

        # Level 1: ensure neighbor known devices appear even if only referenced as targets.
        if device_filter and target_id in devices_by_id and target_id not in nodes:
            nodes[target_id] = _device_node(devices_by_id[target_id])

    edges = _merge_raw_edges(raw_edges)

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
