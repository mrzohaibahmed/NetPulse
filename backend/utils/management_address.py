"""OS IP and iLO management-address validation for device identity (Step 2)."""

from __future__ import annotations

import ipaddress
import re

# Device types allowed to exist without an OS IP (iLO-only).
ILO_ONLY_DEVICE_TYPES = frozenset({
    "server",
    "linux server",
    "esxi server",
})

# Existing NetPulse OS-IP contract (IPv4 only).
IPV4_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)$"
)

# DNS hostname: labels of letters/digits/hyphen, not starting/ending with hyphen.
_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_DNS_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*$"
)


def is_ilo_only_device_type(device_type: str | None) -> bool:
    return (device_type or "").strip().lower() in ILO_ONLY_DEVICE_TYPES


def usable_os_ip(value) -> str | None:
    """Return a non-empty OS IP string, or None if missing/unusable."""
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def device_has_usable_os_ip(device: dict | None) -> bool:
    return usable_os_ip((device or {}).get("ipAddress")) is not None


def normalize_os_ip(value) -> str:
    """Validate and return a canonical IPv4 OS address. Raises ValueError."""
    if value is None:
        raise ValueError("ipAddress is required")
    text = str(value).strip()
    if not text:
        raise ValueError("ipAddress is required")
    if not IPV4_RE.match(text):
        raise ValueError("Invalid IPv4 address")
    return text


def normalize_ilo_address(value) -> str:
    """
    Validate and normalize an iLO management address.

    Accepts IPv4 or DNS hostname. DNS hostnames are lowercased.
    Rejects URL syntax (schemes, paths, ports, userinfo), whitespace, and
    empty/malformed values.
    """
    if value is None:
        raise ValueError("iloAddress is required")
    if not isinstance(value, str):
        raise ValueError("Invalid iloAddress")
    if not value:
        raise ValueError("iloAddress is required")
    if any(ch.isspace() for ch in value):
        raise ValueError("iloAddress must not contain whitespace")

    text = value
    lower = text.lower()
    if "://" in text or lower.startswith("http:") or lower.startswith("https:"):
        raise ValueError("iloAddress must not be a URL")
    if "@" in text:
        raise ValueError("iloAddress must not include userinfo")
    if "/" in text or "\\" in text:
        raise ValueError("iloAddress must not include a path")
    if "?" in text or "#" in text:
        raise ValueError("iloAddress must not include URL components")
    if ":" in text:
        # IPv4 with port or hostname:port — reject embedded URL ports.
        # (IPv6 is out of scope for Step 2.)
        raise ValueError("iloAddress must not include a port")

    # IPv4 literal
    if IPV4_RE.match(text):
        try:
            return str(ipaddress.IPv4Address(text))
        except ValueError as exc:
            raise ValueError("Invalid iloAddress") from exc

    # DNS hostname (normalize to lowercase)
    host = text.lower()
    if host.endswith("."):
        host = host[:-1]
    if not host or host.startswith(".") or ".." in host:
        raise ValueError("Invalid iloAddress hostname")
    if not _DNS_HOSTNAME_RE.match(host):
        raise ValueError("Invalid iloAddress hostname")
    for label in host.split("."):
        if not _DNS_LABEL_RE.match(label):
            raise ValueError("Invalid iloAddress hostname")
    return host
