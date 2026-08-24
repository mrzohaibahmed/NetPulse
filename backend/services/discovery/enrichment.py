"""
Background Nmap enrichment for newly discovered devices.

Discovery inserts a minimal device document immediately after ICMP success,
then queues Nmap + classification here so HTTP responses are not blocked.
"""

from __future__ import annotations

import atexit
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument

from config.database import MAX_SCAN_THREADS, db
from services.discovery.classifier import is_unknown_hostname
from utils.monitor_logger import get_monitor_logger
from utils.utc import utc_now

logger = get_monitor_logger("discovery")

DISCOVERY_STATUS_PENDING = "pending"
DISCOVERY_STATUS_ENRICHING = "enriching"
DISCOVERY_STATUS_COMPLETED = "completed"
DISCOVERY_STATUS_FAILED = "failed"

_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()

# Shared with historical discovery sweep code — bounds concurrent Nmap subprocesses.
_nmap_semaphore = threading.Semaphore(max(1, int(MAX_SCAN_THREADS or 5)))


def _worker_count() -> int:
    return max(1, int(MAX_SCAN_THREADS or 5))


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=_worker_count(),
                thread_name_prefix="discovery-enrich",
            )
            logger.info(
                "[DISCOVERY] Enrichment executor started | workers=%s",
                _worker_count(),
            )
    return _executor


def shutdown_discovery_enrichment_executor() -> None:
    """Best-effort shutdown for tests and process exit."""
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=False, cancel_futures=True)
            _executor = None


atexit.register(shutdown_discovery_enrichment_executor)


def enqueue_discovery_enrichment(device_id: ObjectId, ip_address: str) -> None:
    """Schedule background Nmap enrichment for a newly inserted device."""
    logger.info(
        "[DISCOVERY] Enrichment queued | ip=%s | deviceId=%s",
        ip_address,
        device_id,
    )
    _get_executor().submit(_run_discovery_enrichment, device_id, ip_address)


def enqueue_batch_enrichment(items: list[tuple[ObjectId, str]]) -> None:
    """
    Schedule background enrichment for a list of (device_id, ip_address) tuples.

    Enables asynchronous multi-network discovery without blocking HTTP handlers or
    creating duplicate thread pools.
    """
    executor = _get_executor()
    for device_id, ip_address in items:
        logger.info(
            "[DISCOVERY] Batch enrichment queued | ip=%s | deviceId=%s",
            ip_address,
            device_id,
        )
        executor.submit(_run_discovery_enrichment, device_id, ip_address)


def _claim_enrichment(device_id: ObjectId) -> dict[str, Any] | None:
    """
    Atomically move pending/failed → enriching.

    Returns the device document when this worker should proceed, else None.
    """
    return db.devices.find_one_and_update(
        {
            "_id": device_id,
            "discoveryStatus": {
                "$in": [DISCOVERY_STATUS_PENDING, DISCOVERY_STATUS_FAILED],
            },
        },
        {
            "$set": {
                "discoveryStatus": DISCOVERY_STATUS_ENRICHING,
                "updatedAt": utc_now(),
            },
            "$unset": {"discoveryEnrichmentError": ""},
        },
        return_document=ReturnDocument.AFTER,
    )


def _mark_enrichment_failed(
    device_id: ObjectId,
    ip_address: str,
    error_message: str,
) -> None:
    db.devices.update_one(
        {"_id": device_id},
        {
            "$set": {
                "discoveryStatus": DISCOVERY_STATUS_FAILED,
                "discoveryEnrichmentError": error_message[:2000],
                "updatedAt": utc_now(),
            }
        },
    )
    logger.warning(
        "[DISCOVERY] Enrichment failed | ip=%s | deviceId=%s | error=%s",
        ip_address,
        device_id,
        error_message,
    )


