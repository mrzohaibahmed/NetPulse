"""
nmap_service.py
================
Nmap-based device information scanner for the Network Monitor system.

Responsibilities
----------------
- Run Nmap scans against online devices to collect rich metadata.
- Store results in the ``networkInfo`` sub-document on each device.
- Expose reusable helpers consumable from routes, schedulers, or future services.

This module is **completely independent** of:
  - ping_service.py      (online/offline detection)
  - discovery_service.py (network sweeping)
  - monitor_service.py   (ping scheduling)

Nmap is responsible ONLY for collecting metadata about devices that are
already confirmed online by the ping service.

Scan strategy
-------------
The default scan arguments (-A -T4) combine:
  -O   OS detection       -> networkInfo.os
  -sV  Version detection  -> networkInfo.ports[].product / version
  -sC  Default NSE scripts -> extra service details
  -T4  Timing template 4  -> faster scan, suitable for LAN

Elevated privileges (administrator on Windows, sudo on Linux/macOS) are
required for -O (raw socket access). Without them, switch NMAP_ARGUMENTS
to "-sV -T4" in .env and OS detection will be skipped gracefully.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

# python-nmap wraps the nmap binary; import is deferred so Flask starts even if
# nmap is not yet installed, and errors are reported only when a scan is
# requested.
try:
    import nmap as nmap_lib  # package: python-nmap
    _NMAP_AVAILABLE = True
except ImportError:
    _NMAP_AVAILABLE = False
    nmap_lib = None  # type: ignore[assignment]

from config.database import (
    MAX_SCAN_THREADS,
    NMAP_ARGUMENTS,
    NMAP_PATH,
    NMAP_TIMEOUT,
    db,
)
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("nmap")

# ---------------------------------------------------------------------------
# Module-level scanner singleton (created once, reused across calls).
# Using a module-level instance avoids the overhead of re-initialising the
# python-nmap PortScanner object on every request.
# ---------------------------------------------------------------------------
_scanner: Any = None  # nmap.PortScanner | None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_scanner() -> Any:
    """
    Return the shared nmap.PortScanner instance, creating it on first call.

    Raises
    ------
    RuntimeError
        If python-nmap is not installed.
    RuntimeError
        If the nmap binary cannot be found at NMAP_PATH (or system PATH).
    """
    global _scanner

    if not _NMAP_AVAILABLE:
        raise RuntimeError(
            "python-nmap is not installed. "
            "Run: pip install python-nmap"
        )

    if _scanner is None:
        try:
            # nmap_path=None -> python-nmap auto-detects from system PATH.
            # Passing an explicit path supports custom install locations (Windows).
            if NMAP_PATH:
                _scanner = nmap_lib.PortScanner(nmap_search_path=(NMAP_PATH,))
            else:
                _scanner = nmap_lib.PortScanner()
        except nmap_lib.PortScannerError as exc:
            raise RuntimeError(
                f"Nmap binary not found. "
                f"Install Nmap and set NMAP_PATH in .env if needed. Details: {exc}"
            ) from exc

    return _scanner


def _safe_str(value: Any, default: str = "") -> str:
    """Cast a value to a stripped string; return default on None / falsy."""
    if value is None:
        return default
    return str(value).strip() or default


def _extract_os_info(host_data: Any) -> dict:
    """
    Parse the osmatch block from a python-nmap host result.

    Returns
    -------
    dict
        Keys: name, family, generation, accuracy.
        All values are strings; empty string when unavailable.

    Notes
    -----
    Nmap returns OS matches sorted by accuracy (highest first).
    osmatch[0] is therefore always the best candidate.
    """
    os_info = {
        "name": "",
        "family": "",
        "generation": "",
        "accuracy": "",
    }

    os_matches = host_data.get("osmatch", [])
    if not os_matches:
        return os_info

    # Best match is the first entry (nmap sorts descending by accuracy).
    best_match = os_matches[0]
    os_info["name"] = _safe_str(best_match.get("name"))
    os_info["accuracy"] = _safe_str(best_match.get("accuracy"))

    # osclass contains family / generation details inside the top match.
    os_classes = best_match.get("osclass", [])
    if os_classes:
        top_class = os_classes[0]
        os_info["family"] = _safe_str(top_class.get("osfamily"))
        os_info["generation"] = _safe_str(top_class.get("osgen"))

    return os_info


def _extract_ports(host_data: Any) -> list:
    """
    Extract all scanned ports from the host result.

    Iterates every protocol (tcp/udp) and every port within it, collecting:
      - port     : int   port number
      - protocol : str   "tcp" or "udp"
      - state    : str   "open" / "closed" / "filtered"
      - service  : str   well-known service name (e.g. "http")
      - product  : str   detected product name (e.g. "Apache httpd")
      - version  : str   detected version string (e.g. "2.4.54")
      - extraInfo: str   any extra detail Nmap reported

    Returns a list of port dicts sorted by port number ascending.
    """
    ports = []

    for protocol in host_data.all_protocols():
        port_dict = host_data[protocol]
        for port_number, port_data in port_dict.items():
            ports.append({
                "port": int(port_number),
                "protocol": protocol,
                "state": _safe_str(port_data.get("state")),
                "service": _safe_str(port_data.get("name")),
                "product": _safe_str(port_data.get("product")),
                "version": _safe_str(port_data.get("version")),
                "extraInfo": _safe_str(port_data.get("extrainfo")),
            })

    ports.sort(key=lambda p: p["port"])
    return ports


def _extract_services(ports: list) -> list:
    """
    Derive a deduplicated list of service names from collected port data.

    Useful for quick filtering / display without iterating the full ports list.
    Returns a sorted list of unique, non-empty service name strings.
    """
    seen: set = set()
    services = []
    for port in ports:
        svc = port.get("service", "")
        if svc and svc not in seen:
            seen.add(svc)
            services.append(svc)
    return sorted(services)


def _extract_mac_info(host_data: Any) -> tuple:
    """
    Extract MAC address and hardware vendor from the addresses block.

    Returns
    -------
    tuple[str, str]
        (mac_address, vendor) -- empty strings when unavailable.

    Notes
    -----
    MAC address is only visible when scanning on the same local network segment
    (Layer 2). Remote scans across routers will not return MAC data.
    """
    addresses = host_data.get("addresses", {})
    mac = _safe_str(addresses.get("mac"))

    # python-nmap places vendor under host_data["vendor"][mac_address]
    vendor = ""
    vendor_map = host_data.get("vendor", {})
    if mac and vendor_map:
        vendor = _safe_str(vendor_map.get(mac))

    return mac, vendor


def _extract_hostname(host_data: Any, ip_address: str) -> str:
    """
    Return the first PTR / forward hostname nmap resolved, or empty string.

    Nmap stores hostnames as a list of dicts with keys "name" and "type".
    PTR records are preferred; the first available entry is the fallback.
    """
    hostnames = host_data.get("hostnames", [])
    if not hostnames:
        return ""

    # Prefer PTR (reverse DNS) entries.
    for entry in hostnames:
        if entry.get("type") == "PTR":
            return _safe_str(entry.get("name"))

    # Fall back to first available.
    return _safe_str(hostnames[0].get("name"))


def _build_network_info(scan_result: dict, ip_address: str) -> dict:
    """
    Transform a raw python-nmap scan result into the networkInfo schema.

    Parameters
    ----------
    scan_result : dict
        The full nm.scan(...) return value.
    ip_address : str
        The host that was scanned (used to look up the correct key).

    Returns
    -------
    dict
        Structured networkInfo document ready for MongoDB insertion.

    Schema stored in MongoDB
    ------------------------
    {
        "hostname"  : str,
        "macAddress": str,
        "vendor"    : str,
        "os": {
            "name"      : str,
            "family"    : str,
            "generation": str,
            "accuracy"  : str
        },
        "deviceType": str,   # nmap classification (general purpose, router, etc.)
        "ports"     : list,  # list of port dicts
        "services"  : list,  # deduplicated service name strings
        "lastScan"  : datetime (UTC)
    }
    """
    # python-nmap key is "scan" -> dict keyed by IP string.
    host_data = scan_result.get("scan", {}).get(ip_address, {})

    mac, vendor = _extract_mac_info(host_data)
    ports = _extract_ports(host_data)

    # Nmap osclass.type gives a device classification string such as
    # "general purpose", "router", "switch", "printer", etc.
    device_type = ""
    os_matches = host_data.get("osmatch", [])
    if os_matches:
        os_classes = os_matches[0].get("osclass", [])
        if os_classes:
            device_type = _safe_str(os_classes[0].get("type"))

    return {
        "hostname": _extract_hostname(host_data, ip_address),
        "macAddress": mac,
        "vendor": vendor,
        "os": _extract_os_info(host_data),
        "deviceType": device_type,
        "ports": ports,
        "services": _extract_services(ports),
        "lastScan": datetime.now(timezone.utc),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_device_nmap(ip_address: str) -> dict:
    """
    Execute an Nmap scan against a single IP address and return parsed results.

    Uses the scan arguments from NMAP_ARGUMENTS (env / config) and honours
    NMAP_TIMEOUT to prevent indefinite hangs on unresponsive hosts.

    Parameters
    ----------
    ip_address : str
        Valid IPv4 address of the target host.

    Returns
    -------
    dict
        networkInfo document (see _build_network_info).

    Raises
    ------
    RuntimeError
        If python-nmap or the nmap binary is unavailable, or nmap crashes.
    ValueError
        If the IP address string is invalid / not found in nmap results.
    """
    scanner = _get_scanner()

    logger.info("[NMAP] Starting scan %s | args=%s", ip_address, NMAP_ARGUMENTS)
    start_time = time.monotonic()

    try:
        # arguments= is appended verbatim to the nmap command.
        # timeout= controls how long python-nmap waits for the subprocess.
        scan_result = scanner.scan(
            hosts=ip_address,
            arguments=NMAP_ARGUMENTS,
            timeout=NMAP_TIMEOUT,
        )
    except nmap_lib.PortScannerError as exc:
        raise RuntimeError(
            f"[NMAP] Nmap process error for {ip_address}: {exc}"
        ) from exc

    elapsed = round(time.monotonic() - start_time, 2)

    # Verify nmap actually reached the host (filtered hosts may be absent).
    if ip_address not in scan_result.get("scan", {}):
        raise ValueError(
            f"[NMAP] Host {ip_address} was not found in scan results. "
            "The host may be filtered or unreachable."
        )

    network_info = _build_network_info(scan_result, ip_address)

    open_ports = [p for p in network_info.get("ports", []) if p.get("state") == "open"]
    os_name = network_info.get("os", {}).get("name") or "Unknown"

    logger.info("[NMAP] OS detected: %s | host=%s", os_name, ip_address)
    logger.info("[NMAP] %d open port(s) | host=%s", len(open_ports), ip_address)
    logger.info("[NMAP] Scan completed in %.2fs | host=%s", elapsed, ip_address)

    return network_info


def update_device_network_info(device_id: ObjectId, network_info: dict) -> None:
    """
    Persist Nmap scan results into the device document in MongoDB.

    Stores everything under the ``networkInfo`` key so existing fields
    (status, responseTime, lastSeen, etc.) are never overwritten.
    ``updatedAt`` is refreshed to reflect the time of the scan update.

    Parameters
    ----------
    device_id : ObjectId
        MongoDB _id of the device document to update.
    network_info : dict
        Structured networkInfo dict returned by scan_device_nmap.
    """
    db.devices.update_one(
        {"_id": device_id},
        {
            "$set": {
                "networkInfo": network_info,
                "updatedAt": datetime.now(timezone.utc),
            }
        },
    )


def scan_and_update_device(device: dict) -> dict:
    """
    Orchestrate a full Nmap scan for one device: scan -> parse -> persist.

    This is the primary entry point called from routes and the scheduler.
    It validates the device is online, runs the nmap scan, and updates MongoDB.

    Parameters
    ----------
    device : dict
        A device document from MongoDB. Must include _id, ipAddress, status,
        and optionally hostname for logging.

    Returns
    -------
    dict
        { "success": bool, "ip": str, "error": str | None }

    Notes
    -----
    Never raises. All exceptions are caught and returned in the result dict so
    callers (scheduler, bulk scan route) can tally failures without crashing.
    """
    ip_address = device.get("ipAddress", "unknown")
    hostname = device.get("hostname", ip_address)
    device_id: ObjectId = device["_id"]

    # Guard: skip offline devices to avoid wasting scan time.
    # Ping service is the source of truth for online/offline status.
    if device.get("status") != "Online":
        logger.info(
            "[NMAP] Skipping offline device %s (%s)", hostname, ip_address
        )
        return {"success": False, "ip": ip_address, "error": "Device is not online"}

    try:
        network_info = scan_device_nmap(ip_address)
        update_device_network_info(device_id, network_info)
        return {"success": True, "ip": ip_address, "error": None}

    except RuntimeError as exc:
        # Covers: nmap missing, PortScannerError, permission denied (root required).
        logger.error("[NMAP] Failed | host=%s | %s", ip_address, exc)
        return {"success": False, "ip": ip_address, "error": str(exc)}

    except ValueError as exc:
        # Covers: host not found in results, invalid IP, filtered host.
        logger.warning("[NMAP] Host unreachable | host=%s | %s", ip_address, exc)
        return {"success": False, "ip": ip_address, "error": str(exc)}

    except Exception as exc:
        # Catch-all: never let a single device crash the scheduler cycle.
        logger.exception("[NMAP] Unexpected error | host=%s | %s", ip_address, exc)
        return {"success": False, "ip": ip_address, "error": str(exc)}


def scan_all_online_devices() -> dict:
    """
    Scan every currently-online device using a bounded thread pool.

    Fetches all devices with status == "Online" from MongoDB, then runs
    scan_and_update_device concurrently up to MAX_SCAN_THREADS workers.

    A thread pool (rather than sequential scanning) reduces total wall-clock
    time while the worker cap prevents overloading the network or the host
    machine with too many simultaneous nmap processes.

    Returns
    -------
    dict
        { "total": int, "scanned": int, "failed": int, "errors": list }

    This function is safe to call from an APScheduler background thread.
    All per-device errors are captured; the function itself never raises.
    """
    logger.info("[NMAP] Bulk scan started")
    start_time = time.monotonic()

    # Pre-filter to online devices: offline devices are also guarded inside
    # scan_and_update_device, but filtering here avoids unnecessary submissions.
    online_devices = list(db.devices.find({"status": "Online"}))
    total = len(online_devices)

    if total == 0:
        logger.info("[NMAP] No online devices to scan")
        return {"total": 0, "scanned": 0, "failed": 0, "errors": []}

    logger.info("[NMAP] %d online device(s) queued for scan", total)

    scanned = 0
    failed = 0
    errors: list = []

    # ThreadPoolExecutor with a capped worker count prevents flooding the
    # network with too many simultaneous nmap processes at once.
    with ThreadPoolExecutor(max_workers=MAX_SCAN_THREADS) as executor:
        future_map = {
            executor.submit(scan_and_update_device, device): device
            for device in online_devices
        }

        for future in as_completed(future_map):
            result = future.result()
            if result["success"]:
                scanned += 1
            else:
                failed += 1
                errors.append({
                    "ip": result["ip"],
                    "error": result["error"],
                })

    elapsed = round(time.monotonic() - start_time, 2)
    logger.info(
        "[NMAP] Bulk scan finished in %.2fs | total=%d scanned=%d failed=%d",
        elapsed, total, scanned, failed,
    )

    return {
        "total": total,
        "scanned": scanned,
        "failed": failed,
        "errors": errors,
    }
