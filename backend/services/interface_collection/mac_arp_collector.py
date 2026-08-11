"""
mac_arp_collector.py
=====================
Passive MAC/ARP table polling + active ARP-forcing subnet sweep.

Resolves "what IP is connected to this switch port" via:
    port -> MAC(s) learned on that port (bridge/forwarding table)
         -> MAC in a shared ARP table (built from whichever devices
            actually route, discovered live, never hardcoded)
         -> IP

Two complementary collection strategies (see ``services/interface_collection/
port_resolution.py`` for the read-side decision logic that consumes this
data):

Passive poll (frequent, lightweight)
    Read every switch's MAC table, and — when that switch happens to be
    doing Layer-3 routing for a VLAN — its ARP table too. Most switches in
    a fleet are L2-only, so the ARP read naturally comes back empty for
    them; that is not an error.

Active sweep (infrequent, more invasive)
    A MAC only appears in an ARP table after the device has spoken to its
    gateway. A device that was just plugged in and hasn't sent anything
    will never show up no matter how many times the passive poll re-reads
    the table. This job reads each routing device's REAL connected subnets
    (via its own routing table, never an assumed/hardcoded subnet) and
    pings addresses in them that aren't already known, forcing the ARP
    exchange a passive read can't, then immediately re-reads that device's
    ARP table so the same cycle captures the result.
"""

from __future__ import annotations

import ipaddress
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from config.database import (
    ARP_ACTIVE_SWEEP_MAX_HOSTS,
    MAC_ARP_POLL_MAX_THREADS,
    db,
)
from services.interface_collection.mac_arp_parser import (
    parse_cisco_connected_routes,
    parse_cisco_ip_arp,
    parse_cisco_mac_address_table,
)
from services.interface_collection.port_resolution import normalize_mac
from services.interface_collection.ssh_collector import (
    SSHCollectorError,
    SSHInterfaceCollector,
    resolve_ssh_credentials,
)
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("interface.mac_arp")

# Same eligibility criteria as interface discovery (collector.py), kept as
# a local copy rather than importing that module's private helper so this
# module stays independently runnable/testable.
_SWITCH_LIKE_TYPES = frozenset({
    "switch", "switches", "router", "routers", "firewall", "firewalls",
    "l3 switch", "l2 switch", "core switch", "access switch",
})

# Only the Cisco family is fully supported today, matching this codebase's
# existing CDP/LLDP/switchport coverage (see ssh_collector.COMMAND_SETS).
# Other vendors soft-fail to "unsupported" per device rather than raising —
# a fleet with mixed vendors still gets MAC/ARP data for the Cisco switches.
_MAC_TABLE_COMMANDS = {
    "cisco_ios": "show mac address-table",
    "cisco_xe": "show mac address-table",
    "cisco_nxos": "show mac address-table",
}
_ARP_COMMANDS = {
    "cisco_ios": "show ip arp",
    "cisco_xe": "show ip arp",
    "cisco_nxos": "show ip arp",
}
_CONNECTED_ROUTES_COMMANDS = {
    "cisco_ios": "show ip route connected",
    "cisco_xe": "show ip route connected",
    "cisco_nxos": "show ip route connected",
}


def ensure_mac_arp_indexes() -> None:
    """Create indexes for the MAC table / ARP cache collections (idempotent)."""
    try:
        db.port_mac_table.create_index(
            [("deviceId", 1), ("interfaceName", 1)],
            unique=True,
            name="uniq_device_interface_mac",
        )
        db.arp_cache.create_index(
            [("macAddress", 1)], unique=True, name="uniq_arp_mac",
        )
        db.arp_cache.create_index([("updatedAt", -1)], name="idx_arp_updated")
        logger.info("[MAC/ARP] MongoDB indexes ensured")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MAC/ARP] Failed to ensure indexes: %s", exc)


def _is_poll_candidate(device: dict) -> bool:
    if device.get("credentials"):
        return True
    device_type = (device.get("deviceType") or device.get("type") or "").strip().lower()
    if device_type in _SWITCH_LIKE_TYPES:
        return True
    return any(token in device_type for token in ("switch", "router", "firewall"))


def _eligible_devices() -> list[dict]:
    candidates = list(db.devices.find({"status": "Online"}))
    return [d for d in candidates if _is_poll_candidate(d)]


# ---------------------------------------------------------------------------
# Passive poll
# ---------------------------------------------------------------------------

