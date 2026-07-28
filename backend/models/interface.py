"""
interface.py
============
Factory for vendor-independent interface documents stored in ``db.interfaces``.

Schema is designed for direct consumption by the future Storm Protection Engine
(Port Eligibility, Safety Checks, Risk Scoring, Mitigation).
"""

from datetime import datetime, timezone


def create_interface(
    device_id,
    hostname,
    ip_address,
    name,
    description="",
    admin_status="unknown",
    oper_status="unknown",
    mode="unknown",
    port_mode=None,
    is_access=False,
    is_trunk=False,
    is_uplink=False,
    is_infrastructure=False,
    is_management=False,
    is_protected=False,
    monitoring_enabled=True,
    access_vlan=None,
    voice_vlan=None,
    native_vlan=None,
    allowed_vlans=None,
    vlan="",
    speed="",
    speed_mbps=None,
    duplex="",
    neighbor=None,
    if_index=None,
    mac_address="",
    vendor="",
    collection_method="ssh",
):
    """
    Build a normalised interface document for MongoDB (camelCase API schema).
    """
    now = datetime.now(timezone.utc)
    resolved_mode = (port_mode or mode or "unknown").lower()

    return {
        "deviceId": device_id,
        "hostname": hostname,
        "ipAddress": ip_address,
        "name": name,
        "description": description or "",
        "adminStatus": admin_status or "unknown",
        "operStatus": oper_status or "unknown",
        # Legacy + Storm Protection fields
        "mode": resolved_mode,
        "portMode": resolved_mode,
        "isAccess": bool(is_access),
        "isTrunk": bool(is_trunk),
        "isUplink": bool(is_uplink),
        "isInfrastructure": bool(is_infrastructure),
        "isManagement": bool(is_management),
        "isProtected": bool(is_protected),
        "monitoringEnabled": bool(monitoring_enabled),
        "accessVlan": access_vlan,
        "voiceVlan": voice_vlan,
        "nativeVlan": native_vlan,
        "allowedVlans": list(allowed_vlans or []),
        "vlan": vlan if vlan is not None else "",
        "speed": speed or "",
        "speedMbps": speed_mbps,
        "duplex": duplex or "",
        "neighbor": neighbor,
        "ifIndex": if_index,
        "macAddress": mac_address or "",
        "vendor": vendor or "",
        "collectionMethod": collection_method or "ssh",
        "lastUpdated": now,
        "createdAt": now,
        "updatedAt": now,
    }
