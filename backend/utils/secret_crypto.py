"""
Field-level encryption for secrets persisted in MongoDB (Fernet).

Ciphertexts are stored with a ``npenc:`` prefix so legacy plaintext values remain
readable until an operator runs the one-time migration script.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

CIPHER_PREFIX = "npenc:"


def _flask_debug_enabled() -> bool:
    """Same FLASK_DEBUG semantics as app.py / utils.auth (default off)."""
    return os.getenv("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet | None:
    """
    Return a Fernet instance, or None in local debug when no key is configured
    (encrypt/decrypt become passthrough). Non-debug requires SECRETS_ENCRYPTION_KEY.
    """
    raw = (os.getenv("SECRETS_ENCRYPTION_KEY") or "").strip()
    if raw:
        try:
            key = raw.encode("ascii") if isinstance(raw, str) else raw
            return Fernet(key)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "SECRETS_ENCRYPTION_KEY is invalid. Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            ) from exc

    if _flask_debug_enabled():
        return None

    raise RuntimeError(
        "SECRETS_ENCRYPTION_KEY is unset. Set a Fernet key in the environment "
        "(see .env.example) before starting NetPulse. For local development only, "
        "set FLASK_DEBUG=true (secrets will be stored without encryption)."
    )


def ensure_secrets_encryption_configured() -> None:
    """Eager boot check so misconfiguration fails at startup, not first write."""
    _get_fernet()


def is_encrypted(value: str | None) -> bool:
    return bool(value) and str(value).startswith(CIPHER_PREFIX)


def encrypt_secret(plaintext: str | None) -> str | None:
    """Encrypt a secret for Mongo persistence. Empty/None pass through."""
    if plaintext is None or plaintext == "":
        return plaintext
    text = str(plaintext)
    if is_encrypted(text):
        return text
    fernet = _get_fernet()
    if fernet is None:
        return text
    token = fernet.encrypt(text.encode("utf-8")).decode("ascii")
    return f"{CIPHER_PREFIX}{token}"


def decrypt_secret(value: str | None) -> str | None:
    """
    Decrypt a Mongo-stored secret for use at connection time.

    Values without the ``npenc:`` prefix are treated as legacy plaintext.
    """
    if value is None or value == "":
        return value
    text = str(value)
    if not is_encrypted(text):
        return text
    fernet = _get_fernet()
    if fernet is None:
        raise RuntimeError(
            "Encrypted secret found in the database but SECRETS_ENCRYPTION_KEY "
            "is not configured. Set the key used to encrypt these values."
        )
    token = text[len(CIPHER_PREFIX) :].encode("ascii")
    try:
        return fernet.decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError(
            "Failed to decrypt a stored secret. SECRETS_ENCRYPTION_KEY does not "
            "match the key used to encrypt this value."
        ) from exc
