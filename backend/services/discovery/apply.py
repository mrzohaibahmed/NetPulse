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
from pymongo.errors import DuplicateKeyError

from config.database import MAX_SCAN_THREADS, db
from models.device import create_device
from services.discovery.classifier import (
    ClassificationResult,
    classify_device,
    evidence_from_network_info,
    is_unknown_hostname,
    log_classification,
)
from services.discovery.identity_management import (
    apply_identity_fields_to_classification_update,
    ownership_for_device_edit,
)
from services.discovery.ssh_hostname import (
    fetch_ssh_hostname,
    log_ssh_hostname_fallback,
    log_ssh_hostname_skip,
    nmap_hostname_from_network_info,
    should_attempt_ssh_hostname,
)
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
    ip_address = (existing or {}).get("ipAddress", "unknown")
    device_label = ip_address
    if existing:
        hostname_label = existing.get("hostname")
        if hostname_label and str(hostname_label).strip():
            device_label = f"{hostname_label}/{ip_address}"

    update_fields: dict[str, Any] = {
        "classificationConfidence": int(result.confidence),
        "classificationMethod": result.classification_method,
        "discoverySource": result.discovery_source,
        "updatedAt": datetime.now(timezone.utc),
    }
    apply_identity_fields_to_classification_update(
        existing,
        update_fields,
        detected_hostname=hostname,
        detected_device_type=result.device_type,
        device_label=device_label,
    )
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
    info = network_info
    hostname_ssh = ""
    reverse_dns_hostname = ""

    if try_ssh and existing is not None:
        ip = ip_address or existing.get("ipAddress") or ""
        nmap_ptr, nmap_service = nmap_hostname_from_network_info(network_info)

        if not nmap_ptr and not nmap_service:
            from services.discovery_service import get_hostname  # noqa: PLC0415

            reverse_dns_hostname = get_hostname(ip) or ""
            if reverse_dns_hostname and not is_unknown_hostname(reverse_dns_hostname):
                info = dict(network_info or {})
                if not (info.get("hostname") or "").strip():
                    info["hostname"] = reverse_dns_hostname

        if should_attempt_ssh_hostname(
            existing,
            network_info,
            ip_address=ip,
            reverse_dns_hostname=reverse_dns_hostname,
        ):
            log_ssh_hostname_fallback(existing, ip_address=ip)
            hostname_ssh = fetch_ssh_hostname(existing)
        elif (
            nmap_ptr
            or nmap_service
            or (
                reverse_dns_hostname
                and not is_unknown_hostname(reverse_dns_hostname)
            )
            or (
                (existing.get("hostname") or "")
                and not is_unknown_hostname(existing.get("hostname"))
            )
        ):
            log_ssh_hostname_skip(existing, ip_address=ip)

    evidence = evidence_from_network_info(
        info,
        ip_address=ip_address,
        existing_hostname=(existing or {}).get("hostname") or "",
        hostname_ssh=hostname_ssh,
    )
    result = classify_device(evidence)
    # Honour hostname merge rule on the result object used for persistence.
    result.hostname = merge_hostname(existing, result.hostname)
    log_classification(logger, ip_address, evidence, result)
    return result, evidence


def _discovery_result_payload(
    *,
    ip_address: str,
    ping_result: dict,
    device: dict,
    saved: bool,
    nmap_error: str | None = None,
) -> dict[str, Any]:
    """Build the discovery API row from a stored (or just-inserted) device."""
    return {
        "hostname": device.get("hostname"),
        "ipAddress": ip_address,
        "status": "Online",
        "responseTime": ping_result.get("responseTime"),
        "saved": saved,
        "deviceType": device.get("deviceType"),
        "vendor": device.get("vendor"),
        "operatingSystem": device.get("operatingSystem"),
        "classificationConfidence": device.get("classificationConfidence"),
        "classificationMethod": device.get("classificationMethod"),
        "nmapError": nmap_error,
    }


def enrich_online_host(
    ip_address: str,
    *,
    ping_result: dict,
    existing: dict | None = None,
) -> dict[str, Any]:
    """
    Discovery enrichment for one online host.

    New device
        Nmap → classify → insert (full pipeline).

    Existing device
        Skip Nmap and classification. Update only reachability fields and
        return stored hostname / vendor / deviceType / classification metadata.

    Manual Nmap, scheduled Nmap, and device-detail scans are unchanged
    (they call ``scan_and_update_device``, not this path).
    """
    now = datetime.now(timezone.utc)

    # ── Already monitored: ping-only update (no Nmap / no classification) ──
    if existing is not None:
        db.devices.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "status": "Online",
                    "responseTime": ping_result.get("responseTime"),
                    "lastSeen": ping_result.get("lastSeen"),
                    "updatedAt": now,
                }
            },
        )
        logger.info(
            "[DISCOVERY] Existing device — skipped Nmap | host=%s hostname=%s",
            ip_address,
            existing.get("hostname"),
        )
        return _discovery_result_payload(
            ip_address=ip_address,
            ping_result=ping_result,
            device=existing,
            saved=False,
        )

    # ── New device: full Nmap → classify → insert ─────────────────────────
    from services.discovery_service import get_hostname  # noqa: PLC0415
    from services.nmap_service import scan_device_nmap  # noqa: PLC0415

    network_info = None
    nmap_error = None

    with _nmap_semaphore:
        try:
            network_info = scan_device_nmap(ip_address, profile="quick")
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
        existing=None,
        try_ssh=False,
    )

    device = create_device(
        hostname=result.hostname if not is_unknown_hostname(result.hostname) else (
            dns_hostname or "Unknown"
        ),
        ip_address=ip_address,
        device_type=result.device_type,
        critical=False,
        monitor=True,
    )
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

    try:
        insert_result = db.devices.insert_one(device)
        device["_id"] = insert_result.inserted_id
        saved = True
    except DuplicateKeyError:
        logger.info(
            "[DEVICE DUPLICATE] ip=%s using existing inventory record",
            ip_address,
        )
        existing_doc = db.devices.find_one({"ipAddress": ip_address})
        if not existing_doc:
            raise
        device = existing_doc
        saved = False

    return _discovery_result_payload(
        ip_address=ip_address,
        ping_result=ping_result,
        device=device,
        saved=saved,
        nmap_error=nmap_error,
    )
