from datetime import timezone


def format_datetime(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        if getattr(value, "tzinfo", None) is None:
            return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        iso = value.isoformat()
        return iso.replace("+00:00", "Z") if iso.endswith("+00:00") else iso
    return value


def get_device_type(device):
    return device.get("deviceType") or device.get("type")


def serialize_network_info(network_info: dict | None) -> dict | None:
    """
    Serialise the networkInfo sub-document stored by nmap_service.

    Converts the ``lastScan`` datetime to an ISO-8601 string and returns
    the dict as-is for all other fields (ports, services, os, etc.).
    Returns ``None`` when networkInfo has not yet been populated.
    """
    if not network_info:
        return None

    result = dict(network_info)  # shallow copy — do not mutate the mongo doc

    # Ensure lastScan datetime is JSON-serialisable.
    if "lastScan" in result:
        result["lastScan"] = format_datetime(result["lastScan"])

    return result


def serialize_interface_stat(stat: dict) -> dict:
    """Serialise a document from the ``interface_stats`` collection."""
    return {
        "_id": str(stat["_id"]),
        "deviceId": str(stat["deviceId"]),
        "hostname": stat.get("hostname"),
        "ipAddress": stat.get("ipAddress"),
        "interfaceName": stat.get("interfaceName"),
        "ifIndex": stat.get("ifIndex"),
        "rxBytes": stat.get("rxBytes", 0),
        "txBytes": stat.get("txBytes", 0),
        "rxPackets": stat.get("rxPackets", 0),
        "txPackets": stat.get("txPackets", 0),
        "broadcastPackets": stat.get("broadcastPackets", 0),
        "multicastPackets": stat.get("multicastPackets", 0),
        "rxBroadcastPackets": stat.get("rxBroadcastPackets"),
        "txBroadcastPackets": stat.get("txBroadcastPackets"),
        "rxMulticastPackets": stat.get("rxMulticastPackets"),
        "txMulticastPackets": stat.get("txMulticastPackets"),
        "inputErrors": stat.get("inputErrors", 0),
        "outputErrors": stat.get("outputErrors", 0),
        "discards": stat.get("discards", 0),
        "rxDiscards": stat.get("rxDiscards"),
        "txDiscards": stat.get("txDiscards"),
        "utilization": stat.get("utilization"),
        "rxUtilization": stat.get("rxUtilization"),
        "txUtilization": stat.get("txUtilization"),
        "speedBps": stat.get("speedBps"),
        "collectionMethod": stat.get("collectionMethod") or "snmp",
        "timestamp": format_datetime(stat.get("timestamp")),
    }


def serialize_credentials(credentials: dict | None) -> dict | None:
    """
    Serialise device SSH/SNMP credentials without exposing secrets.

    Passwords / enable secrets are replaced with a boolean ``configured`` flag.
    """
    if not credentials:
        return None

    has_password = bool(credentials.get("sshPassword"))
    has_secret = bool(credentials.get("sshSecret"))

    return {
        "sshUsername": credentials.get("sshUsername") or "",
        "sshPort": credentials.get("sshPort") or 22,
        "sshVendor": credentials.get("sshVendor") or "",
        "sshPasswordConfigured": has_password,
        "sshSecretConfigured": has_secret,
        "snmpCommunityConfigured": bool(credentials.get("snmpCommunity")),
        "snmpPort": credentials.get("snmpPort") or 161,
        "snmpVersion": credentials.get("snmpVersion") or "2c",
    }


def serialize_interface(interface: dict) -> dict:
    """Serialise a document from the ``interfaces`` collection."""
    from services.interface_collection.monitoring_state import (  # noqa: PLC0415
        compute_monitoring_view,
    )

    port_mode = (
        interface.get("portMode")
        or interface.get("mode")
        or "unknown"
    )
    neighbor = _serialize_neighbor(interface.get("neighbor"))
    monitoring = compute_monitoring_view(
        monitoring_mode=interface.get("monitoringMode"),
        monitoring_enabled=interface.get("monitoringEnabled"),
        admin_status=interface.get("adminStatus"),
        oper_status=interface.get("operStatus"),
    )

    return {
        "_id": str(interface["_id"]),
        "deviceId": str(interface["deviceId"]),
        "hostname": interface.get("hostname"),
        "ipAddress": interface.get("ipAddress"),
        "name": interface.get("name"),
        "description": interface.get("description") or "",
        "adminStatus": interface.get("adminStatus") or "unknown",
        "operStatus": interface.get("operStatus") or "unknown",
        "mode": port_mode,
        "portMode": port_mode,
        "isAccess": bool(interface.get("isAccess", port_mode == "access")),
        "isTrunk": bool(interface.get("isTrunk", port_mode == "trunk")),
        "isUplink": bool(interface.get("isUplink", False)),
        "isInfrastructure": bool(interface.get("isInfrastructure", False)),
        "isManagement": bool(interface.get("isManagement", False)),
        "isProtected": bool(interface.get("isProtected", False)),
        # Preference mirror (AUTO ⇒ true). Additive effective fields below.
        "monitoringEnabled": monitoring["monitoringEnabled"],
        "monitoringMode": monitoring["monitoringMode"],
        "administratorDisabled": monitoring["administratorDisabled"],
        "effectiveMonitoring": monitoring["effectiveMonitoring"],
        "monitoringReason": monitoring["monitoringReason"],
        "accessVlan": interface.get("accessVlan"),
        "voiceVlan": interface.get("voiceVlan"),
        "nativeVlan": interface.get("nativeVlan"),
        "allowedVlans": list(interface.get("allowedVlans") or []),
        "vlan": interface.get("vlan") if interface.get("vlan") is not None else "",
        "speed": interface.get("speed") or "",
        "speedMbps": interface.get("speedMbps"),
        "duplex": interface.get("duplex") or "",
        "neighbor": neighbor,
        "ifIndex": interface.get("ifIndex"),
        "macAddress": interface.get("macAddress") or "",
        "vendor": interface.get("vendor") or "",
        "collectionMethod": interface.get("collectionMethod") or "ssh",
        "lastUpdated": format_datetime(interface.get("lastUpdated")),
        "createdAt": format_datetime(interface.get("createdAt")),
        "updatedAt": format_datetime(interface.get("updatedAt")),
    }


def _serialize_neighbor(neighbor) -> dict | None:
    if not neighbor or not isinstance(neighbor, dict):
        return None

    interface = (
        neighbor.get("interface")
        or neighbor.get("port")
        or ""
    )
    return {
        "hostname": neighbor.get("hostname") or "",
        "ip": neighbor.get("ip") or "",
        "platform": neighbor.get("platform") or "",
        "deviceType": (
            neighbor.get("deviceType")
            or neighbor.get("device_type")
            or "Unknown"
        ),
        "interface": interface,
        # Backward-compatible alias for older clients
        "port": interface,
        "protocol": neighbor.get("protocol") or "",
        "managementAddress": (
            neighbor.get("managementAddress")
            or neighbor.get("management_address")
            or neighbor.get("ip")
            or ""
        ),
        "systemDescription": (
            neighbor.get("systemDescription")
            or neighbor.get("system_description")
            or ""
        ),
        "capabilities": list(neighbor.get("capabilities") or []),
    }


def serialize_device(device):
    return {
        "_id": str(device["_id"]),
        "hostname": device.get("hostname"),
        "ipAddress": device.get("ipAddress"),
        "deviceType": get_device_type(device),
        "critical": device.get("critical", False),
        "monitor": device.get("monitor", True),
        "status": device.get("status", "Unknown"),
        "lastSeen": format_datetime(device.get("lastSeen")),
        "lastCheckedAt": format_datetime(device.get("lastCheckedAt")),
        "responseTime": device.get("responseTime"),
        "consecutiveFailures": device.get("consecutiveFailures", 0),
        "pingInterval": device.get("pingInterval"),
        "pingTimeoutMs": device.get("pingTimeoutMs"),
        "pingRetries": device.get("pingRetries"),
        "createdAt": format_datetime(device.get("createdAt")),
        "updatedAt": format_datetime(device.get("updatedAt")),
        # Optional auto-classification fields (absent on older documents).
        "vendor": device.get("vendor"),
        "operatingSystem": device.get("operatingSystem"),
        "classificationConfidence": device.get("classificationConfidence"),
        "classificationMethod": device.get("classificationMethod"),
        "discoverySource": device.get("discoverySource"),
        # Nmap metadata — present after the first successful scan, None before.
        "networkInfo": serialize_network_info(device.get("networkInfo")),
        # SSH credentials metadata (secrets never returned).
        "credentials": serialize_credentials(device.get("credentials")),
    }


def serialize_eligibility_result(result: dict, interface: dict | None = None) -> dict:
    """Serialise a document from the ``eligibility_results`` collection."""
    port_mode = None
    if interface:
        port_mode = (
            interface.get("portMode")
            or interface.get("mode")
            or "unknown"
        )

    payload = {
        "_id": str(result["_id"]) if result.get("_id") is not None else None,
        "deviceId": str(result["deviceId"]) if result.get("deviceId") is not None else None,
        "interface": result.get("interface"),
        "hostname": result.get("hostname") or (interface or {}).get("hostname"),
        "ipAddress": result.get("ipAddress") or (interface or {}).get("ipAddress"),
        "eligible": bool(result.get("eligible")),
        "reason": result.get("reason") or "",
        "failedRule": result.get("failedRule"),
        "confidence": int(result.get("confidence") or 100),
        "checks": result.get("checks") or {},
        "timestamp": format_datetime(result.get("timestamp")),
    }

    if interface is not None:
        from services.interface_collection.monitoring_state import (  # noqa: PLC0415
            compute_monitoring_view,
        )

        monitoring = compute_monitoring_view(
            monitoring_mode=interface.get("monitoringMode"),
            monitoring_enabled=interface.get("monitoringEnabled"),
            admin_status=interface.get("adminStatus"),
            oper_status=interface.get("operStatus"),
        )
        payload.update({
            "adminStatus": interface.get("adminStatus") or "unknown",
            "operStatus": interface.get("operStatus") or "unknown",
            "portMode": port_mode,
            "isAccess": bool(interface.get("isAccess", port_mode == "access")),
            "isTrunk": bool(interface.get("isTrunk", port_mode == "trunk")),
            "isUplink": bool(interface.get("isUplink", False)),
            "isInfrastructure": bool(interface.get("isInfrastructure", False)),
            "isManagement": bool(interface.get("isManagement", False)),
            "isProtected": bool(interface.get("isProtected", False)),
            "monitoringEnabled": monitoring["monitoringEnabled"],
            "monitoringMode": monitoring["monitoringMode"],
            "administratorDisabled": monitoring["administratorDisabled"],
            "effectiveMonitoring": monitoring["effectiveMonitoring"],
            "monitoringReason": monitoring["monitoringReason"],
        })

    return payload


def serialize_risk_result(result: dict) -> dict:
    """Serialise a document from the ``storm_risk_history`` collection."""
    raw = result.get("rawMetrics") or result.get("raw_metrics") or {}
    contributors = result.get("contributors") or []

    def _metric_value(name: str):
        entry = raw.get(name) or {}
        if isinstance(entry, dict) and "value" in entry:
            return entry.get("value")
        for item in contributors:
            if item.get("metric") == name:
                return item.get("value")
        return None

    return {
        "_id": str(result["_id"]) if result.get("_id") is not None else None,
        "deviceId": str(result["deviceId"]) if result.get("deviceId") is not None else None,
        "interface": result.get("interface"),
        "hostname": result.get("hostname"),
        "ipAddress": result.get("ipAddress"),
        "riskScore": float(result.get("riskScore") or 0),
        "severity": result.get("severity") or "LOW",
        "confidence": float(result.get("confidence") or 0),
        "contributors": contributors,
        "rawMetrics": raw,
        "eligible": bool(result.get("eligible", True)),
        "skippedReason": result.get("skippedReason"),
        "timestamp": format_datetime(result.get("timestamp")),
        # Flattened rates for the Storm Protection UI
        "broadcastRate": _metric_value("broadcast"),
        "multicastRate": _metric_value("multicast"),
        "unknownUnicastRate": _metric_value("unknown_unicast"),
        "utilization": _metric_value("utilization"),
        "errorRate": _metric_value("errors"),
        "discardRate": _metric_value("discards"),
        "crcRate": _metric_value("crc"),
        "sourceClassification": result.get("sourceClassification"),
        "sourceConfidence": float(result.get("sourceConfidence") or 0),
        "sourceRationale": result.get("sourceRationale"),
    }


def serialize_confirmation_result(result: dict) -> dict:
    """Serialise a document from the ``storm_confirmation_history`` collection."""
    consecutive = int(result.get("consecutiveHighSamples") or 0)
    required = int(result.get("requiredSamples") or 4)
    progress = 0.0
    if required > 0:
        progress = round(min(100.0, (consecutive / required) * 100.0), 2)

    return {
        "_id": str(result["_id"]) if result.get("_id") is not None else None,
        "deviceId": str(result["deviceId"]) if result.get("deviceId") is not None else None,
        "interface": result.get("interface"),
        "hostname": result.get("hostname"),
        "ipAddress": result.get("ipAddress"),
        "confirmed": bool(result.get("confirmed")),
        "state": result.get("state") or "NOT_CONFIRMED",
        "currentRisk": float(result.get("currentRisk") or 0),
        "highestRisk": float(result.get("highestRisk") or 0),
        "averageRisk": float(result.get("averageRisk") or 0),
        "consecutiveHighSamples": consecutive,
        "requiredSamples": required,
        "progress": progress,
        "reason": result.get("reason") or "",
        "reset": bool(result.get("reset", False)),
        "resetReason": result.get("resetReason"),
        "timestamp": format_datetime(result.get("timestamp")),
    }


def serialize_safety_result(result: dict) -> dict:
    """Serialise a document from the ``storm_safety_history`` collection."""
    return {
        "_id": str(result["_id"]) if result.get("_id") is not None else None,
        "deviceId": str(result["deviceId"]) if result.get("deviceId") is not None else None,
        "interface": result.get("interface"),
        "hostname": result.get("hostname"),
        "ipAddress": result.get("ipAddress"),
        "safe": bool(result.get("safe")),
        "reason": result.get("reason") or "",
        "failedRule": result.get("failedRule"),
        "confidence": float(result.get("confidence") or 0),
        "checks": result.get("checks") or {},
        "cooldownRemainingSeconds": int(result.get("cooldownRemainingSeconds") or 0),
        "mitigationAttempts": int(result.get("mitigationAttempts") or 0),
        "cpuPercent": result.get("cpuPercent"),
        "memoryPercent": result.get("memoryPercent"),
        "status": result.get("status") or ("SAFE" if result.get("safe") else "UNSAFE"),
        "sourceClassification": result.get("sourceClassification"),
        "sourceConfidence": result.get("sourceConfidence"),
        "sourceRationale": result.get("sourceRationale"),
        "timestamp": format_datetime(result.get("timestamp")),
    }


def serialize_incident(doc: dict) -> dict:
    """Serialise a document from the ``storm_incidents`` collection."""
    from services.storm.diagnostics.serializer import (  # noqa: PLC0415
        serialize_incident as _serialize,
    )

    return _serialize(doc)


def serialize_prepare_result(result: dict) -> dict:
    """Serialise an orchestrator.prepare() response."""
    from services.storm.diagnostics.serializer import (  # noqa: PLC0415
        serialize_prepare_result as _serialize,
    )

    return _serialize(result)