def poll_device_mac_and_arp(device: dict) -> dict[str, Any]:
    """
    Passive poll for one device: MAC table always, ARP table when this
    device happens to be doing L3 routing (empty result otherwise — never
    an error, since most switches in a fleet are L2-only).

    Never raises. Safe for scheduler / bulk callers.
    """
    ip_address = device.get("ipAddress", "unknown")
    device_id = device["_id"]
    now = datetime.now(timezone.utc)

    result: dict[str, Any] = {
        "success": False, "ip": ip_address, "ports": 0, "arpEntries": 0, "error": None,
    }

    try:
        creds = resolve_ssh_credentials(device)
    except SSHCollectorError as exc:
        result["error"] = str(exc)
        return result

    mac_cmd = _MAC_TABLE_COMMANDS.get(creds.vendor)
    if not mac_cmd:
        result["error"] = f"MAC table polling unsupported for vendor {creds.vendor}"
        return result

    try:
        with SSHInterfaceCollector(creds) as collector:
            mac_output = collector.run_command(mac_cmd)
            port_macs = parse_cisco_mac_address_table(mac_output)

            arp_entries: list[dict] = []
            arp_cmd = _ARP_COMMANDS.get(creds.vendor)
            if arp_cmd:
                try:
                    arp_output = collector.run_command(arp_cmd)
                    arp_entries = parse_cisco_ip_arp(arp_output)
                except Exception as exc:  # noqa: BLE001
                    # L2-only switches may not support/allow this command —
                    # that's expected, not a failure of the poll overall.
                    logger.debug(
                        "[MAC/ARP] ARP read skipped | host=%s | %s", ip_address, exc,
                    )
    except SSHCollectorError as exc:
        result["error"] = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("[MAC/ARP] Unexpected poll failure | host=%s", ip_address)
        result["error"] = str(exc)
        return result

    _persist_port_macs(device_id, port_macs, now)
    _persist_arp_entries(arp_entries, device_id, device.get("hostname") or ip_address, now)

    result.update(success=True, ports=len(port_macs), arpEntries=len(arp_entries))
    return result


def _persist_port_macs(device_id, port_macs: dict[str, list[str]], now: datetime) -> None:
    """
    Replace each port's learned-MAC snapshot wholesale (current state, not
    an append-only log). A port that reports zero MACs this poll but has a
    stale row from a previous poll is cleared — the resolver must see
    "0 MACs" the moment a device is unplugged, not a stale MAC that has
    since moved or gone away.
    """
    seen_ports = set(port_macs.keys())
    for interface_name, macs in port_macs.items():
        db.port_mac_table.update_one(
            {"deviceId": device_id, "interfaceName": interface_name},
            {"$set": {
                "deviceId": device_id,
                "interfaceName": interface_name,
                "macAddresses": macs,
                "updatedAt": now,
            }},
            upsert=True,
        )

    stale_cursor = db.port_mac_table.find(
        {"deviceId": device_id, "interfaceName": {"$nin": list(seen_ports)}},
        {"_id": 1, "macAddresses": 1},
    )
    for doc in stale_cursor:
        if doc.get("macAddresses"):
            db.port_mac_table.update_one(
                {"_id": doc["_id"]},
                {"$set": {"macAddresses": [], "updatedAt": now}},
            )


def _persist_arp_entries(
    entries: list[dict], device_id, hostname: str, now: datetime,
) -> None:
    """
    Upsert into the shared, cross-device ARP cache keyed by MAC — not
    per-switch. Whichever L3 device most recently reported a MAC wins;
    ARP entries are inherently host-unique so this doesn't need to
    reconcile conflicting sources.
    """
    for entry in entries:
        mac = normalize_mac(entry.get("mac"))
        ip_addr = entry.get("ip")
        if not mac or not ip_addr:
            continue
        db.arp_cache.update_one(
            {"macAddress": mac},
            {"$set": {
                "macAddress": mac,
                "ipAddress": ip_addr,
                "sourceDeviceId": device_id,
                "sourceHostname": hostname,
                "updatedAt": now,
            }},
            upsert=True,
        )


