"""
Apply classification results to device documents and enrich online hosts.

Used by nmap_service (manual / rescan / bulk) and discovery_service
(network range scan) without embedding classification rules in routes.
"""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from config.database import db
from models.device import create_device
from services.discovery.device_types import build_identification
from services.discovery.classifier import (
    ClassificationResult,
    classify_device,
    evidence_from_network_info,
    is_unknown_hostname,
    log_classification,
    DEVICE_TYPE_UNKNOWN,
)
from services.discovery.identification import (
    DEFAULT_IDENTIFICATION_MANAGER,
    IdentificationContext,
)
from services.discovery.enrichment import (
    DISCOVERY_STATUS_PENDING,
    enqueue_discovery_enrichment,
)
from services.discovery.identity_management import (
    apply_identity_fields_to_classification_update,
)
from services.discovery.ssh_hostname import (
    fetch_ssh_hostname,
    log_ssh_hostname_fallback,
    log_ssh_hostname_skip,
    nmap_hostname_from_network_info,
    should_attempt_ssh_hostname,
)
from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("discovery")


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
        "identification": build_identification(
            display_type=result.device_type,
            canonical_type=getattr(result, "canonical_type", None),
            method=(getattr(result, "identification_method", "") or result.classification_method),
            confidence=result.confidence,
            evidence=getattr(result, "identification_evidence", None),
        ),
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
        "updatedAt": utc_now(),
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
    update_fields["identification"] = build_identification(
        display_type=result.device_type,
        canonical_type=getattr(result, "canonical_type", None),
        method=(getattr(result, "identification_method", "") or result.classification_method),
        confidence=result.confidence,
        evidence=getattr(result, "identification_evidence", None),
    )
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


def identify_network_info(
    network_info: dict | None,
    *,
    ip_address: str,
    existing: dict | None = None,
    try_ssh: bool = True,
) -> tuple[ClassificationResult, Any]:
    """
    Route identification through the Phase 2 manager/orchestrator.

    Current live execution path still ends in the Nmap-backed classifier; future
    identifiers are plan-only stubs for now and must not trigger extra scans.
    """
    outcome = DEFAULT_IDENTIFICATION_MANAGER.identify(
        IdentificationContext(
            ip_address=ip_address,
            network_info=network_info,
            existing=existing,
            try_ssh=try_ssh,
            preferred_device_type=(existing or {}).get("deviceType"),
        )
    )
    if outcome.classification is not None:
        return outcome.classification, outcome.raw_evidence
    result, evidence = classify_network_info(
        network_info,
        ip_address=ip_address,
        existing=existing,
        try_ssh=try_ssh,
    )
    result.identification_method = outcome.method or "nmap"
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
        "discoveryStatus": device.get("discoveryStatus"),
        "identification": device.get("identification"),
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
        Insert minimal device immediately → queue background Nmap enrichment.

    Existing device
        Skip Nmap and classification. Update only reachability fields and
        return stored hostname / vendor / deviceType / classification metadata.

    Manual Nmap, scheduled Nmap, and device-detail scans are unchanged
    (they call ``scan_and_update_device``, not this path).
    """
    now = utc_now()

    # ── Already monitored: full monitoring write path (Phase 3) ────────────
    # Route through apply_ping_result so consecutiveFailures, lastCheckedAt,
    # history, and alert resolution stay consistent with scheduled monitoring.
    if existing is not None:
        from services.monitor_service import apply_ping_result  # noqa: PLC0415

        apply_ping_result(
            existing,
            ping_result,
            scan_type="Discovery",
        )
        logger.info(
            "[DISCOVERY] Existing device — monitoring fields synced via "
            "apply_ping_result | host=%s hostname=%s",
            ip_address,
            existing.get("hostname"),
        )
        refreshed = db.devices.find_one({"_id": existing["_id"]}) or existing
        return _discovery_result_payload(
            ip_address=ip_address,
            ping_result=ping_result,
            device=refreshed,
            saved=False,
        )

    # ── New device: insert immediately, enrich in background ───────────────
    device = create_device(
        hostname="Unknown",
        ip_address=ip_address,
        device_type=DEVICE_TYPE_UNKNOWN,
        critical=False,
        monitor=False,
    )
    device["status"] = "Online"
    device["responseTime"] = ping_result.get("responseTime")
    device["lastSeen"] = ping_result.get("lastSeen") or now
    device["lastCheckedAt"] = now
    device["consecutiveFailures"] = 0
    device["updatedAt"] = now
    device["discoveryStatus"] = DISCOVERY_STATUS_PENDING
    device["discoverySource"] = "discovery"

    try:
        insert_result = db.devices.insert_one(device)
        device["_id"] = insert_result.inserted_id
        saved = True
        logger.info(
            "[DISCOVERY] Device inserted | ip=%s | deviceId=%s | discoveryStatus=pending",
            ip_address,
            insert_result.inserted_id,
        )
        enqueue_discovery_enrichment(insert_result.inserted_id, ip_address)
    except DuplicateKeyError:
        logger.info(
            "[DEVICE DUPLICATE] ip=%s using existing inventory record",
            ip_address,
        )
        existing_doc = db.devices.find_one({"ipAddress": ip_address})
        if not existing_doc:
            raise
        from services.monitor_service import apply_ping_result  # noqa: PLC0415

        apply_ping_result(existing_doc, ping_result, scan_type="Discovery")
        device = db.devices.find_one({"_id": existing_doc["_id"]}) or existing_doc
        saved = False

    return _discovery_result_payload(
        ip_address=ip_address,
        ping_result=ping_result,
        device=device,
        saved=saved,
        nmap_error=None,
    )
