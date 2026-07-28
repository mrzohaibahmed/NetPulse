"""
interface_stats.py
==================
Factory for append-only interface statistics documents (``interface_stats``).
"""

from datetime import datetime, timezone


def create_interface_stat(
    device_id,
    hostname,
    ip_address,
    interface_name,
    *,
    rx_bytes=0,
    tx_bytes=0,
    rx_packets=0,
    tx_packets=0,
    broadcast_packets=0,
    multicast_packets=0,
    input_errors=0,
    output_errors=0,
    discards=0,
    utilization=None,
    rx_utilization=None,
    tx_utilization=None,
    speed_bps=None,
    if_index=None,
    collection_method="snmp",
    timestamp=None,
):
    """
    Build a historical interface statistics document.

    Documents are always inserted (never updated) so history is preserved.
    """
    ts = timestamp or datetime.now(timezone.utc)

    return {
        "deviceId": device_id,
        "hostname": hostname or "",
        "ipAddress": ip_address or "",
        "interfaceName": interface_name,
        "ifIndex": if_index,
        "rxBytes": int(rx_bytes or 0),
        "txBytes": int(tx_bytes or 0),
        "rxPackets": int(rx_packets or 0),
        "txPackets": int(tx_packets or 0),
        "broadcastPackets": int(broadcast_packets or 0),
        "multicastPackets": int(multicast_packets or 0),
        "inputErrors": int(input_errors or 0),
        "outputErrors": int(output_errors or 0),
        "discards": int(discards or 0),
        "utilization": utilization,
        "rxUtilization": rx_utilization,
        "txUtilization": tx_utilization,
        "speedBps": int(speed_bps) if speed_bps is not None else None,
        "collectionMethod": collection_method or "snmp",
        "timestamp": ts,
    }