def poll_all_devices_mac_and_arp() -> dict[str, Any]:
    """Bulk passive poll — MAC table (all switches) + ARP table (L3 ones)."""
    logger.info("[MAC/ARP] Passive poll started")
    start = time.monotonic()
    eligible = _eligible_devices()

    succeeded = 0
    failed = 0
    total_ports = 0
    total_arp = 0
    errors: list[dict[str, Any]] = []

    workers = max(int(MAC_ARP_POLL_MAX_THREADS), 1)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(poll_device_mac_and_arp, d): d for d in eligible}
        for future in as_completed(futures):
            result = future.result()
            if result["success"]:
                succeeded += 1
                total_ports += result["ports"]
                total_arp += result["arpEntries"]
            else:
                failed += 1
                if result.get("error"):
                    errors.append({"ip": result["ip"], "error": result["error"]})

    elapsed = round(time.monotonic() - start, 2)
    logger.info(
        "[MAC/ARP] Passive poll finished in %.2fs | total=%d ok=%d failed=%d "
        "ports=%d arpEntries=%d",
        elapsed, len(eligible), succeeded, failed, total_ports, total_arp,
    )
    return {
        "total": len(eligible),
        "succeeded": succeeded,
        "failed": failed,
        "ports": total_ports,
        "arpEntries": total_arp,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Active sweep
# ---------------------------------------------------------------------------

def _sweepable_hosts(
    network: str, prefix_length: int, *, exclude_ip: str | None, already_known: set[str],
) -> list[str]:
    """Host addresses in a subnet not already ARP-resolved, capped for safety."""
    try:
        net = ipaddress.ip_network(f"{network}/{prefix_length}", strict=False)
    except ValueError:
        return []
    if net.num_addresses <= 2:
        return []  # /31 or /32 — no sweepable host range.

    hosts: list[str] = []
    for addr in net.hosts():
        text = str(addr)
        if text == exclude_ip or text in already_known:
            continue
        hosts.append(text)
        if len(hosts) >= ARP_ACTIVE_SWEEP_MAX_HOSTS:
            break
    return hosts


def _ping_sweep(targets: list[str]) -> None:
    """
    Best-effort ICMP probes whose only purpose is to force the routing
    device to ARP for each target on its directly-connected segment.
    Results are intentionally not checked here — the source of truth is
    the router's ARP table re-read immediately after, not this ping's
    own success/failure.
    """
    from ping3 import ping  # noqa: PLC0415

    def _probe(addr: str) -> None:
        try:
            ping(addr, timeout=1)
        except Exception:  # noqa: BLE001
            pass

    with ThreadPoolExecutor(max_workers=32) as executor:
        list(executor.map(_probe, targets))


def sweep_device_subnets(device: dict) -> dict[str, Any]:
    """
    Active sweep for one device: read its real connected subnets, ping
    addresses in them not already in the shared ARP cache, then
    immediately re-read this device's ARP table so newly-forced entries
    are captured in the same cycle rather than waiting for the next
    passive poll.

    A device with zero connected subnets (an L2-only switch) is a
    successful no-op, not a failure — the active sweep only does
    something on devices that actually route.
    """
    ip_address = device.get("ipAddress", "unknown")
    device_id = device["_id"]
    now = datetime.now(timezone.utc)

    result: dict[str, Any] = {
        "success": False, "ip": ip_address, "subnets": 0, "probed": 0, "error": None,
    }

    try:
        creds = resolve_ssh_credentials(device)
    except SSHCollectorError as exc:
        result["error"] = str(exc)
        return result

    routes_cmd = _CONNECTED_ROUTES_COMMANDS.get(creds.vendor)
    if not routes_cmd:
        result["error"] = f"Connected-route discovery unsupported for vendor {creds.vendor}"
        return result

    try:
        with SSHInterfaceCollector(creds) as collector:
            routes_output = collector.run_command(routes_cmd)
            routes = parse_cisco_connected_routes(routes_output)
            if not routes:
                result["success"] = True
                return result

            known_ips = {
                doc["ipAddress"]
                for doc in db.arp_cache.find({}, {"ipAddress": 1})
                if doc.get("ipAddress")
            }

            probe_targets: list[str] = []
            for route in routes:
                probe_targets.extend(_sweepable_hosts(
                    route["network"],
                    route["prefixLength"],
                    exclude_ip=ip_address,
                    already_known=known_ips,
                ))

            if probe_targets:
                _ping_sweep(probe_targets)

            arp_cmd = _ARP_COMMANDS.get(creds.vendor)
            if arp_cmd:
                arp_output = collector.run_command(arp_cmd)
                arp_entries = parse_cisco_ip_arp(arp_output)
                _persist_arp_entries(
                    arp_entries, device_id, device.get("hostname") or ip_address, now,
                )

            result.update(success=True, subnets=len(routes), probed=len(probe_targets))
            return result

    except SSHCollectorError as exc:
        result["error"] = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("[MAC/ARP] Unexpected sweep failure | host=%s", ip_address)
        result["error"] = str(exc)
        return result


def sweep_all_device_subnets() -> dict[str, Any]:
    """
    Bulk active sweep. Run sequentially (not thread-pooled like the passive
    poll) — this job is deliberately the slow, invasive one; there is no
    latency pressure to parallelise it, and sequential execution keeps the
    probe load on the network predictable.
    """
    logger.info("[MAC/ARP] Active ARP sweep started")
    start = time.monotonic()
    eligible = _eligible_devices()

    succeeded = 0
    failed = 0
    total_subnets = 0
    total_probed = 0
    errors: list[dict[str, Any]] = []

    for device in eligible:
        res = sweep_device_subnets(device)
        if res["success"]:
            succeeded += 1
            total_subnets += res["subnets"]
            total_probed += res["probed"]
        else:
            failed += 1
            if res.get("error"):
                errors.append({"ip": res["ip"], "error": res["error"]})

    elapsed = round(time.monotonic() - start, 2)
    logger.info(
        "[MAC/ARP] Active sweep finished in %.2fs | total=%d ok=%d failed=%d "
        "subnets=%d probed=%d",
        elapsed, len(eligible), succeeded, failed, total_subnets, total_probed,
    )
    return {
        "total": len(eligible),
        "succeeded": succeeded,
        "failed": failed,
        "subnets": total_subnets,
        "probed": total_probed,
        "errors": errors,
    }
