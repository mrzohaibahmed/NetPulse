"""
Apply classification results to device documents and enrich online hosts.

Used by nmap_service (manual / rescan / bulk) and discovery_service
(network range scan) without embedding classification rules in routes.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from config.database import MAX_SCAN_THREADS, db
from models.device import create_device
from services.discovery.classifier import (
    ClassificationResult,
    classify_device,
    evidence_from_network_info,
    is_unknown_hostname,
    log_classification,
)
from services.discovery.ssh_hostname import fetch_ssh_hostname
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("discovery")

# Bound concurrent Nmap work started from discovery sweeps.
_nmap_semaphore = threading.Semaphore(max(1, int(MAX_SCAN_THREADS or 5)))


def classification_fields(result: ClassificationResult) -> dict[str, Any]:
    """Map a ClassificationResult to optional MongoDB top-level fields."""
    return {
        "hostname": result.hostname,
        "vendor": result.vendor or None,
        "operatingSystem": result.operating_system or None,
        "deviceType": result.device_type,
        "classificationConfidence": int(result.confidence),
        "classificationMethod": result.classification_method,
        "discoverySource": result.discovery_source,
    }


def merge_hostname(existing: dict | None, detected: str) -> str:
    """Never overwrite an existing real hostname with Unknown."""
    current = (existing or {}).get("hostname") if existing else None
    if is_unknown_hostname(detected):
        if current and not is_unknown_hostname(current):
            return current
        return "Unknown"
    return detected


def apply_classification_to_device(
    device_id: ObjectId,
    result: ClassificationResult,
    *,
    network_info: dict | None = None,
    existing: dict | None = None,
) -> dict[str, Any]:
    """
    Persist classification onto the device document.

    Keeps all existing fields; only sets optional classification fields and
    optionally refreshes ``networkInfo``.
    """
    hostname = merge_hostname(existing, result.hostname)
    update_fields: dict[str, Any] = {
        "hostname": hostname,
        "deviceType": result.device_type,
        "classificationConfidence": int(result.confidence),
        "classificationMethod": result.classification_method,
        "discoverySource": result.discovery_source,
        "updatedAt": datetime.now(timezone.utc),
    }
    if result.vendor:
        update_fields["vendor"] = result.vendor
    if result.operating_system:
        update_fields["operatingSystem"] = result.operating_system
    if network_info is not None:
        update_fields["networkInfo"] = network_info

    db.devices.update_one({"_id": device_id}, {"$set": update_fields})
    return update_fields


def classify_network_info(
    network_info: dict | None,
    *,
    ip_address: str,
    existing: dict | None = None,
    try_ssh: bool = True,
) -> tuple[ClassificationResult, Any]:
    """
    Build evidence from nmap results (+ optional SSH hostname) and classify.
    """
    hostname_ssh = ""
    if try_ssh and existing is not None:
        hostname_ssh = fetch_ssh_hostname(existing)

    evidence = evidence_from_network_info(
        network_info,
        ip_address=ip_address,
        existing_hostname=(existing or {}).get("hostname") or "",
        hostname_ssh=hostname_ssh,
    )
    result = classify_device(evidence)
    # Honour hostname merge rule on the result object used for persistence.
    result.hostname = merge_hostname(existing, result.hostname)
    log_classification(logger, ip_address, evidence, result)
    return result, evidence


def enrich_online_host(
    ip_address: str,
    *,
    ping_result: dict,
    existing: dict | None = None,
) -> dict[str, Any]:
    """
    Nmap → classify → save/update for one online host (discovery path).

    Falls back to DNS-only / unknown classification when Nmap is unavailable.
    Never creates a duplicate device for an existing IP.
    """
    from services.discovery_service import get_hostname  # noqa: PLC0415
    from services.nmap_service import scan_device_nmap  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    network_info = None
    nmap_error = None

    with _nmap_semaphore:
        try:
            network_info = scan_device_nmap(ip_address)
        except Exception as exc:  # noqa: BLE001
            nmap_error = str(exc)
            logger.warning(
                "[DISCOVERY] Nmap skipped/failed | host=%s | %s",
                ip_address,
                exc,
            )

    # Seed PTR from OS reverse DNS when nmap did not return a hostname.
    dns_hostname = get_hostname(ip_address) or ""
    if network_info is None:
        network_info = {
            "hostname": dns_hostname,
            "macAddress": "",
            "vendor": "",
            "os": {"name": "", "family": "", "generation": "", "accuracy": ""},
            "deviceType": "",
            "ports": [],
            "services": [],
            "lastScan": now,
        }
    elif not (network_info.get("hostname") or "").strip() and dns_hostname:
        network_info = dict(network_info)
        network_info["hostname"] = dns_hostname

    result, _evidence = classify_network_info(
        network_info,
        ip_address=ip_address,
        existing=existing,
        try_ssh=True,
    )

    saved = False
    if existing is None:
        device = create_device(
            hostname=result.hostname if not is_unknown_hostname(result.hostname) else (
                dns_hostname or "Unknown"
            ),
            ip_address=ip_address,
            device_type=result.device_type,
            critical=False,
            monitor=True,
        )
        # Apply optional classification fields on create.
        device["status"] = "Online"
        device["responseTime"] = ping_result.get("responseTime")
        device["lastSeen"] = ping_result.get("lastSeen")
        device["updatedAt"] = now
        device["networkInfo"] = network_info
        device["vendor"] = result.vendor or None
        device["operatingSystem"] = result.operating_system or None
        device["classificationConfidence"] = int(result.confidence)
        device["classificationMethod"] = result.classification_method
        device["discoverySource"] = result.discovery_source
        if is_unknown_hostname(device["hostname"]):
            device["hostname"] = "Unknown"
        insert_result = db.devices.insert_one(device)
        device_id = insert_result.inserted_id
        saved = True
    else:
        device_id = existing["_id"]
        apply_classification_to_device(
            device_id,
            result,
            network_info=network_info,
            existing=existing,
        )
        db.devices.update_one(
            {"_id": device_id},
            {
                "$set": {
                    "status": "Online",
                    "responseTime": ping_result.get("responseTime"),
                    "lastSeen": ping_result.get("lastSeen"),
                    "updatedAt": now,
                }
            },
        )

    updated = db.devices.find_one({"_id": device_id}) or {}
    return {
        "hostname": updated.get("hostname"),
        "ipAddress": ip_address,
        "status": "Online",
        "responseTime": ping_result.get("responseTime"),
        "saved": saved,
        "deviceType": updated.get("deviceType"),
        "vendor": updated.get("vendor"),
        "operatingSystem": updated.get("operatingSystem"),
        "classificationConfidence": updated.get("classificationConfidence"),
        "classificationMethod": updated.get("classificationMethod"),
        "nmapError": nmap_error,
    }
