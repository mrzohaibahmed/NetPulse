"""
interface_collection
====================
Interface discovery + periodic statistics for managed switches.

Public entry points:
- collector          — inventory discovery (SSH)
- stats_collector    — counter polling (SNMP preferred, SSH fallback)
"""

from services.interface_collection.collector import (
    discover_all_switch_interfaces,
    discover_device_interfaces,
    ensure_interface_indexes,
    get_interfaces,
)
from services.interface_collection.stats_collector import (
    collect_all_interface_stats,
    collect_device_interface_stats,
    ensure_interface_stats_indexes,
    get_interface_stats_history,
    get_latest_device_stats,
)

__all__ = [
    "discover_all_switch_interfaces",
    "discover_device_interfaces",
    "ensure_interface_indexes",
    "get_interfaces",
    "collect_all_interface_stats",
    "collect_device_interface_stats",
    "ensure_interface_stats_indexes",
    "get_interface_stats_history",
    "get_latest_device_stats",
]
