"""
Parser for Redfish EthernetInterfaces / NetworkInterfaces resources (Phase 3B).

Normalizes host/server NICs separately from the iLO management interface, ensuring
is_management_interface is explicitly set.
"""

from __future__ import annotations

from typing import Any

from services.server_hardware.models import ServerNetworkInterface
from services.server_hardware.parsers.utils import (
    clean_int,
    clean_string,
    extract_component_id,
    logger,
    parse_status,
)


def parse_network_interfaces(
    host_network_data: dict[str, Any] | list[Any] | None = None,
    manager_network_data: dict[str, Any] | list[Any] | None = None,
) -> list[ServerNetworkInterface]:
    """
    Parse host NICs and iLO manager network interfaces.

    Host NICs receive is_management_interface = False.
    Manager interface receives is_management_interface = True.
    """
    interfaces: list[ServerNetworkInterface] = []

    # 1. Parse Host EthernetInterfaces
    if host_network_data:
        raw_host = (
            host_network_data["Members"]
            if isinstance(host_network_data, dict) and "Members" in host_network_data and isinstance(host_network_data["Members"], list)
            else (host_network_data if isinstance(host_network_data, list) else [host_network_data])
        )

        for idx, item in enumerate(raw_host):
            if not isinstance(item, dict):
                logger.warning("Skipping malformed host network entry at index %d", idx)
                continue

            nic_id = extract_component_id(item, f"nic-host-{idx + 1}")
            if not nic_id:
                logger.warning("Skipping host network entry missing identifier at index %d", idx)
                continue

            speed_mbps = clean_int(item.get("SpeedMbps"))
            if speed_mbps is None:
                speed_raw = clean_int(item.get("MaxSpeedMbps") or item.get("Speed"))
                if speed_raw is not None:
                    speed_mbps = speed_raw

            nic_obj = ServerNetworkInterface(
                id=nic_id,
                name=clean_string(item.get("Name") or item.get("Id")),
                mac_address=clean_string(item.get("MACAddress") or item.get("PermanentMACAddress")),
                link_status=clean_string(item.get("LinkStatus") or item.get("InterfaceEnabled")),
                speed_mbps=speed_mbps,
                manufacturer=clean_string(item.get("Manufacturer")),
                model=clean_string(item.get("Model")),
                is_management_interface=False,
                status=parse_status(item),
            )
            interfaces.append(nic_obj)

    # 2. Parse Manager (iLO) EthernetInterface
    if manager_network_data:
        raw_mgr = (
            manager_network_data["Members"]
            if isinstance(manager_network_data, dict) and "Members" in manager_network_data and isinstance(manager_network_data["Members"], list)
            else (manager_network_data if isinstance(manager_network_data, list) else [manager_network_data])
        )

        for idx, item in enumerate(raw_mgr):
            if not isinstance(item, dict):
                logger.warning("Skipping malformed manager network entry at index %d", idx)
                continue

            mgr_nic_id = extract_component_id(item, f"nic-ilo-{idx + 1}")
            if not mgr_nic_id:
                logger.warning("Skipping manager network entry missing identifier at index %d", idx)
                continue

            mgr_speed = clean_int(item.get("SpeedMbps"))

            mgr_nic_obj = ServerNetworkInterface(
                id=mgr_nic_id,
                name=clean_string(item.get("Name") or "iLO Dedicated Management Port"),
                mac_address=clean_string(item.get("MACAddress") or item.get("PermanentMACAddress")),
                link_status=clean_string(item.get("LinkStatus")),
                speed_mbps=mgr_speed,
                manufacturer=clean_string(item.get("Manufacturer")),
                model=clean_string(item.get("Model")),
                is_management_interface=True,
                status=parse_status(item),
            )
            interfaces.append(mgr_nic_obj)

    return interfaces
