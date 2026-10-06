"""
Network destination validation and SSRF protection for NetPulse (Phase 3A).

Validates iLO IP addresses and DNS hostnames against prohibited ranges
(loopback, link-local, multicast, unspecified, IPv4-mapped prohibited addresses).
Permits private RFC 1918 subnets (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) and
valid public management addresses.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Sequence

PROHIBITED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("0.0.0.0/32"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("ff00::/8"),
)


class RedfishSSRFError(ValueError):
    """Raised when an iLO management address resolves to a prohibited destination."""


def _normalize_ip_object(value: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        ip_obj = value
    else:
        text = str(value).strip()
        try:
            ip_obj = ipaddress.ip_address(text)
        except ValueError as exc:
            raise RedfishSSRFError(f"Invalid IP address format: {value}") from exc

    # Unmap IPv4-mapped IPv6 address (e.g. ::ffff:127.0.0.1 -> 127.0.0.1)
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        return ip_obj.ipv4_mapped
    return ip_obj


def is_prohibited_ip(value: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """
    Check whether an IP address is a prohibited SSRF target.

    Rejects loopback, link-local, multicast, unspecified, and reserved IPs.
    Allows standard RFC 1918 private subnets and public unicast addresses.
    """
    try:
        ip_obj = _normalize_ip_object(value)
    except RedfishSSRFError:
        return True

    if (
        ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
        or ip_obj.is_reserved
    ):
        return True

    for net in PROHIBITED_NETWORKS:
        if ip_obj in net:
            return True

    return False


def resolve_and_validate_destination(
    address: str,
    *,
    dns_resolver: Any = None,
) -> tuple[str, list[str]]:
    """
    Validate an iLO management address (IPv4 literal or DNS hostname).

    Resolves DNS hostnames ONCE, validates all candidate IPs against prohibited
    destinations, and returns (selected_ip, list_of_all_validated_ips).

    Raises RedfishSSRFError if the address or all resolved IPs are prohibited.
    """
    if not address or not isinstance(address, str) or not address.strip():
        raise RedfishSSRFError("Management address is required")

    text = address.strip()

    # IPv4 / IPv6 literal
    try:
        ip_obj = ipaddress.ip_address(text)
    except ValueError:
        # Not an IP literal — proceed to DNS hostname resolution
        pass
    else:
        unmapped = _normalize_ip_object(ip_obj)
        if is_prohibited_ip(unmapped):
            raise RedfishSSRFError(
                f"Prohibited destination IP address: {text}"
            )
        ip_str = str(unmapped)
        return ip_str, [ip_str]

    # DNS Hostname Resolution
    resolver = dns_resolver or socket.getaddrinfo
    try:
        infos = resolver(text, 443, socket.AF_INET, socket.SOCK_STREAM)
    except Exception as exc:  # noqa: BLE001
        raise RedfishSSRFError(
            f"Failed to resolve DNS hostname '{text}': {exc}"
        ) from exc

    if not infos:
        raise RedfishSSRFError(f"DNS resolution returned no IPv4 records for '{text}'")

    raw_ips: list[str] = []
    validated_ips: list[str] = []

    for info in infos:
        sockaddr = info[4]
        ip_candidate = sockaddr[0]
        if ip_candidate not in raw_ips:
            raw_ips.append(ip_candidate)
        if not is_prohibited_ip(ip_candidate) and ip_candidate not in validated_ips:
            validated_ips.append(ip_candidate)

    if not validated_ips:
        raise RedfishSSRFError(
            f"All resolved IP addresses for '{text}' ({raw_ips}) are prohibited SSRF destinations"
        )

    return validated_ips[0], validated_ips
