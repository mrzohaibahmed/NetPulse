from datetime import datetime, timezone

from flask import g, has_request_context

from config.database import db


def log_audit(action, entity_type=None, entity_id=None, details=None):
    """Persist an administrative action for audit history (FR9.2)."""
    username = None
    role = None
    user_id = None

    if has_request_context() and hasattr(g, "user") and g.user:
        username = g.user.get("username")
        role = g.user.get("role")
        user_id = g.user.get("id")

    db.auditLogs.insert_one({
        "action": action,
        "entityType": entity_type,
        "entityId": str(entity_id) if entity_id is not None else None,
        "details": details or {},
        "username": username,
        "role": role,
        "userId": user_id,
        "timestamp": datetime.now(timezone.utc),
    })
