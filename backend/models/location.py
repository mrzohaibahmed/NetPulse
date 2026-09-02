"""Site/location helpers for multi-site monitoring."""

from __future__ import annotations

# Initial supported locations; additional values are allowed for future sites.
SITE_LOCATIONS: tuple[str, ...] = ("Mills", "Karachi", "Lahore")

DEFAULT_SITE_LOCATION = "Mills"

ISPS_PER_SITE = 3


def canonical_site_location(value: str | None) -> str | None:
    """Normalize site names, including legacy Mill -> Mills."""
    normalized = normalize_location(value)
    if normalized is None:
        return None
    if normalized.lower() == "mill":
        return "Mills"
    return normalized


def normalize_location(value: str | None) -> str | None:
    """Normalize optional location text; returns None when unset."""
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def validate_location(value: str | None) -> str | None:
    """Validate optional location; raises ValueError when invalid."""
    normalized = normalize_location(value)
    if normalized is None:
        return None
    if len(normalized) > 64:
        raise ValueError("location must be 64 characters or fewer")
    return canonical_site_location(normalized)


def isp_slot_ids_for_location(location: str) -> tuple[str, ...]:
    """Return the three ISP slot ids for a site."""
    key = (canonical_site_location(location) or DEFAULT_SITE_LOCATION).strip().lower().replace(" ", "-")
    if key in ("mill", "mills"):
        return ("isp-1", "isp-2", "isp-3")
    return tuple(f"{key}-isp-{index}" for index in range(1, ISPS_PER_SITE + 1))


def all_isp_slot_ids() -> tuple[str, ...]:
    slots: list[str] = []
    for location in SITE_LOCATIONS:
        slots.extend(isp_slot_ids_for_location(location))
    return tuple(slots)
