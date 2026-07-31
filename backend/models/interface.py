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
    monitoring_mode=None,
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
    from services.interface_collection.monitoring_state import (  # noqa: PLC0415
        MONITORING_MODE_AUTO,
        MONITORING_MODE_DISABLED_BY_USER,
        normalize_monitoring_mode,
        preference_enabled_for_mode,
    )

    now = datetime.now(timezone.utc)
    resolved_mode = (port_mode or mode or "unknown").lower()

    # Prefer explicit monitoring_mode; otherwise derive from preference mirror.
    resolved_monitoring_mode = normalize_monitoring_mode(monitoring_mode)
    if resolved_monitoring_mode is None:
        if monitoring_enabled is False:
            # Callers that pass monitoring_enabled=False without a mode mean
            # administrator opt-out (API / tests). Inventory rediscovery uses
            # monitoringMode explicitly and never relies on this path for latch.
            resolved_monitoring_mode = MONITORING_MODE_DISABLED_BY_USER
        else:
            resolved_monitoring_mode = MONITORING_MODE_AUTO
    preference = preference_enabled_for_mode(resolved_monitoring_mode)

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
        "monitoringMode": resolved_monitoring_mode,
        "monitoringEnabled": preference,
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
