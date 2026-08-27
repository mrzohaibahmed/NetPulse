"""
Persist user-arranged topology canvas positions (visual layout only).

Discovery remains the source of truth for devices and links. This module
stores only node positions (and optional edge id metadata) per view key.
"""

from __future__ import annotations

from typing import Any

from config.database import db
from utils.serializers import format_datetime
from utils.utc import utc_now

LAYOUT_COLLECTION = "topology_layouts"
_MAX_NODES = 2000
_MAX_EDGES = 5000


def _collection():
    return db[LAYOUT_COLLECTION]


def _validate_position(raw: Any) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    if "x" not in raw or "y" not in raw:
        return None
    x_raw = raw["x"]
    y_raw = raw["y"]
    if x_raw is None or y_raw is None:
        return None
    try:
        x = float(x_raw)
        y = float(y_raw)
    except (TypeError, ValueError):
        return None
    if not (abs(x) < 1_000_000 and abs(y) < 1_000_000):
        return None
    return {"x": x, "y": y}


def validate_layout_payload(data: dict | None) -> tuple[list[dict], list[dict]]:
    """
    Validate PUT body. Returns (nodes, edges).

    Raises ValueError with a safe user-facing message on invalid input.
    """
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object")

    raw_nodes = data.get("nodes")
    if not isinstance(raw_nodes, list):
        raise ValueError("nodes must be an array")
    if len(raw_nodes) > _MAX_NODES:
        raise ValueError(f"Too many nodes (maximum {_MAX_NODES})")

    nodes: list[dict] = []
    seen_ids: set[str] = set()
    for item in raw_nodes:
        if not isinstance(item, dict):
            raise ValueError("Each node must be an object")
        node_id = str(item.get("id") or "").strip()
        if not node_id:
            raise ValueError("Each node requires a non-empty id")
        if node_id in seen_ids:
            continue
        position = _validate_position(item.get("position"))
        if position is None:
            raise ValueError(f"Invalid position for node {node_id}")
        seen_ids.add(node_id)
        nodes.append({"id": node_id, "position": position})

    raw_edges = data.get("edges", [])
    if raw_edges is None:
        raw_edges = []
    if not isinstance(raw_edges, list):
        raise ValueError("edges must be an array")
    if len(raw_edges) > _MAX_EDGES:
        raise ValueError(f"Too many edges (maximum {_MAX_EDGES})")

    edges: list[dict] = []
    seen_edge_ids: set[str] = set()
    for item in raw_edges:
        if not isinstance(item, dict):
            raise ValueError("Each edge must be an object")
        edge_id = str(item.get("id") or "").strip()
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        if not edge_id or not source or not target:
            raise ValueError("Each edge requires id, source, and target")
        if edge_id in seen_edge_ids:
            continue
        seen_edge_ids.add(edge_id)
        edges.append({"id": edge_id, "source": source, "target": target})

    return nodes, edges


def normalize_view_key(view_key: str | None) -> str:
    key = (view_key or "full").strip() or "full"
    if len(key) > 128:
        raise ValueError("viewKey is too long")
    # Allow "full" or Mongo ObjectId-like / safe identifiers used as switch ids.
    if key != "full" and not all(c.isalnum() or c in "-_" for c in key):
        raise ValueError("Invalid viewKey")
    return key


def serialize_layout(doc: dict | None) -> dict | None:
    if not doc:
        return None
    return {
        "viewKey": str(doc.get("_id") or doc.get("viewKey") or "full"),
        "nodes": doc.get("nodes") or [],
        "edges": doc.get("edges") or [],
        "updatedAt": format_datetime(doc.get("updatedAt")),
    }


def get_layout(view_key: str) -> dict | None:
    key = normalize_view_key(view_key)
    doc = _collection().find_one({"_id": key})
    return serialize_layout(doc)


def save_layout(view_key: str, nodes: list[dict], edges: list[dict]) -> dict:
    key = normalize_view_key(view_key)
    now = utc_now()
    doc = {
        "_id": key,
        "viewKey": key,
        "nodes": nodes,
        "edges": edges,
        "updatedAt": now,
    }
    _collection().replace_one({"_id": key}, doc, upsert=True)
    return serialize_layout(doc) or {
        "viewKey": key,
        "nodes": nodes,
        "edges": edges,
        "updatedAt": format_datetime(now),
    }
