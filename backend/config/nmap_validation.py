"""
Startup validation for Nmap CLI argument strings from .env.

Ensures operator-supplied profiles are non-empty, safe to pass to the
nmap subprocess, and look like plausible flag sequences.
"""

from __future__ import annotations

import os
import re
import shlex

# Characters that must not appear in a literal arguments string (shell injection).
_FORBIDDEN_CHARS = frozenset(";&|`$()<>\n\r")

# Each token should look like an nmap flag or flag value (alphanumeric + common punctuation).
_TOKEN_RE = re.compile(r"^[\w./:@%,+-]+$")

_DEFAULT_QUICK = "-O -sV -T4 --top-ports 100"
_DEFAULT_DEEP = "-A -T4"


def validate_nmap_arguments(name: str, value: str | None) -> str:
    """
    Validate and normalize an Nmap arguments string.

    Parameters
    ----------
    name : str
        Env var name (for error messages), e.g. ``NMAP_QUICK_ARGUMENTS``.
    value : str | None
        Raw value from the environment.

    Returns
    -------
    str
        Stripped, validated arguments string.

    Raises
    ------
    ValueError
        When the value is missing, empty, unsafe, or unparsable.
    """
    if value is None or not str(value).strip():
        raise ValueError(
            f"{name} must be a non-empty Nmap argument string "
            f"(e.g. {_DEFAULT_QUICK!r} for quick inventory scans)."
        )

    normalized = str(value).strip()

    if len(normalized) > 512:
        raise ValueError(f"{name} exceeds maximum length of 512 characters.")

    bad = _FORBIDDEN_CHARS.intersection(normalized)
    if bad:
        raise ValueError(
            f"{name} contains disallowed character(s): {''.join(sorted(bad))!r}. "
            "Use a plain Nmap flag string only."
        )

    try:
        tokens = shlex.split(normalized, posix=os.name != "nt")
    except ValueError as exc:
        raise ValueError(f"{name} is not a valid argument string: {exc}") from exc

    if not tokens:
        raise ValueError(f"{name} must contain at least one Nmap flag.")

    for token in tokens:
        if not _TOKEN_RE.match(token):
            raise ValueError(
                f"{name} contains invalid token {token!r}. "
                "Use standard Nmap flags and values only."
            )

    # Require at least one flag-like token (starts with '-').
    if not any(t.startswith("-") for t in tokens):
        raise ValueError(
            f"{name} must include at least one Nmap flag (token starting with '-')."
        )

    return normalized


def validate_nmap_scan_profiles(
    *,
    quick_arguments: str | None,
    deep_arguments: str | None,
) -> tuple[str, str]:
    """
    Validate both inventory (quick) and diagnostic (deep) profiles.

    Returns normalized (quick, deep) strings.
    """
    quick = validate_nmap_arguments("NMAP_QUICK_ARGUMENTS", quick_arguments)
    deep = validate_nmap_arguments("NMAP_ARGUMENTS", deep_arguments)
    return quick, deep
