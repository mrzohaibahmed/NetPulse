"""Tests for Nmap .env argument validation at startup."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from config.nmap_validation import (
    validate_nmap_arguments,
    validate_nmap_scan_profiles,
)


def test_valid_quick_and_deep_defaults():
    quick, deep = validate_nmap_scan_profiles(
        quick_arguments="-O -sV -T4 --top-ports 100",
        deep_arguments="-A -T4",
    )
    assert quick == "-O -sV -T4 --top-ports 100"
    assert deep == "-A -T4"


def test_valid_alternate_quick_profile():
    quick = validate_nmap_arguments("NMAP_QUICK_ARGUMENTS", "-sV -T4 -F")
    assert quick == "-sV -T4 -F"


def test_rejects_empty():
    with pytest.raises(ValueError, match="non-empty"):
        validate_nmap_arguments("NMAP_QUICK_ARGUMENTS", "   ")


def test_rejects_shell_metacharacters():
    with pytest.raises(ValueError, match="disallowed"):
        validate_nmap_arguments("NMAP_QUICK_ARGUMENTS", "-sV; rm -rf /")


def test_rejects_no_flags():
    with pytest.raises(ValueError, match="at least one Nmap flag"):
        validate_nmap_arguments("NMAP_QUICK_ARGUMENTS", "scanme only")


def test_rejects_unbalanced_quotes():
    with pytest.raises(ValueError, match="not a valid argument string"):
        validate_nmap_arguments("NMAP_ARGUMENTS", '-A "unclosed')


def test_strips_whitespace():
    assert validate_nmap_arguments("NMAP_ARGUMENTS", "  -A -T4  ") == "-A -T4"
