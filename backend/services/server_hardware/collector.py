"""
iLO Server Hardware Polling Collector Service (Phase 3D).

Periodically collects normalized server hardware telemetry from eligible HPE iLO devices.
Integrates Phase 3A RedfishClient, Phase 3B normalizer facade, and Phase 3C MongoDB persistence.
Does NOT calculate aggregate health, manage outage alerts, or perform OS ping monitoring.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from config.database import db
from services.scheduler_ownership import (
    CycleLeadershipGuard,
    require_scheduler_leadership,
)
from services.server_hardware.collection_state import (
    CollectionStateError,
    record_ilo_poll_attempt,
    record_ilo_poll_failure,
    record_ilo_poll_success,
)
from services.server_hardware.models import ServerHardware
from services.server_hardware.persistence import (
    append_server_hardware_history,
    get_server_hardware_history,
    save_current_server_hardware,
)
from services.server_hardware import normalize_server_hardware
from utils.monitor_logger import get_monitor_logger
from utils.redfish_client import (
    RedfishAuthenticationError,
    RedfishClient,
    RedfishConnectionError,
    RedfishError,
    RedfishTLSVerificationError,
    RedfishTimeoutError,
)
from utils.secret_crypto import decrypt_secret
from utils.utc import utc_now

logger = get_monitor_logger("server_hardware.collector")

JOB_ID = "ilo_hardware_poll_job"
ELIGIBLE_DEVICE_TYPES = ["Server", "Linux Server", "ESXi Server"]
HISTORY_CADENCE_SECONDS = 600.0  # 10 minutes
MAX_CONCURRENT_DEVICES = 5


def _classify_redfish_exception(exc: Exception) -> str:
    """Map Redfish client and network exceptions to Step 1 collection status categories."""
    if isinstance(exc, RedfishAuthenticationError):
        return "AUTHENTICATION_ERROR"
    if isinstance(exc, RedfishTimeoutError):
        return "TIMEOUT"
    if isinstance(exc, RedfishTLSVerificationError):
        return "TLS_ERROR"
    if isinstance(exc, RedfishConnectionError):
        return "CONNECTION_ERROR"
    if isinstance(exc, RedfishError):
        return "REDFISH_ERROR"
    return "UNKNOWN_ERROR"


def _safe_record_attempt(device_id: Any, attempt_at: datetime, database: Any) -> None:
    try:
        record_ilo_poll_attempt(device_id, attempt_at, database=database)
    except CollectionStateError as exc:
        logger.error("[ILO_COLLECTOR] Failed to record poll attempt | deviceId=%s | %s", device_id, exc)


def _safe_record_failure(device_id: Any, attempt_at: datetime, error: Exception | str, status: str, database: Any) -> None:
    try:
        record_ilo_poll_failure(device_id, attempt_at, error, status=status, database=database)
    except CollectionStateError as exc:
        logger.error("[ILO_COLLECTOR] Failed to record poll failure | deviceId=%s | %s", device_id, exc)


def _safe_record_success(device_id: Any, attempt_at: datetime, database: Any) -> None:
    try:
        record_ilo_poll_success(device_id, attempt_at, success_at=utc_now(), database=database)
    except CollectionStateError as exc:
        logger.error("[ILO_COLLECTOR] Failed to record poll success | deviceId=%s | %s", device_id, exc)


def _resolve_collection_members(client: RedfishClient, data: Any) -> Any:
    """Resolve collection member URIs into expanded member dicts where needed."""
    if not isinstance(data, dict) or "Members" not in data or not isinstance(data["Members"], list):
        return data

    resolved: list[Any] = []
    for item in data["Members"]:
        if isinstance(item, dict):
            if "@odata.id" in item and len(item) == 1:
                try:
                    full_item = client.get(item["@odata.id"])
                    if isinstance(full_item, dict):
                        resolved.append(full_item)
                except Exception:  # noqa: BLE001
                    pass
            else:
                resolved.append(item)
    return {"Members": resolved}


def poll_single_device_ilo_hardware(
    device: dict[str, Any],
    *,
    database: Any = None,
) -> bool:
    """
    Execute iLO hardware collection, normalization, and persistence for a single device.

    Returns True if current snapshot was successfully saved, False otherwise.
    Isolated per-device failure handling prevents one server failure from aborting others.
    """
    device_id = device.get("_id")
    hostname = device.get("hostname") or str(device_id)
    ilo_address = device.get("iloAddress")

    if not ilo_address or not isinstance(ilo_address, str) or not ilo_address.strip():
        logger.info("[ILO_COLLECTOR] Skipping device with missing or empty iloAddress | deviceId=%s", device_id)
        return False

    credentials = device.get("credentials") or {}
    username = credentials.get("iloUsername")
    password_raw = credentials.get("iloPassword")

    if not username or not password_raw:
        logger.info(
            "[ILO_COLLECTOR] Skipping device missing iLO credentials | deviceId=%s | hostname=%s",
            device_id,
            hostname,
        )
        return False

    attempt_at = utc_now()
    _safe_record_attempt(device_id, attempt_at, database)

    try:
        decrypted_password = decrypt_secret(password_raw)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[ILO_COLLECTOR] Failed to decrypt iLO password | deviceId=%s | error=%s",
            device_id,
            exc,
        )
        _safe_record_failure(device_id, attempt_at, exc, status="AUTHENTICATION_ERROR", database=database)
        return False

    if not decrypted_password:
        logger.info(
            "[ILO_COLLECTOR] Skipping device with empty decrypted iLO password | deviceId=%s",
            device_id,
        )
        _safe_record_failure(
            device_id, attempt_at, "Decrypted iLO password is empty", status="AUTHENTICATION_ERROR", database=database
        )
        return False

    ilo_port = credentials.get("iloPort") or 443
    verify_tls = device.get("verifyTls", True)

    service_root_data: dict[str, Any] | None = None
    system_data: dict[str, Any] | None = None
    processors_data: dict[str, Any] | list[Any] | None = None
    memory_data: dict[str, Any] | list[Any] | None = None
    standard_storage_data: dict[str, Any] | list[Any] | None = None
    smart_storage_data: dict[str, Any] | list[Any] | None = None
    host_network_data: dict[str, Any] | list[Any] | None = None
    manager_network_data: dict[str, Any] | list[Any] | None = None
    thermal_data: dict[str, Any] | None = None
    power_chassis_data: dict[str, Any] | None = None
    manager_data: dict[str, Any] | None = None

    # Step 1: Establish Redfish Session and Fetch Resources
    try:
        with RedfishClient(
            ilo_address=ilo_address,
            username=username,
            password=decrypted_password,
            port=ilo_port,
            verify_tls=verify_tls,
            device_id=str(device_id),
        ) as client:
            # Essential Resource 1: Service Root
            try:
                service_root_data = client.get("/redfish/v1/")
            except RedfishError as exc:
                logger.warning(
                    "[ILO_COLLECTOR] Essential resource /redfish/v1/ failed | deviceId=%s | %s",
                    device_id,
                    exc,
                )
                _safe_record_failure(device_id, attempt_at, exc, status=_classify_redfish_exception(exc), database=database)
                return False

            if not isinstance(service_root_data, dict):
                logger.warning("[ILO_COLLECTOR] Invalid Service Root format | deviceId=%s", device_id)
                _safe_record_failure(
                    device_id, attempt_at, "Invalid Service Root response payload", status="REDFISH_ERROR", database=database
                )
                return False

            # Essential Resource 2: ComputerSystem Data
            system_uri = "/redfish/v1/Systems/1/"
            systems_link = service_root_data.get("Systems", {}).get("@odata.id")
            if systems_link:
                try:
                    systems_coll = client.get(systems_link)
                    if isinstance(systems_coll, dict) and "Members" in systems_coll and systems_coll["Members"]:
                        first_sys = systems_coll["Members"][0]
                        if isinstance(first_sys, dict) and "@odata.id" in first_sys:
                            system_uri = first_sys["@odata.id"]
                except Exception:  # noqa: BLE001
                    pass

            try:
                system_data = client.get(system_uri)
            except RedfishError as exc:
                logger.warning(
                    "[ILO_COLLECTOR] Essential resource system (%s) failed | deviceId=%s | %s",
                    system_uri,
                    device_id,
                    exc,
                )
                _safe_record_failure(device_id, attempt_at, exc, status=_classify_redfish_exception(exc), database=database)
                return False

            if not isinstance(system_data, dict):
                logger.warning("[ILO_COLLECTOR] Invalid System payload format | deviceId=%s", device_id)
                _safe_record_failure(
                    device_id, attempt_at, "Invalid System response payload", status="REDFISH_ERROR", database=database
                )
                return False

            # Optional Sub-Resources (individual safe try/except)
            try:
                proc_uri = system_data.get("Processors", {}).get("@odata.id") or f"{system_uri.rstrip('/')}/Processors/"
                proc_coll = client.get(proc_uri)
                processors_data = _resolve_collection_members(client, proc_coll)
            except Exception:  # noqa: BLE001
                processors_data = None

            try:
                mem_uri = system_data.get("Memory", {}).get("@odata.id") or f"{system_uri.rstrip('/')}/Memory/"
                mem_coll = client.get(mem_uri)
                memory_data = _resolve_collection_members(client, mem_coll)
            except Exception:  # noqa: BLE001
                memory_data = None

            try:
                stor_uri = system_data.get("Storage", {}).get("@odata.id") or f"{system_uri.rstrip('/')}/Storage/"
                stor_coll = client.get(stor_uri)
                standard_storage_data = _resolve_collection_members(client, stor_coll)
            except Exception:  # noqa: BLE001
                standard_storage_data = None

            try:
                smart_link = system_data.get("Oem", {}).get("Hpe", {}).get("SmartStorage", {}).get("@odata.id")
                if not smart_link:
                    smart_link = f"{system_uri.rstrip('/')}/SmartStorage/ArrayControllers/"
                smart_coll = client.get(smart_link)
                smart_storage_data = _resolve_collection_members(client, smart_coll)
            except Exception:  # noqa: BLE001
                smart_storage_data = None

            try:
                net_uri = system_data.get("EthernetInterfaces", {}).get("@odata.id") or f"{system_uri.rstrip('/')}/EthernetInterfaces/"
                net_coll = client.get(net_uri)
                host_network_data = _resolve_collection_members(client, net_coll)
            except Exception:  # noqa: BLE001
                host_network_data = None

            manager_uri = "/redfish/v1/Managers/1/"
            try:
                mgr_link = service_root_data.get("Managers", {}).get("@odata.id")
                if mgr_link:
                    mgr_coll = client.get(mgr_link)
                    if isinstance(mgr_coll, dict) and "Members" in mgr_coll and mgr_coll["Members"]:
                        first_mgr = mgr_coll["Members"][0]
                        if isinstance(first_mgr, dict) and "@odata.id" in first_mgr:
                            manager_uri = first_mgr["@odata.id"]
                manager_data = client.get(manager_uri)
            except Exception:  # noqa: BLE001
                manager_data = None

            if manager_data and isinstance(manager_data, dict):
                try:
                    mgr_net_uri = manager_data.get("EthernetInterfaces", {}).get("@odata.id") or f"{manager_uri.rstrip('/')}/EthernetInterfaces/"
                    mgr_net_coll = client.get(mgr_net_uri)
                    manager_network_data = _resolve_collection_members(client, mgr_net_coll)
                except Exception:  # noqa: BLE001
                    manager_network_data = None

            chassis_uri = "/redfish/v1/Chassis/1/"
            chassis_link = service_root_data.get("Chassis", {}).get("@odata.id")
            if chassis_link:
                try:
                    chassis_coll = client.get(chassis_link)
                    if isinstance(chassis_coll, dict) and "Members" in chassis_coll and chassis_coll["Members"]:
                        first_ch = chassis_coll["Members"][0]
                        if isinstance(first_ch, dict) and "@odata.id" in first_ch:
                            chassis_uri = first_ch["@odata.id"]
                except Exception:  # noqa: BLE001
                    pass

            try:
                thermal_uri = f"{chassis_uri.rstrip('/')}/Thermal/"
                thermal_data = client.get(thermal_uri)
            except Exception:  # noqa: BLE001
                thermal_data = None

            try:
                power_uri = f"{chassis_uri.rstrip('/')}/Power/"
                power_chassis_data = client.get(power_uri)
            except Exception:  # noqa: BLE001
                power_chassis_data = None

    except (RedfishAuthenticationError, RedfishConnectionError, RedfishTLSVerificationError, RedfishTimeoutError, RedfishError) as exc:
        logger.warning(
            "[ILO_COLLECTOR] Redfish collection failed for device | deviceId=%s | hostname=%s | %s",
            device_id,
            hostname,
            exc,
        )
        _safe_record_failure(device_id, attempt_at, exc, status=_classify_redfish_exception(exc), database=database)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[ILO_COLLECTOR] Unexpected exception during Redfish collection | deviceId=%s | hostname=%s | %s",
            device_id,
            hostname,
            exc,
        )
        _safe_record_failure(device_id, attempt_at, exc, status="UNKNOWN_ERROR", database=database)
        return False

    # Step 2: Normalize Collected Hardware Payloads
    try:
        hardware: ServerHardware = normalize_server_hardware(
            service_root_data=service_root_data,
            system_data=system_data,
            processors_data=processors_data,
            memory_data=memory_data,
            standard_storage_data=standard_storage_data,
            smart_storage_data=smart_storage_data,
            host_network_data=host_network_data,
            manager_network_data=manager_network_data,
            thermal_data=thermal_data,
            power_chassis_data=power_chassis_data,
            manager_data=manager_data,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[ILO_COLLECTOR] Server hardware normalization failed | deviceId=%s | %s",
            device_id,
            exc,
        )
        _safe_record_failure(device_id, attempt_at, exc, status="UNKNOWN_ERROR", database=database)
        return False

    observed_at = utc_now()

    # Step 3: Save Current Snapshot to server_hardware_current
    try:
        current_saved = save_current_server_hardware(
            device_id,
            hardware,
            observed_at,
            database=database,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[ILO_COLLECTOR] Failed to save current server hardware snapshot | deviceId=%s | %s",
            device_id,
            exc,
        )
        _safe_record_failure(device_id, attempt_at, exc, status="UNKNOWN_ERROR", database=database)
        return False

    if not current_saved:
        logger.info(
            "[ILO_COLLECTOR] Current hardware snapshot write skipped (stale observation) | deviceId=%s | observedAt=%s",
            device_id,
            observed_at,
        )
        _safe_record_failure(
            device_id, attempt_at, "Stale observation ignored", status="UNKNOWN_ERROR", database=database
        )
        return False

    # Step 4: Record Collection State SUCCESS
    _safe_record_success(device_id, attempt_at, database=database)

    # Step 5: Check 10-Minute History Retention Cadence
    _maybe_append_history(device_id, hardware, observed_at, database=database)
    return True


def _maybe_append_history(
    device_id: ObjectId | str,
    hardware: ServerHardware,
    observed_at: datetime,
    *,
    database: Any = None,
) -> None:
    """Enforce 10-minute history retention cadence before appending to server_hardware_history."""
    try:
        recent_history = get_server_hardware_history(device_id, limit=1, database=database)
        should_append = False

        if not recent_history:
            should_append = True
        else:
            latest_entry = recent_history[0]
            latest_obs = latest_entry.get("observedAt")

            if latest_obs is None or not isinstance(latest_obs, datetime):
                should_append = True
            else:
                if latest_obs.tzinfo is None:
                    latest_obs = latest_obs.replace(tzinfo=timezone.utc)

                elapsed_seconds = (observed_at - latest_obs).total_seconds()
                if elapsed_seconds >= HISTORY_CADENCE_SECONDS:
                    should_append = True
                elif elapsed_seconds < 0:
                    logger.warning(
                        "[ILO_COLLECTOR] History timestamp in future relative to stored history | deviceId=%s",
                        device_id,
                    )

        if should_append:
            try:
                history_saved = append_server_hardware_history(
                    device_id,
                    hardware,
                    observed_at,
                    database=database,
                )
                if history_saved:
                    logger.info(
                        "[ILO_COLLECTOR] Appended server hardware history snapshot | deviceId=%s | observedAt=%s",
                        device_id,
                        observed_at,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[ILO_COLLECTOR] History append failed (current write intact) | deviceId=%s | %s",
                    device_id,
                    exc,
                )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[ILO_COLLECTOR] Failed history cadence check | deviceId=%s | %s",
            device_id,
            exc,
        )


def is_server_hardware_monitoring_enabled(database: Any = None) -> bool:
    """Check whether master server hardware monitoring is enabled in Mongo settings."""
    target_db = database if database is not None else db
    try:
        settings = target_db.settings.find_one({"_id": "global"}) or {}
        if "serverHardwareMonitoringEnabled" in settings:
            return bool(settings.get("serverHardwareMonitoringEnabled"))
    except Exception:
        pass
    return True


def collect_all_server_hardware(*, database: Any = None) -> dict[str, int]:
    """
    Master collector entry point executed periodically by scheduler job.

    Discovers eligible server devices, verifies leadership, and polls devices with bounded concurrency.
    """
    if not require_scheduler_leadership(JOB_ID):
        logger.info("[ILO_COLLECTOR] Skipping iLO hardware collection — not scheduler leader")
        return {"total": 0, "polled": 0, "success": 0, "failed": 0, "skipped": 0}

    target_db = database if database is not None else db

    if not is_server_hardware_monitoring_enabled(database=target_db):
        logger.info("[ILO_COLLECTOR] Skipping iLO hardware collection — serverHardwareMonitoringEnabled is False")
        return {"total": 0, "polled": 0, "success": 0, "failed": 0, "skipped": 0}

    query = {
        "deviceType": {"$in": ELIGIBLE_DEVICE_TYPES},
        "iloAddress": {"$type": "string", "$gt": ""},
    }

    try:
        devices = list(target_db.devices.find(query))
    except Exception as exc:  # noqa: BLE001
        logger.error("[ILO_COLLECTOR] Failed to query eligible server devices: %s", exc)
        return {"total": 0, "polled": 0, "success": 0, "failed": 0, "skipped": 0}

    total_devices = len(devices)
    if total_devices == 0:
        logger.info("[ILO_COLLECTOR] No eligible iLO server devices configured for hardware collection")
        return {"total": 0, "polled": 0, "success": 0, "failed": 0, "skipped": 0}

    logger.info(
        "[ILO_COLLECTOR] Starting iLO server hardware collection cycle | eligibleDevices=%d",
        total_devices,
    )

    guard = CycleLeadershipGuard(device_renew_every=5, cycle_id="ilo_hardware_collection")
    success_count = 0
    failed_count = 0
    dispatched_count = 0

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DEVICES) as executor:
        future_map = {}
        for dev in devices:
            if not guard.ensure(reason="device_dispatch"):
                logger.warning(
                    "[ILO_COLLECTOR] Scheduler leadership lost during dispatch — aborting remaining device queue"
                )
                break

            dispatched_count += 1
            guard.note_device_visited()
            future = executor.submit(poll_single_device_ilo_hardware, dev, database=target_db)
            future_map[future] = dev

        for future in as_completed(future_map):
            dev = future_map[future]
            try:
                res = future.result()
                if res:
                    success_count += 1
                else:
                    failed_count += 1
            except Exception as exc:  # noqa: BLE001
                failed_count += 1
                logger.error(
                    "[ILO_COLLECTOR] Device hardware poll execution error | deviceId=%s | %s",
                    dev.get("_id"),
                    exc,
                )

    skipped_count = total_devices - dispatched_count

    logger.info(
        "[ILO_COLLECTOR] Completed iLO server hardware collection cycle | "
        "total=%d | success=%d | failed=%d | skipped=%d",
        total_devices,
        success_count,
        failed_count,
        skipped_count,
    )

    return {
        "total": total_devices,
        "polled": success_count + failed_count,
        "success": success_count,
        "failed": failed_count,
        "skipped": skipped_count,
    }
