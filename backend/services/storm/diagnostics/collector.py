"""
Diagnostics Capture orchestrator.

Collects immutable pre-mitigation evidence from MongoDB + read-only SSH.
Never executes configuration commands.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId

from services.storm.diagnostics.snapshots import (
    parse_device_health,
    parse_interface_snapshot,
    parse_mac_table,
    parse_switchport_snapshot,
)
from services.storm.diagnostics.ssh_capture import capture_show_outputs
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("storm.diagnostics")


def _db():
    from config.database import db  # noqa: PLC0415

    return db


def _oid(device_id):
    if isinstance(device_id, ObjectId):
        return device_id
    if isinstance(device_id, str) and ObjectId.is_valid(device_id):
        return ObjectId(device_id)
    return device_id


def _latest(collection: str, device_id, interface: str) -> Optional[dict]:
    return _db()[collection].find_one(
        {"deviceId": _oid(device_id), "interface": interface},
        sort=[("timestamp", -1)],
    )


def _latest_stats(device_id, interface: str) -> Optional[dict]:
    return _db().interface_stats.find_one(
        {"deviceId": _oid(device_id), "interfaceName": interface},
        sort=[("timestamp", -1)],
    )


def _load_device(device_id) -> Optional[dict]:
    return _db().devices.find_one({"_id": _oid(device_id)})


def _load_interface(device_id, interface: str) -> Optional[dict]:
    return _db().interfaces.find_one(
        {"deviceId": _oid(device_id), "name": interface}
    )


def _statistics_bundle(
    latest_stat: Optional[dict],
    risk: Optional[dict],
) -> dict[str, Any]:
    raw = (risk or {}).get("rawMetrics") or {}
    contributors = (risk or {}).get("contributors") or []

    def from_raw(metric: str):
        entry = raw.get(metric) or {}
        if isinstance(entry, dict) and "value" in entry:
            return entry.get("value")
        for item in contributors:
            if item.get("metric") == metric:
                return item.get("value")
        return None

    return {
        "broadcastRate": from_raw("broadcast"),
        "multicastRate": from_raw("multicast"),
        "unknownUnicastRate": from_raw("unknown_unicast"),
        "utilization": from_raw("utilization")
        if from_raw("utilization") is not None
        else (latest_stat or {}).get("utilization"),
        "errorRate": from_raw("errors"),
        "crcRate": from_raw("crc"),
        "discardRate": from_raw("discards"),
        "latestSample": {
            "broadcastPackets": (latest_stat or {}).get("broadcastPackets"),
            "multicastPackets": (latest_stat or {}).get("multicastPackets"),
            "rxBroadcastPackets": (latest_stat or {}).get("rxBroadcastPackets"),
            "txBroadcastPackets": (latest_stat or {}).get("txBroadcastPackets"),
            "rxMulticastPackets": (latest_stat or {}).get("rxMulticastPackets"),
            "txMulticastPackets": (latest_stat or {}).get("txMulticastPackets"),
            "inputErrors": (latest_stat or {}).get("inputErrors"),
            "outputErrors": (latest_stat or {}).get("outputErrors"),
            "discards": (latest_stat or {}).get("discards"),
            "rxDiscards": (latest_stat or {}).get("rxDiscards"),
            "txDiscards": (latest_stat or {}).get("txDiscards"),
            "utilization": (latest_stat or {}).get("utilization"),
            "timestamp": (latest_stat or {}).get("timestamp"),
            "collectionMethod": (latest_stat or {}).get("collectionMethod"),
        }
        if latest_stat
        else None,
    }


def capture_diagnostics(
    device_id,
    interface: str,
    *,
    probe_ssh: bool = True,
    ssh_bundle: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Capture a full diagnostics package for one interface.

    Soft-fails individual sections so evidence is still stored when partial.
    """
    logger.info("Diagnostics Started | %s", interface)
    now = datetime.now(timezone.utc)
    name = str(interface).strip()
    device = _load_device(device_id)
    iface = _load_interface(device_id, name)

    eligibility = _latest("eligibility_results", device_id, name)
    risk = _latest("storm_risk_history", device_id, name)
    confirmation = _latest("storm_confirmation_history", device_id, name)
    safety = _latest("storm_safety_history", device_id, name)
    latest_stat = _latest_stats(device_id, name)

    ssh_result = ssh_bundle
    if ssh_result is None and probe_ssh and device is not None:
        ssh_result = capture_show_outputs(device, name)
    ssh_result = ssh_result or {
        "success": False,
        "outputs": {},
        "errors": {"skipped": "SSH probe skipped or device missing"},
        "vendor": None,
    }

    outputs = ssh_result.get("outputs") or {}
    interface_snap = parse_interface_snapshot(outputs.get("interface"), name)
    # Prefer Mongo inventory if SSH parse incomplete
    if iface and not interface_snap.get("available"):
        interface_snap = {
            "interface": name,
            "available": True,
            "rawPresent": False,
            "source": "mongodb",
            "adminStatus": iface.get("adminStatus"),
            "operStatus": iface.get("operStatus"),
            "speed": iface.get("speed"),
            "speedKbps": None,
            "duplex": iface.get("duplex"),
            "mtu": None,
            "lastInput": None,
            "lastOutput": None,
            "errors": {
                "input": (latest_stat or {}).get("inputErrors"),
                "output": (latest_stat or {}).get("outputErrors"),
            },
            "crc": None,
            "discards": {"input": None, "output": None, "other": (latest_stat or {}).get("discards")},
        }

    switchport = parse_switchport_snapshot(outputs.get("switchport"), name)
    if iface and not switchport.get("available"):
        switchport = {
            "interface": name,
            "available": True,
            "supported": True,
            "rawPresent": False,
            "source": "mongodb",
            "mode": iface.get("portMode") or iface.get("mode"),
            "accessVlan": iface.get("accessVlan"),
            "voiceVlan": iface.get("voiceVlan"),
            "nativeVlan": iface.get("nativeVlan"),
            "allowedVlans": iface.get("allowedVlans") or [],
        }

    mac_table = parse_mac_table(outputs.get("mac"), name)

    mongo_health = {}
    if device:
        storm = device.get("storm") or {}
        health = device.get("health") or storm.get("health") or {}
        mongo_health = {
            "cpuPercent": health.get("cpuPercent", storm.get("cpuPercent")),
            "memoryPercent": health.get("memoryPercent", storm.get("memoryPercent")),
            "uptime": health.get("uptime", storm.get("uptime")),
        }
    if safety:
        if mongo_health.get("cpuPercent") is None:
            mongo_health["cpuPercent"] = safety.get("cpuPercent")
        if mongo_health.get("memoryPercent") is None:
            mongo_health["memoryPercent"] = safety.get("memoryPercent")

    device_health = parse_device_health(
        outputs.get("cpu"),
        outputs.get("uptime"),
        mongo_health=mongo_health,
    )

    neighbor = None
    if iface and isinstance(iface.get("neighbor"), dict):
        n = iface["neighbor"]
        neighbor = {
            "hostname": n.get("hostname"),
            "platform": n.get("platform"),
            "managementAddress": n.get("managementAddress") or n.get("ip"),
            "deviceType": n.get("deviceType"),
            "protocol": n.get("protocol"),
            "interface": n.get("interface") or n.get("port"),
        }

    package = {
        "capturedAt": now,
        "deviceId": str(device_id),
        "interface": name,
        "hostname": (device or {}).get("hostname") or (iface or {}).get("hostname"),
        "ipAddress": (device or {}).get("ipAddress") or (iface or {}).get("ipAddress"),
        "interfaceSnapshot": interface_snap,
        "switchportSnapshot": switchport,
        "macTable": mac_table,
        "statistics": _statistics_bundle(latest_stat, risk),
        "neighbor": neighbor,
        "deviceHealth": device_health,
        "eligibility": eligibility,
        "risk": risk,
        "confirmation": confirmation,
        "safety": safety,
        "diagnosticsMeta": {
            "sshSuccess": bool(ssh_result.get("success")),
            "sshErrors": ssh_result.get("errors") or {},
            "vendor": ssh_result.get("vendor"),
            "probeSsh": probe_ssh,
        },
    }

    logger.info(
        "Diagnostics Complete | %s | ssh=%s | mac=%s | switchport=%s",
        name,
        package["diagnosticsMeta"]["sshSuccess"],
        mac_table.get("macCount", 0),
        switchport.get("available"),
    )
    return package
