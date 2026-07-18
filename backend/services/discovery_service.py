import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from config.database import db
from models.device import create_device
from services.ping_service import ping_device


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
    return get_hostname(ip_address) or "Unknown Device"


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
            }

        existing = db.devices.find_one({"ipAddress": ip_address})
        saved = False
        now = datetime.now(timezone.utc)

        if existing is None:
            hostname = resolve_hostname_for_device(ip_address)
        else:
            hostname = existing.get("hostname") or "Unknown Device"

        if existing is None:
            device = create_device(
                hostname=hostname,
                ip_address=ip_address,
                device_type="Unknown",
                critical=False,
                monitor=True,
            )
            device["status"] = "Online"
            device["responseTime"] = result["responseTime"]
            device["lastSeen"] = result["lastSeen"]
            device["updatedAt"] = now

            db.devices.insert_one(device)
            saved = True
        else:
            db.devices.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "status": "Online",
                        "responseTime": result["responseTime"],
                        "lastSeen": result["lastSeen"],
                        "updatedAt": now,
                    }
                },
            )

        return {
            "hostname": hostname,
            "ipAddress": ip_address,
            "status": "Online",
            "responseTime": result["responseTime"],
            "saved": saved,
        }

    except Exception:
        return {
            "hostname": None,
            "ipAddress": ip_address,
            "status": "Offline",
            "responseTime": None,
            "saved": False,
        }


def discover_devices(start_ip, end_ip):
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

    results = []

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [
            executor.submit(scan_single_ip, ip)
            for ip in ip_addresses
        ]

        for future in as_completed(futures):
            results.append(future.result())

    results.sort(
        key=lambda item: ipaddress.IPv4Address(item["ipAddress"])
    )

    return results
