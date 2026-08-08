from config.database import db
from bson import ObjectId

def get_devices():
    return list(db.devices.find({}, {"_id": 1, "hostname": 1, "ipAddress": 1, "deviceType": 1, "status": 1}))

def get_interfaces(device_id=None):
    query = {}
    if device_id:
        try:
            query["deviceId"] = {"$in": [str(device_id), ObjectId(str(device_id))]}
        except:
            query["deviceId"] = str(device_id)
    
    return list(db.interfaces.find(
        query, 
        {"_id": 1, "deviceId": 1, "name": 1, "mode": 1, "isTrunk": 1, "operStatus": 1, "neighbor": 1, "hostname": 1}
    ))

def _normalize_hostname(h):
    if not h: return ""
    return str(h).split(".")[0].strip().lower()

def build_nodes_and_edges(devices, interfaces):
    nodes = []
    edges = []
    node_ids = set()
    
    # 1. Add Device Nodes (Switches)
    switch_map = {}
    for dev in devices:
        dev_id_str = str(dev["_id"])
        hostname = dev.get("hostname", "Unknown")
        norm_hostname = _normalize_hostname(hostname)
        switch_map[norm_hostname] = dev_id_str
        
        if dev_id_str not in node_ids:
            nodes.append({
                "id": dev_id_str,
                "type": "switchNode",
                "data": {
                    "label": hostname,
                    "ip": dev.get("ipAddress", ""),
                    "status": dev.get("status", "Unknown")
                }
            })
            node_ids.add(dev_id_str)

    # 2. Add Endpoints and Edges from Interfaces
    for iface in interfaces:
        source_id = str(iface.get("deviceId"))
        is_trunk = iface.get("isTrunk") or iface.get("mode") == "trunk"
        is_up = iface.get("operStatus", "").lower() == "up"
        
        neighbor = iface.get("neighbor")
        target_id = None
        raw_neighbor_hostname = ""
        neighbor_ip = ""
        neighbor_platform = ""
        
        if neighbor and neighbor.get("hostname"):
            raw_neighbor_hostname = neighbor.get("hostname")
            norm_neighbor_hostname = _normalize_hostname(raw_neighbor_hostname)
            
            # If neighbor is a known switch, find its ID
            target_id = switch_map.get(norm_neighbor_hostname)
            neighbor_ip = neighbor.get("ip", "")
            neighbor_platform = neighbor.get("platform", "")
            
            # If the neighbor is NOT a known switch, create an endpoint node
            if not target_id:
                safe_hostname = norm_neighbor_hostname.replace(" ", "_")
                target_id = f"endpoint_{safe_hostname}"
        else:
            # If no CDP/LLDP neighbor but port is UP, we still show a connected endpoint
            if is_up:
                iface_name = iface.get("name", "Unknown_Port")
                target_id = f"unknown_{source_id}_{iface_name.replace('/', '_')}"
                raw_neighbor_hostname = f"Device on {iface_name}"
                neighbor_platform = "Unknown (No CDP/LLDP)"
                
        if target_id and target_id not in node_ids:
            nodes.append({
                "id": target_id,
                "type": "endpointNode",
                "data": {
                    "label": raw_neighbor_hostname,
                    "ip": neighbor_ip,
                    "platform": neighbor_platform
                }
            })
            node_ids.add(target_id)
        
        if target_id and source_id:
            edge_id = f"e_{source_id}_{target_id}"
            reverse_edge_id = f"e_{target_id}_{source_id}"
            
            # Prevent duplicate edges
            edge_exists = any(e["id"] == edge_id or e["id"] == reverse_edge_id for e in edges)
            if not edge_exists:
                edges.append({
                    "id": edge_id,
                    "source": source_id,
                    "target": target_id,
                    "type": "smoothstep",
                    "animated": is_up,
                    "label": "Trunk" if is_trunk else "Access",
                    "style": {
                        "stroke": "#8b5cf6" if is_trunk else "#3b82f6",
                        "strokeWidth": 2
                    }
                })

    return {"nodes": nodes, "edges": edges}

def get_full_topology():
    devices = get_devices()
    interfaces = get_interfaces()
    return build_nodes_and_edges(devices, interfaces)

def get_switch_topology(device_id):
    devices = get_devices()
    interfaces = get_interfaces()
    
    # Build the full topology first
    full_topo = build_nodes_and_edges(devices, interfaces)
    
    # Filter to only include the target switch and its direct neighbors
    target_id_str = str(device_id)
    connected_node_ids = {target_id_str}
    filtered_edges = []
    
    for edge in full_topo["edges"]:
        if edge["source"] == target_id_str or edge["target"] == target_id_str:
            filtered_edges.append(edge)
            connected_node_ids.add(edge["source"])
            connected_node_ids.add(edge["target"])
            
    filtered_nodes = [node for node in full_topo["nodes"] if node["id"] in connected_node_ids]
    
    return {"nodes": filtered_nodes, "edges": filtered_edges}
