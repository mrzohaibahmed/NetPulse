import os
from datetime import datetime, timezone

from config.database import db
from utils.auth import normalize_role
from utils.auth import hash_password
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("users.bootstrap")

# Well-known passwords that must never be used for production bootstrap.
_FORBIDDEN_BOOTSTRAP_PASSWORDS = frozenset(
    {
        "admin123",
        "viewer123",
        "superadmin123",
        "user123",
        "password",
        "password123",
        "changeme",
        "change-me",
        "admin",
        "user",
        "superadmin",
        "netpulse",
        "123456",
        "12345678",
    }
)

_MIN_BOOTSTRAP_PASSWORD_LENGTH = 12


def _flask_debug_enabled() -> bool:
    return os.getenv("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")


def _is_production_like() -> bool:
    """True when we must not use well-known bootstrap passwords."""
    if _flask_debug_enabled():
        return False
    env = (os.getenv("NETPULSE_ENV") or "").strip().lower()
    # Unset NETPULSE_ENV with FLASK_DEBUG=false is treated as production
    # (see config.deployment.get_app_environment).
    return env in ("", "production", "prod")


def is_forbidden_bootstrap_password(password: str) -> bool:
    value = (password or "").strip()
    if not value:
        return True
    lowered = value.lower()
    if lowered in _FORBIDDEN_BOOTSTRAP_PASSWORDS:
        return True
    if len(value) < _MIN_BOOTSTRAP_PASSWORD_LENGTH:
        return True
    for marker in ("change-this", "change-me", "replace-with", "placeholder", "example"):
        if marker in lowered:
            return True
    return False


def _require_secure_password(env_name: str, password: str, *, role: str) -> str:
    value = (password or "").strip()
    if _is_production_like():
        if is_forbidden_bootstrap_password(value):
            raise RuntimeError(
                f"Refusing to bootstrap {role} account: {env_name} is missing or "
                f"uses a weak/well-known password. Set a strong unique "
                f"{env_name} (min {_MIN_BOOTSTRAP_PASSWORD_LENGTH} characters) "
                f"before starting NetPulse. Existing users are not modified."
            )
        return value

    # Local debug only: allow explicit env password, or documented lab defaults.
    if not value:
        if role == "admin":
            value = "admin123"
        elif role == "user":
            value = "user123"
    return value


def ensure_default_admin():
    """Create initial users only when the users collection is empty.

    Production requires strong DEFAULT_ADMIN_PASSWORD / DEFAULT_USER_PASSWORD.
    Never logs password values. Does not modify existing users.
    """
    if db.users.count_documents({}) == 0:
        username = (os.getenv("DEFAULT_ADMIN_USER") or "admin").strip() or "admin"
        user_username = (os.getenv("DEFAULT_USER_NAME") or "user").strip() or "user"
        password = _require_secure_password(
            "DEFAULT_ADMIN_PASSWORD",
            os.getenv("DEFAULT_ADMIN_PASSWORD") or "",
            role="admin",
        )
        user_password = _require_secure_password(
            "DEFAULT_USER_PASSWORD",
            (os.getenv("DEFAULT_USER_PASSWORD") or os.getenv("DEFAULT_VIEWER_PASSWORD") or ""),
            role="user",
        )
        now = datetime.now(timezone.utc)

        db.users.insert_many([
            {
                "username": username,
                "passwordHash": hash_password(password),
                "role": "admin",
                "active": True,
                "mustChangePassword": True,
                "createdAt": now,
                "updatedAt": now,
            },
            {
                "username": user_username,
                "passwordHash": hash_password(user_password),
                "role": "user",
                "active": True,
                "mustChangePassword": True,
                "createdAt": now,
                "updatedAt": now,
            },
        ])
        logger.info(
            "Default users created | admin=%s | user=%s | "
            "mustChangePassword=true",
            username,
            user_username,
        )

    migrate_legacy_roles()


def migrate_legacy_roles():
    """Normalize any legacy stored role names to the canonical admin/user model."""
    now = datetime.now(timezone.utc)
    changed = 0
    for user in db.users.find({}, {"_id": 1, "role": 1}):
        current = user.get("role")
        normalized = normalize_role(current)
        if current != normalized:
            db.users.update_one(
                {"_id": user["_id"]},
                {"$set": {"role": normalized, "updatedAt": now}},
            )
            changed += 1
    if changed:
        logger.info("Migrated legacy user roles | changed=%s", changed)
