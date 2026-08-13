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
    rx_broadcast_packets=None,
    tx_broadcast_packets=None,
    rx_multicast_packets=None,
    tx_multicast_packets=None,
    rx_discards=None,
    tx_discards=None,
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
    cycle_id=None,
):
    """
    Build a historical interface statistics document.

    Documents are always inserted (never updated) so history is preserved.
    ``cycle_id`` is optional execution metadata for storm pipeline staging.
    """
    ts = timestamp or datetime.now(timezone.utc)

    doc = {
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
    if cycle_id:
        doc["cycleId"] = str(cycle_id)

    directional = (
        ("rxBroadcastPackets", rx_broadcast_packets),
        ("txBroadcastPackets", tx_broadcast_packets),
        ("rxMulticastPackets", rx_multicast_packets),
        ("txMulticastPackets", tx_multicast_packets),
        ("rxDiscards", rx_discards),
        ("txDiscards", tx_discards),
    )
    for key, value in directional:
        if value is not None:
            doc[key] = int(value)

    return doc
