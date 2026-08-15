"""
JWT_SECRET validation for production and development.

Never logs or returns the secret value.
"""

from __future__ import annotations

import os
import re

# Exact strings that must never be used outside controlled debug.
_BLOCKED_EXACT = frozenset(
    {
        "netpulse-dev-secret-change-me",
        "change-me",
        "change-me-in-production",
        "secret",
        "password",
        "jwt-secret",
        "your-secret",
        "test",
        "testing",
        "dev",
        "development",
    }
)

# Substrings that indicate placeholder / sample secrets.
_BLOCKED_SUBSTRINGS = (
    "change-this",
    "change-me",
    "replace-with",
    "replace-me",
    "your-secret",
    "placeholder",
    "example",
    "todo",
    "changeme",
    "netpulse-dev-secret",
)

_MIN_PRODUCTION_LENGTH = 32


def _flask_debug_enabled() -> bool:
    return os.getenv("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")


def is_weak_jwt_secret(secret: str) -> bool:
    """Return True when the secret is empty, short, or a known placeholder."""
    value = (secret or "").strip()
    if not value:
        return True
    lowered = value.lower()
    if lowered in _BLOCKED_EXACT:
        return True
    if any(part in lowered for part in _BLOCKED_SUBSTRINGS):
        return True
    if len(value) < _MIN_PRODUCTION_LENGTH:
        return True
    # Reject trivial repeated characters (e.g. "aaaaaaaa...").
    if len(set(value)) < 8:
        return True
    # Reject secrets that are only hex of insufficient entropy pattern.
    if re.fullmatch(r"(.)\1{31,}", value):
        return True
    return False


def resolve_jwt_secret(*, allow_insecure_dev: bool | None = None) -> str:
    """
    Resolve JWT_SECRET from the environment.

    Production / non-debug: requires a strong unique secret; raises RuntimeError
    with a message that never includes the secret value.

    Debug (FLASK_DEBUG): allows a weak/default secret for local development only.
    """
    if allow_insecure_dev is None:
        allow_insecure_dev = _flask_debug_enabled()

    raw = (os.getenv("JWT_SECRET") or "").strip()

    if not is_weak_jwt_secret(raw):
        return raw

    if allow_insecure_dev:
        # Controlled local-only fallback — never used when FLASK_DEBUG is off.
        return raw or "netpulse-dev-secret-change-me"

    raise RuntimeError(
        "JWT_SECRET is missing or too weak for production. "
        "Set a unique secret of at least 32 characters that is not a "
        "placeholder (see .env.example). Generate one with: "
        "python -c \"import secrets; print(secrets.token_urlsafe(48))\". "
        "For local development only, set FLASK_DEBUG=true."
    )
