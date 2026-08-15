"""
Safe API error responses — never leak internals to clients in production.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from flask import g, has_request_context, jsonify

logger = logging.getLogger("netpulse.api_errors")


def _request_id() -> str:
    if has_request_context():
        existing = getattr(g, "request_id", None)
        if existing:
            return str(existing)
        rid = uuid.uuid4().hex[:12]
        g.request_id = rid
        return rid
    return uuid.uuid4().hex[:12]


def ensure_request_id() -> str:
    return _request_id()


def internal_error_response(
    exc: BaseException,
    *,
    message: str = "Internal server error",
    log_message: str | None = None,
):
    """
    Log the real exception server-side and return a generic client payload.

    Preserves intentional validation messages when callers return 400 themselves.
    """
    rid = _request_id()
    logger.error(
        "%s | requestId=%s | errorType=%s | detail=%s",
        log_message or message,
        rid,
        type(exc).__name__,
        type(exc).__name__,  # never include raw exception text (may hold secrets)
        exc_info=exc,
    )
    body: dict[str, Any] = {
        "success": False,
        "message": message,
        "error": message,
        "requestId": rid,
    }
    return jsonify(body), 500


def validation_error_response(message: str, *, status: int = 400):
    """Safe client-facing validation / business-rule error."""
    return jsonify({
        "success": False,
        "message": str(message),
        "requestId": _request_id(),
    }), status
