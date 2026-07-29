import os
from datetime import datetime, timezone

from config.database import db
from utils.auth import hash_password


def ensure_default_admin():
    """Create default admin and viewer if no users exist (FR7.1–FR7.2)."""
    if db.users.count_documents({}) == 0:
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

    ensure_super_admin()


def ensure_super_admin():
    """
    Ensure at least one super-admin account exists.

    Safe to call on every boot: skips creation when a super-admin is already present.
    """
    if db.users.find_one({"role": "super-admin"}):
        return

    username = (os.getenv("DEFAULT_SUPER_ADMIN_USER") or "superadmin").strip()
    password = (os.getenv("DEFAULT_SUPER_ADMIN_PASSWORD") or "superadmin123").strip()
    now = datetime.now(timezone.utc)

    existing = db.users.find_one({"username": username})
    if existing:
        db.users.update_one(
            {"_id": existing["_id"]},
            {"$set": {"role": "super-admin", "updatedAt": now}},
        )
        print(f"Promoted existing user to super-admin: {username}")
        return

    db.users.insert_one({
        "username": username,
        "passwordHash": hash_password(password),
        "role": "super-admin",
        "createdAt": now,
        "updatedAt": now,
    })
    print(f"Default super-admin created: {username}")
