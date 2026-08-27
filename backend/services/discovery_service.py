import atexit
import ipaddress
import re
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from config.database import db
from services.ping_service import ping_device
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("discovery")

_SCAN_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SCAN_PROGRESS_TTL_SECONDS = 1800
_ACTIVE_SCAN_STATUSES = frozenset({"pending", "running"})
_scan_progress_lock = threading.Lock()
_scan_progress: dict[str, dict] = {}
_active_network_scan_id: str | None = None
_network_scan_executor: ThreadPoolExecutor | None = None
_network_scan_executor_lock = threading.Lock()


class ActiveNetworkScanError(Exception):
    """Raised when a network discovery scan is already in progress."""

    def __init__(self, scan_id: str):
        self.scan_id = scan_id
        super().__init__(f"Network scan already in progress: {scan_id}")


def is_valid_scan_id(scan_id) -> bool:
    return bool(scan_id) and bool(_SCAN_ID_RE.match(str(scan_id).strip()))


def _prune_scan_progress(now: float | None = None) -> None:
    cutoff = (now if now is not None else time.monotonic()) - _SCAN_PROGRESS_TTL_SECONDS
    stale = [
        key
        for key, value in _scan_progress.items()
        if float(value.get("startedMonotonic") or 0) < cutoff
    ]
    for key in stale:
        _scan_progress.pop(key, None)


def begin_scan_progress(scan_id: str | None, total: int) -> None:
    if not is_valid_scan_id(scan_id):
        return
    now = time.monotonic()
    key = str(scan_id).strip()
    with _scan_progress_lock:
        _prune_scan_progress(now)
        prior = _scan_progress.get(key) or {}
        _scan_progress[key] = {
            "status": "running",
            "total": int(total),
            "completed": 0,
            "online": 0,
            "newlySaved": 0,
            "startedMonotonic": float(prior.get("startedMonotonic") or now),
            "elapsedSeconds": 0.0,
            "error": None,
            "summary": prior.get("summary"),
        }


def _record_scan_result(scan_id: str | None, row: dict | None) -> None:
    if not is_valid_scan_id(scan_id):
        return
    key = str(scan_id).strip()
    with _scan_progress_lock:
        state = _scan_progress.get(key)
        if not state:
            return
        state["completed"] = int(state.get("completed") or 0) + 1
        if (row or {}).get("status") == "Online":
            state["online"] = int(state.get("online") or 0) + 1
        if (row or {}).get("saved"):
            state["newlySaved"] = int(state.get("newlySaved") or 0) + 1
        started = float(state.get("startedMonotonic") or time.monotonic())
        state["elapsedSeconds"] = round(time.monotonic() - started, 1)


def finish_scan_progress(scan_id: str | None, *, status: str = "complete") -> None:
    if not is_valid_scan_id(scan_id):
        return
    key = str(scan_id).strip()
    with _scan_progress_lock:
        state = _scan_progress.get(key)
        if not state:
            return
        started = float(state.get("startedMonotonic") or time.monotonic())
        state["elapsedSeconds"] = round(time.monotonic() - started, 1)
        state["status"] = status
        if status == "complete":
            state["completed"] = int(state.get("total") or state.get("completed") or 0)


def _set_scan_summary(scan_id: str, summary: dict) -> None:
    if not is_valid_scan_id(scan_id):
        return
    key = str(scan_id).strip()
    with _scan_progress_lock:
        state = _scan_progress.get(key)
        if not state:
            return
        state["summary"] = {
            "totalScanned": int(summary.get("totalScanned") or 0),
            "online": int(summary.get("online") or 0),
            "offline": int(summary.get("offline") or 0),
            "newlySaved": int(summary.get("newlySaved") or 0),
        }
        state["error"] = None


def _set_scan_error(scan_id: str, message: str) -> None:
    if not is_valid_scan_id(scan_id):
        return
    key = str(scan_id).strip()
    with _scan_progress_lock:
        state = _scan_progress.get(key)
        if not state:
            _scan_progress[key] = {
                "status": "failed",
                "total": 0,
                "completed": 0,
                "online": 0,
                "newlySaved": 0,
                "startedMonotonic": time.monotonic(),
                "elapsedSeconds": 0.0,
                "error": message,
                "summary": None,
            }
            return
        state["status"] = "failed"
        state["error"] = message


