from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from flask import Blueprint, g, jsonify, request
from pymongo.errors import DuplicateKeyError

from config.database import db
from services.audit_service import log_audit
from services.login_rate_limit import (
    check_login_allowed,
    clear_login_failures,
    record_login_failure,
)
from services.user_service import ensure_default_admin
from utils.api_errors import internal_error_response
from utils.auth import (
    JWT_EXPIRE_HOURS,
    VALID_ROLES,
    create_access_token,
    hash_password,
    normalize_role,
    require_auth,
    verify_password,
)
from utils.serializers import format_datetime

auth_bp = Blueprint("auth", __name__)


def serialize_user(user):
    return {
        "_id": str(user["_id"]),
        "username": user.get("username"),
        "role": normalize_role(user.get("role", "user")),
        "active": user.get("active") is not False,
        "mustChangePassword": bool(user.get("mustChangePassword")),
        "createdAt": format_datetime(user.get("createdAt")),
        "updatedAt": format_datetime(user.get("updatedAt")),
    }


def _client_ip() -> str:
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return (request.remote_addr or "").strip() or "unknown"


def _find_current_user():
    user_id = g.user.get("id")
    if user_id and ObjectId.is_valid(user_id):
        user = db.users.find_one({"_id": ObjectId(user_id)})
        if user:
            return user
    return db.users.find_one({"username": g.user.get("username")})


def _username_taken(username, exclude_id=None):
    query = {"username": username}
    if exclude_id is not None:
        query["_id"] = {"$ne": exclude_id}
    return db.users.find_one(query) is not None


@auth_bp.route("/auth/login", methods=["POST"])
def login():
    try:
        ensure_default_admin()
        data = request.get_json() or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        ip = _client_ip()

        if not username or not password:
            return jsonify({
                "success": False,
                "message": "Username and password are required",
            }), 400

        allowed, retry_after = check_login_allowed(username, ip)
        if not allowed:
            return jsonify({
                "success": False,
                "message": "Too many failed login attempts. Try again later.",
                "retryAfterSeconds": retry_after,
            }), 429

        user = db.users.find_one({"username": username})
        if not user or not verify_password(password, user.get("passwordHash", "")):
            result = record_login_failure(username, ip)
            payload = {
                "success": False,
                "message": "Invalid username or password",
            }
            if result.get("locked"):
                payload["retryAfterSeconds"] = result.get("retryAfterSeconds")
                return jsonify(payload), 429
            return jsonify(payload), 401

        if user.get("active") is False:
            return jsonify({
                "success": False,
                "message": "Account is disabled. Contact an administrator.",
            }), 403

        clear_login_failures(username, ip)

        token = create_access_token(
            user_id=str(user["_id"]),
            username=user["username"],
            role=normalize_role(user.get("role", "user")),
            must_change_password=bool(user.get("mustChangePassword")),
        )

        return jsonify({
            "success": True,
            "message": (
                "Password change required"
                if user.get("mustChangePassword")
                else "Login successful"
            ),
            "token": token,
            "expiresInHours": JWT_EXPIRE_HOURS,
            "user": serialize_user(user),
        }), 200

    except RuntimeError as error:
        # Bootstrap / configuration errors — safe message, no secrets.
        return jsonify({
            "success": False,
            "message": str(error),
        }), 503
    except Exception as error:
        return internal_error_response(error, message="Login failed")


@auth_bp.route("/auth/me", methods=["GET"])
@require_auth(allow_password_change=True)
def me():
    user = _find_current_user()
    if not user:
        return jsonify({
            "success": False,
            "message": "User not found",
        }), 404

    return jsonify({
        "success": True,
        "user": serialize_user(user),
    }), 200