def _run_discovery_enrichment(device_id: ObjectId, ip_address: str) -> None:
    """
    Background worker: Nmap quick scan → hostname → classify → persist.

    Never raises — failures are recorded on the device document.
    """
    try:
        device = _claim_enrichment(device_id)
        if device is None:
            logger.info(
                "[DISCOVERY] Enrichment skipped (already running or complete) | "
                "ip=%s | deviceId=%s",
                ip_address,
                device_id,
            )
            return

        logger.info(
            "[DISCOVERY] Enrichment started | ip=%s | deviceId=%s",
            ip_address,
            device_id,
        )
        nmap_started = time.monotonic()
        network_info = None
        nmap_error: str | None = None

        with _nmap_semaphore:
            from services.nmap_service import scan_device_nmap  # noqa: PLC0415

            try:
                network_info = scan_device_nmap(ip_address, profile="quick")
            except Exception as exc:  # noqa: BLE001
                nmap_error = str(exc)
                logger.warning(
                    "[DISCOVERY] Nmap failed during enrichment | ip=%s | %s",
                    ip_address,
                    exc,
                )

        nmap_elapsed = round(time.monotonic() - nmap_started, 2)
        logger.info(
            "[DISCOVERY] Nmap finished | ip=%s | elapsed=%ss | success=%s",
            ip_address,
            nmap_elapsed,
            network_info is not None,
        )

        from services.discovery_service import get_hostname  # noqa: PLC0415

        now = utc_now()
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

        from services.discovery.apply import (  # noqa: PLC0415
            apply_classification_to_device,
            identify_network_info,
        )

        # Refresh device — may have been updated since insert.
        existing = db.devices.find_one({"_id": device_id}) or device

        result, _evidence = identify_network_info(
            network_info,
            ip_address=ip_address,
            existing=existing,
            try_ssh=False,
        )

        hostname = result.hostname
        if is_unknown_hostname(hostname):
            hostname = merge_pending_hostname(existing, dns_hostname)

        final_status = (
            DISCOVERY_STATUS_FAILED if nmap_error else DISCOVERY_STATUS_COMPLETED
        )

        apply_classification_to_device(
            device_id,
            result,
            network_info=network_info,
            existing=existing,
        )

        update_fields: dict[str, Any] = {
            "discoveryStatus": final_status,
            "updatedAt": utc_now(),
        }
        if not is_unknown_hostname(hostname):
            update_fields["hostname"] = hostname
        if nmap_error:
            update_fields["discoveryEnrichmentError"] = nmap_error[:2000]

        update_op: dict[str, Any] = {"$set": update_fields}
        if not nmap_error:
            update_op["$unset"] = {"discoveryEnrichmentError": ""}

        db.devices.update_one({"_id": device_id}, update_op)

        if final_status == DISCOVERY_STATUS_FAILED:
            logger.warning(
                "[DISCOVERY] Enrichment finished with Nmap failure | ip=%s | "
                "deviceId=%s | nmapElapsed=%ss | error=%s",
                ip_address,
                device_id,
                nmap_elapsed,
                nmap_error,
            )
        else:
            logger.info(
                "[DISCOVERY] Enrichment completed | ip=%s | deviceId=%s | "
                "deviceType=%s | nmapElapsed=%ss",
                ip_address,
                device_id,
                result.device_type,
                nmap_elapsed,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[DISCOVERY] Enrichment worker error | ip=%s | deviceId=%s",
            ip_address,
            device_id,
        )
        try:
            _mark_enrichment_failed(device_id, ip_address, str(exc))
        except Exception as mark_exc:  # noqa: BLE001
            logger.error(
                "[DISCOVERY] Failed to mark enrichment failed | ip=%s | %s",
                ip_address,
                mark_exc,
            )


def merge_pending_hostname(existing: dict | None, dns_hostname: str) -> str:
    """Prefer an existing real hostname, then DNS, else Unknown."""
    current = (existing or {}).get("hostname") or ""
    if current and not is_unknown_hostname(current):
        return current
    if dns_hostname and not is_unknown_hostname(dns_hostname):
        return dns_hostname
    return "Unknown"
