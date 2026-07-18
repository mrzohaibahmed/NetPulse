from datetime import datetime, timezone

from bson import ObjectId
from flask import Blueprint, g, jsonify, request

from config.database import db
from services.audit_service import log_audit
from services.user_service import ensure_default_admin
from utils.auth import (
    JWT_EXPIRE_HOURS,
    create_access_token,
    hash_password,
    require_auth,
    verify_password,
)
from utils.serializers import format_datetime

auth_bp = Blueprint("auth", __name__)


def serialize_user(user):
    return {
        "_id": str(user["_id"]),
        "username": user.get("username"),
        "role": user.get("role", "viewer"),
        "createdAt": format_datetime(user.get("createdAt")),
        "updatedAt": format_datetime(user.get("updatedAt")),
    }


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

        if not username or not password:
            return jsonify({
                "success": False,
                "message": "Username and password are required",
            }), 400

        user = db.users.find_one({"username": username})
        if not user or not verify_password(password, user.get("passwordHash", "")):
            return jsonify({
                "success": False,
                "message": "Invalid username or password",
            }), 401

        token = create_access_token(
            user_id=str(user["_id"]),
            username=user["username"],
            role=user.get("role", "viewer"),
        )

        return jsonify({
            "success": True,
            "message": "Login successful",
            "token": token,
            "expiresInHours": JWT_EXPIRE_HOURS,
            "user": serialize_user(user),
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Login failed",
            "error": str(error),
        }), 500


@auth_bp.route("/auth/me", methods=["GET"])
@require_auth()
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
@require_auth()
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

        if not new_username and not new_password:
            return jsonify({
                "success": False,
                "message": "Provide a new username and/or new password",
            }), 400

        update = {"updatedAt": datetime.now(timezone.utc)}

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
            update["passwordHash"] = hash_password(new_password)

        db.users.update_one({"_id": user["_id"]}, {"$set": update})
        updated = db.users.find_one({"_id": user["_id"]})

        token = create_access_token(
            user_id=str(updated["_id"]),
            username=updated["username"],
            role=updated.get("role", "viewer"),
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
        return jsonify({
            "success": False,
            "message": "Failed to update account",
            "error": str(error),
        }), 500


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
        return jsonify({
            "success": False,
            "message": "Failed to list users",
            "error": str(error),
        }), 500


@auth_bp.route("/users/<user_id>", methods=["PUT"])
@require_auth(roles=["admin"])
def update_user(user_id):
    """Admin can change another user's username and/or password."""
    try:
        if not ObjectId.is_valid(user_id):
            return jsonify({"success": False, "message": "Invalid user ID"}), 400

        target = db.users.find_one({"_id": ObjectId(user_id)})
        if not target:
            return jsonify({"success": False, "message": "User not found"}), 404

        data = request.get_json() or {}
        new_username = (data.get("username") or "").strip()
        new_password = data.get("password") or data.get("newPassword") or ""

        if not new_username and not new_password:
            return jsonify({
                "success": False,
                "message": "Provide a username and/or password to update",
            }), 400

        update = {"updatedAt": datetime.now(timezone.utc)}

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

        db.users.update_one({"_id": target["_id"]}, {"$set": update})
        updated = db.users.find_one({"_id": target["_id"]})

        log_audit(
            action="user_updated",
            entity_type="user",
            entity_id=updated["_id"],
            details={
                "targetUsername": updated.get("username"),
                "usernameChanged": "username" in update,
                "passwordChanged": "passwordHash" in update,
            },
        )

        return jsonify({
            "success": True,
            "message": "User updated successfully",
            "data": serialize_user(updated),
        }), 200

    except Exception as error:
        return jsonify({
            "success": False,
            "message": "Failed to update user",
            "error": str(error),
        }), 500