@auth_bp.route("/auth/account", methods=["PUT"])
@require_auth(allow_password_change=True)
def update_own_account():
    """Change own username and/or password (FR7 access management)."""
    try:
        user = _find_current_user()
        if not user:
            return jsonify({"success": False, "message": "User not found"}), 404

        data = request.get_json() or {}
        current_password = data.get("currentPassword") or ""
        new_username = (data.get("username") or "").strip()
        new_password = data.get("newPassword") or ""
        must_change = bool(user.get("mustChangePassword"))

        if not current_password:
            return jsonify({
                "success": False,
                "message": "Current password is required",
            }), 400

        if not verify_password(current_password, user.get("passwordHash", "")):
            return jsonify({
                "success": False,
                "message": "Current password is incorrect",
            }), 400

        if must_change and not new_password:
            return jsonify({
                "success": False,
                "code": "password_change_required",
                "message": "Password change required. Provide a new password.",
            }), 400

        if not new_username and not new_password:
            return jsonify({
                "success": False,
                "message": "Provide a new username and/or new password",
            }), 400

        update: dict[str, Any] = {}
        update["updatedAt"] = datetime.now(timezone.utc)

        if new_username:
            if len(new_username) < 3:
                return jsonify({
                    "success": False,
                    "message": "Username must be at least 3 characters",
                }), 400
            if new_username != user["username"] and _username_taken(new_username, user["_id"]):
                return jsonify({
                    "success": False,
                    "message": "Username is already taken",
                }), 409
            update["username"] = new_username

        if new_password:
            if len(new_password) < 6:
                return jsonify({
                    "success": False,
                    "message": "New password must be at least 6 characters",
                }), 400
            if verify_password(new_password, user.get("passwordHash", "")):
                return jsonify({
                    "success": False,
                    "message": "New password must be different from the current password",
                }), 400
            update["passwordHash"] = hash_password(new_password)
            update["mustChangePassword"] = False

        try:
            db.users.update_one({"_id": user["_id"]}, {"$set": update})
        except DuplicateKeyError:
            return jsonify({
                "success": False,
                "message": "Username is already taken",
            }), 409
        updated = db.users.find_one({"_id": user["_id"]})

        token = create_access_token(
            user_id=str(updated["_id"]),
            username=updated["username"],
            role=normalize_role(updated.get("role", "user")),
            must_change_password=bool(updated.get("mustChangePassword")),
        )

        log_audit(
            action="account_updated",
            entity_type="user",
            entity_id=updated["_id"],
            details={
                "usernameChanged": "username" in update,
                "passwordChanged": "passwordHash" in update,
            },
        )

        return jsonify({
            "success": True,
            "message": "Account updated successfully",
            "token": token,
            "user": serialize_user(updated),
        }), 200

    except Exception as error:
        return internal_error_response(error, message="Failed to update account")


@auth_bp.route("/users", methods=["GET"])
@require_auth(roles=["admin"])
def list_users():
    try:
        users = [
            serialize_user(user)
            for user in db.users.find().sort("username", 1)
        ]
        return jsonify({
            "success": True,
            "count": len(users),
            "data": users,
        }), 200
    except Exception as error:
        return internal_error_response(error, message="Failed to list users")


@auth_bp.route("/users/<user_id>", methods=["PUT"])
@require_auth(roles=["admin"])
def update_user(user_id):
    """Admin can change another user's username, password, and/or role."""
    try:
        if not ObjectId.is_valid(user_id):
            return jsonify({"success": False, "message": "Invalid user ID"}), 400

        target = db.users.find_one({"_id": ObjectId(user_id)})
        if not target:
            return jsonify({"success": False, "message": "User not found"}), 404

        data = request.get_json() or {}
        new_username = (data.get("username") or "").strip()
        new_password = data.get("password") or data.get("newPassword") or ""
        raw_role = data.get("role")
        new_role = normalize_role(raw_role) if raw_role is not None else None
        raw_active = data.get("active")
        new_active = bool(raw_active) if raw_active is not None else None

        if (
            not new_username
            and not new_password
            and new_role is None
            and new_active is None
        ):
            return jsonify({
                "success": False,
                "message": "Provide a username, password, role, and/or active status to update",
            }), 400

        target_role = normalize_role(target.get("role"))

        if new_role is not None:
            if raw_role and str(raw_role).strip().lower() not in VALID_ROLES:
                return jsonify({
                    "success": False,
                    "message": f"Invalid role. Allowed: {', '.join(VALID_ROLES)}",
                }), 400

        update: dict[str, Any] = {}
        update["updatedAt"] = datetime.now(timezone.utc)

        if new_username:
            if len(new_username) < 3:
                return jsonify({
                    "success": False,
                    "message": "Username must be at least 3 characters",
                }), 400
            if new_username != target["username"] and _username_taken(
                new_username, target["_id"]
            ):
                return jsonify({
                    "success": False,
                    "message": "Username is already taken",
                }), 409
            update["username"] = new_username

        if new_password:
            if len(new_password) < 6:
                return jsonify({
                    "success": False,
                    "message": "Password must be at least 6 characters",
                }), 400
            update["passwordHash"] = hash_password(new_password)
            update["mustChangePassword"] = True

        if new_role is not None and new_role != target_role:
            update["role"] = new_role

        if new_active is not None and new_active != (target.get("active") is not False):
            update["active"] = new_active

        if _is_active_admin(target) and _active_admin_count() <= 1:
            would_lose_admin = (
                update.get("active") is False
                or ("role" in update and update["role"] != "admin")
            )
            if would_lose_admin:
                return jsonify({
                    "success": False,
                    "message": "Cannot remove the last active administrator",
                }), 400

        if len(update) == 1:
            return jsonify({
                "success": False,
                "message": "No changes to apply",
            }), 400

        try:
            db.users.update_one({"_id": target["_id"]}, {"$set": update})
        except DuplicateKeyError:
            return jsonify({
                "success": False,
                "message": "Username is already taken",
            }), 409
        updated = db.users.find_one({"_id": target["_id"]})

        log_audit(
            action="user_updated",
            entity_type="user",
            entity_id=updated["_id"],
            details={
                "targetUsername": updated.get("username"),
                "usernameChanged": "username" in update,
                "passwordChanged": "passwordHash" in update,
                "roleChanged": "role" in update,
                "activeChanged": "active" in update,
                "role": updated.get("role"),
                "active": updated.get("active") is not False,
            },
        )

        return jsonify({
            "success": True,
            "message": "User updated successfully",
            "data": serialize_user(updated),
        }), 200

    except Exception as error:
        return internal_error_response(error, message="Failed to update user")


