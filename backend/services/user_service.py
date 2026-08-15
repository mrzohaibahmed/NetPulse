import os
from datetime import datetime, timezone

from config.database import db
from utils.auth import hash_password
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("users.bootstrap")

# Well-known passwords that must never be used for production bootstrap.
_FORBIDDEN_BOOTSTRAP_PASSWORDS = frozenset(
    {
        "admin123",
        "viewer123",
        "superadmin123",
        "password",
        "password123",
        "changeme",
        "change-me",
        "admin",
        "viewer",
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
        elif role == "viewer":
            value = "viewer123"
        elif role == "super-admin":
            value = "superadmin123"
    return value


def ensure_default_admin():
    """Create initial users only when the users collection is empty.

    Production requires strong DEFAULT_ADMIN_PASSWORD / DEFAULT_VIEWER_PASSWORD.
    Never logs password values. Does not modify existing users.
    """
    if db.users.count_documents({}) == 0:
        username = (os.getenv("DEFAULT_ADMIN_USER") or "admin").strip() or "admin"
        password = _require_secure_password(
            "DEFAULT_ADMIN_PASSWORD",
            os.getenv("DEFAULT_ADMIN_PASSWORD") or "",
            role="admin",
        )
        viewer_password = _require_secure_password(
            "DEFAULT_VIEWER_PASSWORD",
            os.getenv("DEFAULT_VIEWER_PASSWORD") or "",
            role="viewer",
        )
        now = datetime.now(timezone.utc)

        db.users.insert_many([
            {
                "username": username,
                "passwordHash": hash_password(password),
                "role": "admin",
                "mustChangePassword": True,
                "createdAt": now,
                "updatedAt": now,
            },
            {
                "username": "viewer",
                "passwordHash": hash_password(viewer_password),
                "role": "viewer",
                "mustChangePassword": True,
                "createdAt": now,
                "updatedAt": now,
            },
        ])
        logger.info(
            "Default users created | admin=%s | viewer=viewer | "
            "mustChangePassword=true",
            username,
        )

    ensure_super_admin()


def ensure_super_admin():
    """
    Ensure at least one super-admin exists.

    When a super-admin already exists, this is a no-op (never resets passwords).

    When none exists:
    - Prefer promoting an existing username (DEFAULT_SUPER_ADMIN_USER) without
      changing their password.
    - Creating a *new* super-admin requires a strong DEFAULT_SUPER_ADMIN_PASSWORD
      in production and never falls back to well-known defaults.
    """
    if db.users.find_one({"role": "super-admin"}):
        return

    username = (os.getenv("DEFAULT_SUPER_ADMIN_USER") or "superadmin").strip() or "superadmin"
    now = datetime.now(timezone.utc)

    existing = db.users.find_one({"username": username})
    if existing:
        db.users.update_one(
            {"_id": existing["_id"]},
            {"$set": {"role": "super-admin", "updatedAt": now}},
        )
        logger.info(
            "Promoted existing user to super-admin | username=%s | password unchanged",
            username,
        )
        return

    # No matching user — create only with an explicitly strong password.
    raw_password = os.getenv("DEFAULT_SUPER_ADMIN_PASSWORD") or ""
    if _is_production_like() and (
        not raw_password.strip() or is_forbidden_bootstrap_password(raw_password)
    ):
        logger.error(
            "No super-admin exists and DEFAULT_SUPER_ADMIN_PASSWORD is missing "
            "or weak. Not creating a privileged account with a known password. "
            "Promote an existing admin via the users API or set a strong "
            "DEFAULT_SUPER_ADMIN_PASSWORD and restart."
        )
        return

    if not raw_password.strip():
        # Debug / empty DB edge: still refuse silent known defaults.
        logger.error(
            "No super-admin exists and DEFAULT_SUPER_ADMIN_PASSWORD is unset. "
            "Skipping auto-create."
        )
        return

    password = _require_secure_password(
        "DEFAULT_SUPER_ADMIN_PASSWORD",
        raw_password,
        role="super-admin",
    )

    db.users.insert_one({
        "username": username,
        "passwordHash": hash_password(password),
        "role": "super-admin",
        "mustChangePassword": True,
        "createdAt": now,
        "updatedAt": now,
    })
    logger.info(
        "Super-admin created | username=%s | mustChangePassword=true",
        username,
    )
