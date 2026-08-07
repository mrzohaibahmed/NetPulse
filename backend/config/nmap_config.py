"""
Nmap scan profile validation for .env configuration.

Validated at import time (application startup) so invalid operator overrides
fail fast instead of producing obscure Nmap runtime errors.
"""

from __future__ import annotations

import os
import re
import shlex

# Nmap CLI arguments are passed to a subprocess; reject shell metacharacters.
_FORBIDDEN_SHELL_CHARS = re.compile(r"[;|&$`<>]")
_MAX_ARGUMENTS_LENGTH = 512

# Short flags (-O, -sV), long flags (--top-ports), timing (-T4).
_FLAG_TOKEN = re.compile(r"^--?[A-Za-z][A-Za-z0-9-]*$")
# Numeric / port-list values (100, 22,80,443, 1-65535).
_VALUE_TOKEN = re.compile(r"^[0-9]+(?:[,\-][0-9]+)*$")


def validate_nmap_arguments(name: str, raw: str | None, *, default: str) -> str:
    """
    Validate and normalize an Nmap arguments string from the environment.

    Parameters
    ----------
    name : str
        Environment variable name (for error messages).
    raw : str | None
        Value from ``os.getenv`` (may be None to use default).
    default : str
        Fallback when ``raw`` is None or blank.

    Returns
    -------
    str
        Stripped, validated argument string.

    Raises
    ------
    ValueError
        When the value is empty, too long, unsafe, or not parseable as Nmap args.
    """
    value = (raw if raw is not None else default).strip()
    if not value:
        raise ValueError(f"{name} must not be empty")

    if len(value) > _MAX_ARGUMENTS_LENGTH:
        raise ValueError(
            f"{name} exceeds maximum length ({_MAX_ARGUMENTS_LENGTH} characters)"
        )

    if "\n" in value or "\r" in value:
        raise ValueError(f"{name} must be a single line")

    if _FORBIDDEN_SHELL_CHARS.search(value):
        raise ValueError(
            f"{name} contains invalid characters (;|&$`<> are not allowed)"
        )

    try:
        # posix=False on Windows preserves backslash paths in NMAP_PATH-adjacent
        # edge cases; nmap flags themselves are POSIX-like on all platforms.
        tokens = shlex.split(value, posix=(os.name != "nt"))
    except ValueError as exc:
        raise ValueError(f"{name} is not a valid argument string: {exc}") from exc

    if not tokens:
        raise ValueError(f"{name} must contain at least one Nmap argument")

    invalid = [t for t in tokens if not _is_valid_nmap_token(t)]
    if invalid:
        bad = invalid[0]
        raise ValueError(
            f"{name} contains invalid token {bad!r}. "
            "Use standard Nmap flags (e.g. -O -sV -T4 --top-ports 100)."
        )

    return value


def _is_valid_nmap_token(token: str) -> bool:
    """Return True when a shlex token looks like a safe Nmap flag or value."""
    if _FLAG_TOKEN.match(token):
        return True
    if _VALUE_TOKEN.match(token):
        return True
    return False