def _active_admin_count() -> int:
    return db.users.count_documents({
        "role": "admin",
        "$or": [{"active": True}, {"active": {"$exists": False}}],
    })


def _is_active_admin(user_doc) -> bool:
    return (
        normalize_role(user_doc.get("role")) == "admin"
        and user_doc.get("active") is not False
    )


@auth_bp.route("/users", methods=["POST"])
@require_auth(roles=["admin"])
def create_user():
    try:
        data = request.get_json() or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        raw_role = data.get("role")
        active = data.get("active")

        if not username:
            return jsonify({"success": False, "message": "Username is required"}), 400
        if len(username) < 3:
            return jsonify({
                "success": False,
                "message": "Username must be at least 3 characters",
            }), 400
        if not password:
            return jsonify({"success": False, "message": "Password is required"}), 400
        if len(password) < 6:
            return jsonify({
                "success": False,
                "message": "Password must be at least 6 characters",
            }), 400
        if raw_role is None:
            return jsonify({"success": False, "message": "Role is required"}), 400
        if active is None:
            return jsonify({"success": False, "message": "Active status is required"}), 400

        role = normalize_role(raw_role)
        if str(raw_role).strip().lower() not in VALID_ROLES:
            return jsonify({
                "success": False,
                "message": f"Invalid role. Allowed: {', '.join(VALID_ROLES)}",
            }), 400

        if _username_taken(username):
            return jsonify({
                "success": False,
                "message": "Username is already taken",
            }), 409

        now = datetime.now(timezone.utc)
        doc = {
            "username": username,
            "passwordHash": hash_password(password),
            "role": role,
            "active": bool(active),
            "mustChangePassword": False,
            "createdAt": now,
            "updatedAt": now,
        }

        try:
            result = db.users.insert_one(doc)
        except DuplicateKeyError:
            return jsonify({
                "success": False,
                "message": "Username is already taken",
            }), 409

        created = db.users.find_one({"_id": result.inserted_id})

        log_audit(
            action="user_created",
            entity_type="user",
            entity_id=created["_id"],
            details={
                "username": created.get("username"),
                "role": created.get("role"),
                "active": created.get("active"),
            },
        )

        return jsonify({
            "success": True,
            "message": "User created successfully",
            "data": serialize_user(created),
        }), 201

    except Exception as error:
        return internal_error_response(error, message="Failed to create user")


@auth_bp.route("/users/<user_id>", methods=["DELETE"])
@require_auth(roles=["admin"])
def delete_user(user_id):
    try:
        if not ObjectId.is_valid(user_id):
            return jsonify({"success": False, "message": "Invalid user ID"}), 400

        target = db.users.find_one({"_id": ObjectId(user_id)})
        if not target:
            return jsonify({"success": False, "message": "User not found"}), 404

        if _is_active_admin(target) and _active_admin_count() <= 1:
            return jsonify({
                "success": False,
                "message": "Cannot delete the last active administrator",
            }), 400

        db.users.delete_one({"_id": target["_id"]})

        log_audit(
            action="user_deleted",
            entity_type="user",
            entity_id=target["_id"],
            details={"username": target.get("username"), "role": target.get("role")},
        )

        return jsonify({
            "success": True,
            "message": "User deleted successfully",
        }), 200

    except Exception as error:
        return internal_error_response(error, message="Failed to delete user")
