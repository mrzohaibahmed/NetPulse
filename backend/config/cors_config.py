"""
CORS configuration with explicit origins when credentials are enabled.

Production must not use wildcard origins with ``supports_credentials=True``.
"""

from __future__ import annotations

import os
from typing import Any

from config.deployment import get_app_environment, _flask_debug_enabled


def _parse_origins(raw: str) -> list[str]:
    parts = []
    for item in raw.replace("\n", ",").split(","):
        origin = item.strip()
        if origin:
            parts.append(origin.rstrip("/"))
    return parts


def _default_development_origins() -> list[str]:
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5000",
        "http://127.0.0.1:5000",
    ]


def resolve_cors_origins() -> list[str]:
    """
    Resolve allowed browser origins.

    Env ``CORS_ALLOWED_ORIGINS`` — comma-separated list.
    When unset in development, localhost Vite/Flask origins are allowed.
    When unset in production, boot fails closed.
    """
    raw = (os.getenv("CORS_ALLOWED_ORIGINS") or "").strip()
    if raw:
        origins = _parse_origins(raw)
        if not origins:
            raise RuntimeError(
                "CORS_ALLOWED_ORIGINS is set but empty after parsing. "
                "Provide at least one explicit origin."
            )
        return origins

    if _flask_debug_enabled() or get_app_environment() == "development":
        return _default_development_origins()

    raise RuntimeError(
        "CORS_ALLOWED_ORIGINS is required in production when credentials are "
        "enabled. Set explicit frontend origins (see .env.example)."
    )


def build_cors_kwargs() -> dict[str, Any]:
    origins = resolve_cors_origins()
    return {
        "origins": origins,
        "supports_credentials": True,
        "allow_headers": ["Authorization", "Content-Type"],
        "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    }
