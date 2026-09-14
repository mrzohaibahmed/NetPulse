"""Switch hardware collection orchestrator."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from bson import ObjectId

from config.database import db
from services.interface_collection.snmp import SNMPCollectorError, snmp_available
from services.switch_hardware import config as hw_config
from services.switch_hardware.alerts import evaluate_hardware_alerts
from services.switch_hardware.events import detect_hardware_events
from services.switch_hardware.health_evaluator import evaluate_snapshot
from services.switch_hardware.models import (
    build_current_document,
    build_history_document,
    empty_cpu,
    empty_fans,
    empty_inventory,
    empty_memory,
    empty_power_supplies,
    empty_temperature,
)
from services.switch_hardware.snmp_collector import collect_snmp_hardware
from services.switch_hardware.ssh_collector import collect_ssh_hardware
from services.switch_hardware.vendor_detection import (
    is_eligible_switch,
    platform_label,
)
from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("switch_hardware.collector")

_device_locks: dict[str, threading.Lock] = {}
_lock_guard = threading.Lock()
_inflight: set[str] = set()


def _device_lock(device_id: str) -> threading.Lock:
    with _lock_guard:
        if device_id not in _device_locks:
            _device_locks[device_id] = threading.Lock()
        return _device_locks[device_id]


def _merge_dict(base: dict, incoming: dict) -> dict:
    result = dict(base)
    for key, value in incoming.items():
        if value is None:
            continue
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        elif isinstance(value, list) and value:
            result[key] = value
        elif value not in ("", [], {}):
            result[key] = value
    return result


def _merge_component_list(existing: dict, incoming: dict, list_key: str = "items") -> dict:
    merged = dict(existing or {})
    incoming_items = incoming.get(list_key) or []
    if not incoming_items:
        return merged if merged.get(list_key) else incoming
    if not merged.get(list_key):
        return incoming
    by_name = {i.get("name"): i for i in merged.get(list_key) or []}
    for item in incoming_items:
        name = item.get("name")
        if name and name in by_name:
            by_name[name] = _merge_dict(by_name[name], item)
        elif name:
            by_name[name] = item
    merged[list_key] = list(by_name.values())
    merged["count"] = len(merged[list_key])
    return merged


def _merge_hardware(snmp_data: dict | None, ssh_data: dict | None) -> dict[str, Any]:
    snapshot = {
        "inventory": empty_inventory(),
        "temperature": empty_temperature(),
        "fans": empty_fans(),
        "powerSupplies": empty_power_supplies(),
        "cpu": empty_cpu(),
        "memory": empty_memory(),
        "hardwareAlarms": [],
        "availability": {"snmp": "unknown", "ssh": "unknown"},
        "evidence": {"logEvidence": []},
    }

    if snmp_data:
        snapshot["inventory"] = _merge_dict(snapshot["inventory"], snmp_data.get("inventory") or {})
        snapshot["temperature"] = _merge_component_list(
            snapshot["temperature"], snmp_data.get("temperature") or {}, "sensors"
        )
        snapshot["fans"] = _merge_component_list(snapshot["fans"], snmp_data.get("fans") or {})
        snapshot["powerSupplies"] = _merge_component_list(
            snapshot["powerSupplies"], snmp_data.get("powerSupplies") or {}
        )
        snapshot["availability"]["snmp"] = (snmp_data.get("availability") or {}).get("snmp", "available")

    if ssh_data:
        parsed = ssh_data.get("parsed") or {}
        snapshot["inventory"] = _merge_dict(snapshot["inventory"], parsed.get("inventory") or {})
        snapshot["temperature"] = _merge_component_list(
            snapshot["temperature"], parsed.get("temperature") or {}, "sensors"
        )
        snapshot["fans"] = _merge_component_list(snapshot["fans"], parsed.get("fans") or {})
        snapshot["powerSupplies"] = _merge_component_list(
            snapshot["powerSupplies"], parsed.get("powerSupplies") or {}
        )
        snapshot["cpu"] = _merge_dict(snapshot["cpu"], parsed.get("cpu") or {})
        snapshot["memory"] = _merge_dict(snapshot["memory"], parsed.get("memory") or {})
        alarms = parsed.get("alarms") or []
        if alarms:
            snapshot["hardwareAlarms"] = alarms
        logs = parsed.get("logEvidence") or []
        if logs:
            snapshot["evidence"]["logEvidence"] = logs
        snapshot["availability"]["ssh"] = (ssh_data.get("availability") or {}).get("ssh", "available")

    return snapshot


def collect_device_hardware(
    device_id,
    *,
    mode: str = "full",
    source_label: str = "scheduled",
) -> dict[str, Any]:
    if not hw_config.is_hardware_monitoring_enabled():
        return {"success": False, "reason": "disabled"}

    if isinstance(device_id, str):
        if not ObjectId.is_valid(device_id):
            raise ValueError("Invalid device id")
        device_id = ObjectId(device_id)

    device = db.devices.find_one({"_id": device_id})
    if not device:
        raise ValueError("Device not found")
    if not is_eligible_switch(device):
        raise ValueError("Device is not an eligible Cisco switch")

    key = str(device_id)
    if key in _inflight:
        return {"success": False, "reason": "in_flight"}

    lock = _device_lock(key)
    if not lock.acquire(blocking=False):
        return {"success": False, "reason": "in_flight"}

    _inflight.add(key)
    timeout = hw_config.collection_timeout_seconds()
    include_inventory = mode in ("full", "inventory")

    try:
        snmp_data = None
        ssh_data = None
        errors: list[str] = []

        if hw_config.is_snmp_enabled() and snmp_available() and mode in ("full", "snmp", "poll"):
            try:
                snmp_data = collect_snmp_hardware(device, timeout=timeout)
            except SNMPCollectorError as exc:
                errors.append(f"SNMP: {exc}")
                logger.info("SNMP hardware unavailable | device=%s | %s", device_id, exc)

        if hw_config.is_ssh_enabled() and mode in ("full", "ssh", "inventory", "poll"):
            try:
                ssh_data = collect_ssh_hardware(
                    device,
                    include_inventory=include_inventory,
                    timeout=timeout,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"SSH: {exc}")
                logger.info("SSH hardware unavailable | device=%s | %s", device_id, exc)

        merged = _merge_hardware(snmp_data, ssh_data)
        merged = evaluate_snapshot(merged)

        platform = None
        if ssh_data:
            platform = ssh_data.get("platform")
        if not platform and snmp_data:
            from services.switch_hardware.vendor_detection import detect_platform

            platform = detect_platform(device, sys_descr=snmp_data.get("sysDescr"))

        previous = db.switch_hardware_current.find_one({"deviceId": device_id})
        collection_status = "success"
        if errors and (snmp_data or ssh_data):
            collection_status = "partial"
        elif errors:
            collection_status = "failed"

        now = utc_now()
        doc = build_current_document(
            device_id,
            platform=platform_label(platform),
            collection_status=collection_status,
            overall_health=merged.get("overallHealth", "unknown"),
            inventory=merged.get("inventory"),
            temperature=merged.get("temperature"),
            fans=merged.get("fans"),
            powerSupplies=merged.get("powerSupplies"),
            cpu=merged.get("cpu"),
            memory=merged.get("memory"),
            alarms=merged.get("hardwareAlarms"),
            availability=merged.get("availability"),
            evidence=merged.get("evidence"),
            last_error="; ".join(errors) if errors else None,
            last_successful_collection_at=now if collection_status != "failed" else (previous or {}).get(
                "lastSuccessfulCollectionAt"
            ),
            last_attempted_collection_at=now,
        )

        db.switch_hardware_current.update_one(
            {"deviceId": device_id},
            {"$set": doc},
            upsert=True,
        )

        if collection_status != "failed":
            history = build_history_document(
                device_id,
                doc,
                source=source_label,
            )
            db.switch_hardware_history.insert_one(history)

        prev_snapshot = previous or {}
        detect_hardware_events(
            device_id,
            prev_snapshot,
            doc,
            source=source_label,
        )
        evaluate_hardware_alerts(device, doc)

        return {"success": collection_status != "failed", "document": doc, "errors": errors}
    finally:
        _inflight.discard(key)
        lock.release()


def collect_all_switch_hardware(*, mode: str = "poll") -> dict[str, Any]:
    if not hw_config.is_hardware_monitoring_enabled():
        return {"processed": 0, "skipped": "disabled"}

    query = {"monitor": True}
    devices = list(db.devices.find(query))
    eligible = [d for d in devices if is_eligible_switch(d)]

    workers = hw_config.max_workers()
    results = {"processed": 0, "success": 0, "failed": 0}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(collect_device_hardware, d["_id"], mode=mode): d["_id"]
            for d in eligible
        }
        for future in as_completed(futures):
            results["processed"] += 1
            try:
                outcome = future.result()
                if outcome.get("success"):
                    results["success"] += 1
                else:
                    results["failed"] += 1
            except Exception as exc:  # noqa: BLE001
                results["failed"] += 1
                logger.warning("Hardware collection worker failed | %s", exc)

    logger.info(
        "Hardware collection cycle complete | mode=%s | processed=%s success=%s failed=%s",
        mode,
        results["processed"],
        results["success"],
        results["failed"],
    )
    return results


def get_current_hardware(device_id) -> dict | None:
    if isinstance(device_id, str) and ObjectId.is_valid(device_id):
        device_id = ObjectId(device_id)
    return db.switch_hardware_current.find_one({"deviceId": device_id})


def serialize_hardware_document(doc: dict | None) -> dict | None:
    if not doc:
        return None
    out = dict(doc)
    out.pop("_id", None)
    if out.get("deviceId") is not None:
        out["deviceId"] = str(out["deviceId"])
    return out
