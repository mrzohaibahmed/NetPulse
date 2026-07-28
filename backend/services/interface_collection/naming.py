"""
naming.py
=========
Vendor-agnostic interface name canonicalisation.

Used by discovery, statistics, and API consumers so that:

  Gi1/0/1  ==  Gig1/0/1  ==  GigabitEthernet1/0/1

never create duplicate inventory records or miss stats joins.
"""

from __future__ import annotations

import re


# Long-form / alias → preferred short storage prefix
_PREFIX_RULES: tuple[tuple[str, str], ...] = (
    ("tengigabitethernet", "Te"),
    ("tengigabiteth", "Te"),
    ("tengige", "Te"),
    ("tenGigE".lower(), "Te"),
    ("gigabitethernet", "Gi"),
    ("gigabiteth", "Gi"),
    ("gige", "Gi"),
    ("gig", "Gi"),
    ("fastethernet", "Fa"),
    ("fasteth", "Fa"),
    ("fortyGigabitEthernet".lower(), "Fo"),
    ("fortygigabitethernet", "Fo"),
    ("hundredGigE".lower(), "Hu"),
    ("hundredgigabitethernet", "Hu"),
    ("twentyFiveGigE".lower(), "Twe"),
    ("twentyfivegigabitethernet", "Twe"),
    ("ethernet", "Et"),
    ("port-channel", "Po"),
    ("portchannel", "Po"),
    ("vlan", "Vl"),
    ("loopback", "Lo"),
    ("management", "Ma"),
    ("mgmt", "Ma"),
)

# Already-short prefixes (case-insensitive) that should be title-cased
_SHORT_PREFIXES = (
    "twe", "te", "gi", "fa", "fo", "hu", "et", "po", "vl", "lo", "ma",
)


def canonicalize_interface_name(name: str | None) -> str:
    """
    Return a lowercase comparison key shared by short and long Cisco names.

    Examples
    --------
    GigabitEthernet1/0/1 → gi1/0/1
    Gi1/0/1              → gi1/0/1
    Gig1/0/1             → gi1/0/1
    """
    text = (name or "").strip().lower().replace(" ", "")
    if not text:
        return ""

    for full, short in _PREFIX_RULES:
        if text.startswith(full):
            return short.lower() + text[len(full):]

    for short in _SHORT_PREFIXES:
        if text.startswith(short) and len(text) > len(short):
            nxt = text[len(short)]
            if nxt.isdigit() or nxt in "/.-":
                return text

    return text


def normalize_storage_interface_name(name: str | None) -> str:
    """
    Collapse interface names to a stable short form for MongoDB storage.

    Keeps operator-familiar names (Gi1/0/1) while ensuring long-form and
    aliases map to the same document key.
    """
    text = re.sub(r"\s+", "", (name or "").strip())
    if not text:
        return ""

    lower = text.lower()

    for full, short in _PREFIX_RULES:
        if lower.startswith(full):
            return short + text[len(full):]

    # Normalise casing of already-short prefixes: gi1/0/1 → Gi1/0/1
    for short in _SHORT_PREFIXES:
        if lower.startswith(short) and len(text) > len(short):
            nxt = text[len(short)]
            if nxt.isdigit() or nxt in "/.-":
                # Preserve preferred casing from _PREFIX_RULES
                preferred = next(
                    (s for f, s in _PREFIX_RULES if s.lower() == short),
                    short[:1].upper() + short[1:],
                )
                # Prefer the first matching short from rules with exact lower match
                for _full, pref in _PREFIX_RULES:
                    if pref.lower() == short:
                        preferred = pref
                        break
                return preferred + text[len(short):]

    return text


def names_match(a: str | None, b: str | None) -> bool:
    """True when two interface names refer to the same port."""
    ca = canonicalize_interface_name(a)
    cb = canonicalize_interface_name(b)
    return bool(ca) and ca == cb
