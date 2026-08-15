"""Helpers for safe MongoDB query construction from user input."""

from __future__ import annotations

import re
from typing import Any


def escape_regex(value: str, *, max_length: int = 200) -> str:
    """
    Escape a user-supplied search string for use in MongoDB $regex.

    Truncates overly long input to bound ReDoS / memory risk.
    """
    text = (value or "").strip()
    if len(text) > max_length:
        text = text[:max_length]
    return re.escape(text)


def regex_filter(value: str, *, max_length: int = 200) -> dict[str, Any]:
    """Build a case-insensitive literal MongoDB regex clause."""
    return {"$regex": escape_regex(value, max_length=max_length), "$options": "i"}