def get_scan_progress(scan_id: str | None) -> dict | None:
    if not is_valid_scan_id(scan_id):
        return None
    key = str(scan_id).strip()
    with _scan_progress_lock:
        state = _scan_progress.get(key)
        if not state:
            return None
        started = float(state.get("startedMonotonic") or time.monotonic())
        elapsed = round(time.monotonic() - started, 1)
        total = max(int(state.get("total") or 0), 0)
        completed = min(int(state.get("completed") or 0), total) if total else int(state.get("completed") or 0)
        percent = 0 if total <= 0 else min(100, int((completed / total) * 100))
        payload = {
            "scanId": key,
            "status": state.get("status") or "running",
            "total": total,
            "completed": completed,
            "online": int(state.get("online") or 0),
            "newlySaved": int(state.get("newlySaved") or 0),
            "elapsedSeconds": elapsed,
            "percent": percent,
            "error": state.get("error"),
        }
        summary = state.get("summary")
        if isinstance(summary, dict):
            payload["summary"] = summary
        return payload


def get_active_network_scan_id() -> str | None:
    """Return the in-flight network scan id, if any."""
    with _scan_progress_lock:
        sid = _active_network_scan_id
        if not sid:
            return None
        state = _scan_progress.get(sid)
        if state and state.get("status") in _ACTIVE_SCAN_STATUSES:
            return sid
        return None


def _release_active_network_scan(scan_id: str) -> None:
    global _active_network_scan_id
    key = str(scan_id).strip()
    with _scan_progress_lock:
        if _active_network_scan_id == key:
            _active_network_scan_id = None


def _get_network_scan_executor() -> ThreadPoolExecutor:
    global _network_scan_executor
    with _network_scan_executor_lock:
        if _network_scan_executor is None:
            _network_scan_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="discovery-network-scan",
            )
        return _network_scan_executor


def shutdown_network_scan_executor() -> None:
    global _network_scan_executor
    with _network_scan_executor_lock:
        if _network_scan_executor is not None:
            _network_scan_executor.shutdown(wait=False, cancel_futures=True)
            _network_scan_executor = None


atexit.register(shutdown_network_scan_executor)


def _run_network_scan_job(ip_addresses: list[str], scan_id: str) -> None:
    """Background wrapper — calls existing discover_ips; always releases the active-scan guard."""
    try:
        results = discover_ips(ip_addresses, scan_id=scan_id)
        online = sum(1 for device in results if device.get("status") == "Online")
        offline = sum(1 for device in results if device.get("status") == "Offline")
        newly_saved = sum(1 for device in results if device.get("saved"))
        _set_scan_summary(
            scan_id,
            {
                "totalScanned": len(results),
                "online": online,
                "offline": offline,
                "newlySaved": newly_saved,
            },
        )
    except Exception:
        logger.exception(
            "[DISCOVERY] Background network scan failed | scanId=%s",
            scan_id,
        )
        finish_scan_progress(scan_id, status="failed")
        _set_scan_error(
            scan_id,
            "Network scan failed. Check server logs for details.",
        )
    finally:
        _release_active_network_scan(scan_id)


def start_network_scan_job(ip_addresses: list[str]) -> str:
    """
    Start discover_ips in a background worker and return the scanId immediately.

    Raises ActiveNetworkScanError when another network scan is still pending/running.
    """
    global _active_network_scan_id

    if not ip_addresses:
        raise ValueError("No IP addresses resolved from the selected scan targets")

    now = time.monotonic()
    with _scan_progress_lock:
        _prune_scan_progress(now)
        existing_id = _active_network_scan_id
        if existing_id:
            existing_state = _scan_progress.get(existing_id)
            if existing_state and existing_state.get("status") in _ACTIVE_SCAN_STATUSES:
                raise ActiveNetworkScanError(existing_id)
            _active_network_scan_id = None

        scan_id = str(uuid.uuid4())
        _active_network_scan_id = scan_id
        _scan_progress[scan_id] = {
            "status": "pending",
            "total": len(ip_addresses),
            "completed": 0,
            "online": 0,
            "newlySaved": 0,
            "startedMonotonic": now,
            "elapsedSeconds": 0.0,
            "error": None,
            "summary": None,
        }

    _get_network_scan_executor().submit(_run_network_scan_job, list(ip_addresses), scan_id)
    return scan_id


