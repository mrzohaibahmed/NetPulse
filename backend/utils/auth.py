import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import bcrypt
import jwt
from dotenv import load_dotenv
from flask import g, jsonify, request

from utils.jwt_secret import resolve_jwt_secret

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "8"))
JWT_ALGORITHM = "HS256"

JWT_SECRET = resolve_jwt_secret()

# Canonical role model: admin + user.
# Legacy roles are normalized for backward compatibility:
# - viewer/operator -> user
# - super-admin -> admin
VALID_ROLES = ("user", "admin")
_LEGACY_ROLE_MAP = {
    "viewer": "user",
    "operator": "user",
    "super-admin": "admin",
}
ROLE_PRIVILEGES = {
    "user": frozenset({"user"}),
    "admin": frozenset({"user", "admin"}),
}


def normalize_role(role: str | None) -> str:
    value = (role or "user").strip().lower()
    value = _LEGACY_ROLE_MAP.get(value, value)
    return value if value in VALID_ROLES else "user"


def role_satisfies(user_role: str | None, allowed_roles: list[str] | tuple[str, ...] | None) -> bool:
    """Return True when the user's role meets any of the required roles (with inheritance)."""
    if not allowed_roles:
        return True
    privileges = ROLE_PRIVILEGES.get(normalize_role(user_role), frozenset({"user"}))
    required = {normalize_role(r) for r in allowed_roles}
    return bool(privileges.intersection(required))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def create_access_token(
    user_id: str,
    username: str,
    role: str,
    *,
    must_change_password: bool = False,
) -> str:
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "mustChangePassword": bool(must_change_password),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


def get_token_from_request():
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return None


def require_auth(roles=None, allow_password_change=False):
    """Protect a route. roles=None allows any authenticated user.

    When the JWT has mustChangePassword=True, all routes are blocked with 403
    unless allow_password_change=True (account self-service / session probe).
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            token = get_token_from_request()
            if not token:
                return jsonify({
                    "success": False,
                    "message": "Authentication required",
                }), 401

            try:
                payload = decode_access_token(token)
            except jwt.ExpiredSignatureError:
                return jsonify({
                    "success": False,
                    "message": "Token expired. Please log in again.",
                }), 401
            except jwt.InvalidTokenError:
                return jsonify({
                    "success": False,
                    "message": "Invalid authentication token",
                }), 401

            role = normalize_role(payload.get("role", "user"))
            if roles and not role_satisfies(role, roles):
                return jsonify({
                    "success": False,
                    "message": "Insufficient permissions",
                }), 403

            must_change = bool(payload.get("mustChangePassword"))
            g.user = {
                "id": payload.get("sub"),
                "username": payload.get("username"),
                "role": role,
                "mustChangePassword": must_change,
            }

            if must_change and not allow_password_change:
                return jsonify({
                    "success": False,
                    "code": "password_change_required",
                    "message": "Password change required. Update your password before continuing.",
                }), 403

            return fn(*args, **kwargs)

        return wrapper

    return decorator
