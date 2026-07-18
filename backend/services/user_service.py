import os
from datetime import datetime, timezone

from config.database import db
from utils.auth import hash_password


def ensure_default_admin():
    """Create default admin and viewer if no users exist (FR7.1–FR7.2)."""
    if db.users.count_documents({}) > 0:
        return

    username = (os.getenv("DEFAULT_ADMIN_USER") or "admin").strip()
    password = (os.getenv("DEFAULT_ADMIN_PASSWORD") or "admin123").strip()
    now = datetime.now(timezone.utc)

    db.users.insert_many([
        {
            "username": username,
            "passwordHash": hash_password(password),
            "role": "admin",
            "createdAt": now,
            "updatedAt": now,
        },
        {
            "username": "viewer",
            "passwordHash": hash_password("viewer123"),
            "role": "viewer",
            "createdAt": now,
            "updatedAt": now,
        },
    ])
    print(f"Default users created: {username} (admin), viewer (viewer)")