def get_hostname(ip_address, timeout=1.0):
    """Resolve hostname for an IP with a short timeout (non-blocking shutdown)."""
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(socket.gethostbyaddr, ip_address)
    try:
        return future.result(timeout=timeout)[0]
    except Exception:
        return None
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def resolve_hostname_for_device(ip_address):
    """Best-effort hostname lookup when saving a single discovered device."""
    return get_hostname(ip_address) or "Unknown"


def get_local_network_hint():
    """Suggest a scan range based on this machine's LAN address."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        local_ip = probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()

    try:
        network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
        hosts = list(network.hosts())
        if not hosts:
            return None

        return {
            "localIP": local_ip,
            "startIP": str(hosts[0]),
            "endIP": str(hosts[-1]),
            "network": str(network),
        }
    except ipaddress.AddressValueError:
        return None


def scan_single_ip(ip_address):
    try:
        result = ping_device(ip_address)

        # ping_device returns Online / Not Reachable / Offline (Critical) — never plain "Offline"
        if not result.get("success") or result.get("status") != "Online":
            return {
                "hostname": None,
                "ipAddress": ip_address,
                "status": "Offline",
                "responseTime": None,
                "saved": False,
                "deviceType": None,
                "vendor": None,
                "operatingSystem": None,
                "classificationConfidence": None,
            }

        existing = db.devices.find_one({"ipAddress": ip_address})

        # New hosts: Nmap → classify → insert.
        # Existing hosts: ping status only (Nmap skipped inside enrich_online_host).
        from services.discovery.apply import enrich_online_host  # noqa: PLC0415

        return enrich_online_host(
            ip_address,
            ping_result=result,
            existing=existing,
        )

    except Exception:
        return {
            "hostname": None,
            "ipAddress": ip_address,
            "status": "Offline",
            "responseTime": None,
            "saved": False,
            "deviceType": None,
            "vendor": None,
            "operatingSystem": None,
            "classificationConfidence": None,
        }


def discover_ips(ip_addresses, scan_id=None):
    if len(ip_addresses) > 1024:
        raise ValueError("Scan target list is too large. Maximum 1024 addresses per scan.")

    started = time.monotonic()
    target_count = len(ip_addresses)
    logger.info("[DISCOVERY] Scan started | targets=%s | scanId=%s", target_count, scan_id or "-")
    begin_scan_progress(scan_id, target_count)

    results = []
    try:
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [
                executor.submit(scan_single_ip, ip)
                for ip in ip_addresses
            ]

            for future in as_completed(futures):
                row = future.result()
                results.append(row)
                _record_scan_result(scan_id, row)
        finish_scan_progress(scan_id, status="complete")
    except Exception:
        finish_scan_progress(scan_id, status="failed")
        raise

    results.sort(
        key=lambda item: ipaddress.IPv4Address(item["ipAddress"])
    )

    elapsed = round(time.monotonic() - started, 2)
    online = sum(1 for item in results if item.get("status") == "Online")
    newly_saved = sum(1 for item in results if item.get("saved"))
    existing_online = online - newly_saved
    logger.info(
        "[DISCOVERY] Scan completed | targets=%s | online=%s | new=%s | "
        "existingOnline=%s | elapsed=%ss | scanId=%s",
        target_count,
        online,
        newly_saved,
        existing_online,
        elapsed,
        scan_id or "-",
    )
    return results


def discover_devices(start_ip, end_ip, scan_id=None):
    try:
        start = ipaddress.IPv4Address(start_ip)
        end = ipaddress.IPv4Address(end_ip)
    except ipaddress.AddressValueError as error:
        raise ValueError("Invalid IP address.") from error

    if start > end:
        raise ValueError("Start IP must be less than or equal to End IP.")

    total = int(end) - int(start) + 1
    if total > 1024:
        raise ValueError("Scan range too large. Maximum 1024 addresses per scan.")

    ip_addresses = [
        str(ipaddress.IPv4Address(ip))
        for ip in range(int(start), int(end) + 1)
    ]
    return discover_ips(ip_addresses, scan_id=scan_id)
