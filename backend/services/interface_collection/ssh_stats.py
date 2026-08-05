"""
ssh_stats.py
============
SSH fallback collector for interface counters when SNMP is unavailable.

Uses Cisco-style counter commands (and a Juniper variant) and returns the
same raw dict shape as ``snmp.SNMPInterfaceCollector.collect_interface_stats``.
"""

from __future__ import annotations

import re
from typing import Any

from services.interface_collection.ssh_collector import (
    SSHCollectorError,
    SSHCredentials,
    SSHInterfaceCollector,
    resolve_ssh_credentials,
)
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("interface.ssh_stats")

STATS_COMMAND_SETS: dict[str, dict[str, str]] = {
    "cisco_ios": {
        "counters": "show interfaces counters",
        "errors": "show interfaces counters errors",
        "status": "show interfaces status",
    },
    "cisco_xe": {
        "counters": "show interfaces counters",
        "errors": "show interfaces counters errors",
        "status": "show interfaces status",
    },
    "cisco_nxos": {
        "counters": "show interface counters",
        "errors": "show interface counters errors",
        "status": "show interface status",
    },
    "juniper_junos": {
        "counters": "show interfaces statistics",
    },
    "generic": {
        "counters": "show interfaces counters",
        "errors": "show interfaces counters errors",
    },
}


class SSHStatsCollector:
    """Collect interface counters over an existing SSH transport pattern."""

    def __init__(self, credentials: SSHCredentials):
        self.credentials = credentials

    def collect_interface_stats(self) -> list[dict]:
        vendor = self.credentials.vendor
        commands = dict(
            STATS_COMMAND_SETS.get(vendor)
            or STATS_COMMAND_SETS["generic"]
        )

        outputs: dict[str, str] = {}
        with SSHInterfaceCollector(self.credentials) as session:
            for key, command in commands.items():
                try:
                    outputs[key] = session.run_command(command)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[SSH-STATS] Command failed | host=%s cmd=%s | %s",
                        self.credentials.host,
                        command,
                        exc,
                    )
                    outputs[key] = ""

        if vendor.startswith("juniper"):
            rows = parse_juniper_statistics(outputs.get("counters", ""))
        else:
            status_map = parse_cisco_status_map(outputs.get("status", ""))
            rows = merge_cisco_counter_tables(
                parse_cisco_counters(outputs.get("counters", "")),
                parse_cisco_counter_errors(outputs.get("errors", "")),
                status_map,
            )

        logger.info(
            "[SSH-STATS] Collected %d interface(s) | host=%s",
            len(rows),
            self.credentials.host,
        )
        if not rows:
            raise SSHCollectorError(
                f"No interface counters parsed via SSH from {self.credentials.host}"
            )
        return rows


def collect_ssh_interface_stats(device: dict) -> list[dict]:
    """Resolve credentials and collect SSH interface stats for a device."""
    credentials = resolve_ssh_credentials(device)
    return SSHStatsCollector(credentials).collect_interface_stats()


# ---------------------------------------------------------------------------
# Cisco parsers
# ---------------------------------------------------------------------------

def parse_cisco_counters(output: str) -> dict[str, dict[str, int]]:
    """
    Parse ``show interfaces counters``.

    Returns map of port → {rx_bytes, tx_bytes, rx_packets, tx_packets,
    multicast_packets, broadcast_packets}.
    """
    result: dict[str, dict[str, int]] = {}
    if not output:
        return result

    section = None  # "in" | "out"
    for line in output.replace("\r", "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        lower = stripped.lower()
        if lower.startswith("port") and "inoctets" in lower.replace(" ", ""):
            section = "in"
            continue
        if lower.startswith("port") and "outoctets" in lower.replace(" ", ""):
            section = "out"
            continue
        if stripped.startswith("-") or stripped.endswith("#") or stripped.endswith(">"):
            continue

        parts = stripped.split()
        if len(parts) < 5:
            continue
        port = parts[0]
        if port.lower() == "port":
            continue

        try:
            nums = [int(p.replace(",", "")) for p in parts[1:5]]
        except ValueError:
            continue

        entry = result.setdefault(port, {
            "rx_bytes": 0,
            "tx_bytes": 0,
            "rx_packets": 0,
            "tx_packets": 0,
            "rx_multicast_packets": 0,
            "tx_multicast_packets": 0,
            "rx_broadcast_packets": 0,
            "tx_broadcast_packets": 0,
            "multicast_packets": 0,
            "broadcast_packets": 0,
        })

        if section == "in":
            # InOctets InUcastPkts InMcastPkts InBcastPkts
            entry["rx_bytes"] = nums[0]
            entry["rx_packets"] = nums[1] + nums[2] + nums[3]
            entry["rx_multicast_packets"] = nums[2]
            entry["rx_broadcast_packets"] = nums[3]
            entry["multicast_packets"] += nums[2]
            entry["broadcast_packets"] += nums[3]
        elif section == "out":
            entry["tx_bytes"] = nums[0]
            entry["tx_packets"] = nums[1] + nums[2] + nums[3]
            entry["tx_multicast_packets"] = nums[2]
            entry["tx_broadcast_packets"] = nums[3]
            entry["multicast_packets"] += nums[2]
            entry["broadcast_packets"] += nums[3]
        else:
            # Heuristic single-table layout
            entry["rx_bytes"] = nums[0]
            entry["rx_packets"] = nums[1]

    return result


def parse_cisco_counter_errors(output: str) -> dict[str, dict[str, int]]:
    """
    Parse ``show interfaces counters errors``.

    Typical columns:
      Port Align-Err FCS-Err Xmit-Err Rcv-Err UnderSize OutDiscards
      ... or InErrs / OutErrs variants
    """
    result: dict[str, dict[str, int]] = {}
    if not output:
        return result

    header_cols: list[str] = []
    for line in output.replace("\r", "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        lower = stripped.lower()
        if lower.startswith("port") and ("err" in lower or "discard" in lower):
            header_cols = [c.lower() for c in stripped.split()]
            continue
        if stripped.startswith("-") or stripped.endswith("#") or stripped.endswith(">"):
            continue

        parts = stripped.split()
        if len(parts) < 2 or parts[0].lower() == "port":
            continue

        port = parts[0]
        values: list[int] = []
        for token in parts[1:]:
            try:
                values.append(int(token.replace(",", "")))
            except ValueError:
                values.append(0)

        input_errors = 0
        output_errors = 0
        rx_discards = 0
        tx_discards = 0
        discards = 0

        if header_cols:
            # Map by header names when available
            col_map = {
                header_cols[i]: values[i - 1]
                for i in range(1, min(len(header_cols), len(values) + 1))
            }
            input_errors = (
                col_map.get("rcv-err", 0)
                + col_map.get("align-err", 0)
                + col_map.get("fcs-err", 0)
                + col_map.get("inerrs", 0)
                + col_map.get("undersize", 0)
            )
            output_errors = (
                col_map.get("xmit-err", 0)
                + col_map.get("outerrs", 0)
            )
            rx_discards = col_map.get("indiscards", 0)
            tx_discards = col_map.get("outdiscards", 0)
            discards = rx_discards + tx_discards
            if discards == 0:
                discards = col_map.get("discards", 0)
        elif len(values) >= 6:
            # Align FCS Xmit Rcv UnderSize OutDiscards
            input_errors = values[0] + values[1] + values[3] + values[4]
            output_errors = values[2]
            tx_discards = values[5]
            discards = tx_discards
        elif len(values) >= 2:
            input_errors = values[0]
            output_errors = values[1]

        result[port] = {
            "input_errors": input_errors,
            "output_errors": output_errors,
            "rx_discards": rx_discards if rx_discards or tx_discards else None,
            "tx_discards": tx_discards if rx_discards or tx_discards else None,
            "discards": discards,
        }

    return result


def parse_cisco_status_map(output: str) -> dict[str, dict[str, Any]]:
    """
    Parse ``show interfaces status`` into port → operational fields.

    Returns map of port → {admin_status, oper_status, speed_bps, status}.
    Speed is omitted when auto / unknown so callers can fall back to inventory.
    """
    result: dict[str, dict[str, Any]] = {}
    if not output:
        return result

    # Cisco IOS/XE tabular status (Name may contain spaces / be empty).
    status_re = re.compile(
        r"^(?P<port>\S+)\s+"
        r"(?P<name>.*?)\s+"
        r"(?P<status>connected|notconnect|disabled|err-disabled|"
        r"monitoring|inactive|up|down)\s+"
        r"(?P<vlan>\S+)\s+"
        r"(?P<duplex>\S+)\s+"
        r"(?P<speed>\S+)\s+"
        r"(?P<type>\S.*)?$",
        re.IGNORECASE,
    )

    for line in output.replace("\r", "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("port"):
            continue

        match = status_re.match(stripped)
        if match:
            port = match.group("port")
            status = match.group("status").lower()
            admin_status, oper_status = _cisco_link_status_to_admin_oper(status)
            entry: dict[str, Any] = {
                "status": status,
                "admin_status": admin_status,
                "oper_status": oper_status,
            }
            bps = _speed_token_to_bps(match.group("speed"))
            if bps:
                entry["speed_bps"] = bps
            result[port] = entry
            continue

        # Fallback for odd layouts: infer status + speed tokens when possible.
        parts = stripped.split()
        if len(parts) < 5:
            continue
        port = parts[0]
        status_token = None
        for token in parts[1:]:
            tl = token.lower()
            if tl in (
                "connected", "notconnect", "disabled", "err-disabled",
                "errdisabled", "monitoring", "inactive", "up", "down",
            ):
                status_token = tl
                break
        if not status_token:
            continue
        admin_status, oper_status = _cisco_link_status_to_admin_oper(status_token)
        entry = {
            "status": status_token,
            "admin_status": admin_status,
            "oper_status": oper_status,
        }
        speed_token = _guess_speed_token(parts)
        bps = _speed_token_to_bps(speed_token) if speed_token else None
        if bps:
            entry["speed_bps"] = bps
        result[port] = entry
    return result


def parse_cisco_speed_map(output: str) -> dict[str, int]:
    """
    Extract port → speed_bps from ``show interfaces status``.

    Parses the Speed column explicitly — never treats the VLAN id as bandwidth.
    When Speed is ``auto`` / ``a-auto`` / ``-``, the port is omitted so callers
    can fall back to inventory / SNMP negotiated speed.
    """
    speeds: dict[str, int] = {}
    for port, entry in parse_cisco_status_map(output).items():
        bps = entry.get("speed_bps")
        if bps:
            speeds[port] = int(bps)
    return speeds


def _cisco_link_status_to_admin_oper(status: str) -> tuple[str, str]:
    """
    Map Cisco ``show interfaces status`` Status column to inventory fields.

    Mirrors discovery normalizer status-only mapping so stats refresh stays
    consistent with SSH inventory semantics.
    """
    text = (status or "").strip().lower()
    if text == "disabled":
        return "down", "down"
    if text in ("connected", "up"):
        return "up", "up"
    if text in (
        "notconnect", "err-disabled", "errdisabled", "monitoring",
        "inactive", "down",
    ):
        return "up", "down"
    return "unknown", "unknown"


def _guess_speed_token(parts: list[str]) -> str | None:
    """Best-effort Speed column when the primary regex does not match."""
    # Prefer tokens that look like negotiated speeds (a-1000, 1000, 10G…).
    candidates: list[str] = []
    for token in parts[1:]:
        tl = token.lower()
        if tl in ("auto", "a-auto", "-"):
            candidates.append(token)
            continue
        if re.match(r"^a?-?\d+(?:\.\d+)?g?$", tl):
            # Skip likely VLAN-only bare small integers when a better token exists
            bare = tl.lstrip("a-")
            if bare.isdigit() and int(bare) <= 4094 and "g" not in tl and not tl.startswith("a"):
                continue
            candidates.append(token)
    if not candidates:
        return None
    # Prefer the last negotiated/auto token (Speed is near the end of the row)
    return candidates[-1]


def merge_cisco_counter_tables(
    counters: dict[str, dict[str, int]],
    errors: dict[str, dict[str, int]],
    status_or_speeds: dict[str, Any] | None = None,
) -> list[dict]:
    """
    Merge counter / error / status tables into raw stats rows.

    ``status_or_speeds`` accepts either:
    - parse_cisco_status_map() output (preferred — includes admin/oper), or
    - parse_cisco_speed_map() output (legacy speed-only dict[str, int]).
    """
    status_map = status_or_speeds or {}
    names = set(counters) | set(errors)
    rows: list[dict] = []
    for name in sorted(names):
        c = counters.get(name, {})
        e = errors.get(name, {})
        status_entry = status_map.get(name)

        speed_bps = None
        admin_status = None
        oper_status = None
        if isinstance(status_entry, dict):
            speed_bps = status_entry.get("speed_bps")
            admin_status = status_entry.get("admin_status")
            oper_status = status_entry.get("oper_status")
        elif isinstance(status_entry, int):
            speed_bps = status_entry

        row: dict[str, Any] = {
            "name": name,
            "if_index": None,
            "rx_bytes": c.get("rx_bytes", 0),
            "tx_bytes": c.get("tx_bytes", 0),
            "rx_packets": c.get("rx_packets", 0),
            "tx_packets": c.get("tx_packets", 0),
            "rx_broadcast_packets": c.get("rx_broadcast_packets"),
            "tx_broadcast_packets": c.get("tx_broadcast_packets"),
            "rx_multicast_packets": c.get("rx_multicast_packets"),
            "tx_multicast_packets": c.get("tx_multicast_packets"),
            "broadcast_packets": c.get("broadcast_packets", 0),
            "multicast_packets": c.get("multicast_packets", 0),
            "input_errors": e.get("input_errors", 0),
            "output_errors": e.get("output_errors", 0),
            "rx_discards": e.get("rx_discards"),
            "tx_discards": e.get("tx_discards"),
            "discards": e.get("discards", 0),
            "speed_bps": speed_bps,
        }
        if admin_status:
            row["admin_status"] = admin_status
        if oper_status:
            row["oper_status"] = oper_status
        rows.append(row)
    return rows


def parse_juniper_statistics(output: str) -> list[dict]:
    """Best-effort parse of ``show interfaces statistics`` blocks."""
    rows: list[dict] = []
    if not output:
        return rows

    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if current and current.get("name"):
            rows.append(current)
        current = None

    for line in output.replace("\r", "").splitlines():
        stripped = line.strip()
        name_match = re.match(r"^Physical interface:\s+(\S+)", stripped, re.I)
        if name_match:
            flush()
            current = {
                "name": name_match.group(1).rstrip(","),
                "if_index": None,
                "rx_bytes": 0,
                "tx_bytes": 0,
                "rx_packets": 0,
                "tx_packets": 0,
                "broadcast_packets": 0,
                "multicast_packets": 0,
                "input_errors": 0,
                "output_errors": 0,
                "discards": 0,
                "speed_bps": None,
            }
            # Typical: "Physical interface: ge-0/0/0, Enabled, Physical link is Up"
            admin_status, oper_status = _parse_juniper_link_flags(stripped)
            if admin_status:
                current["admin_status"] = admin_status
            if oper_status:
                current["oper_status"] = oper_status
            continue
        if not current:
            continue

        speed_match = re.search(r"Speed:\s+(\S+)", stripped, re.I)
        if speed_match:
            current["speed_bps"] = _speed_token_to_bps(speed_match.group(1))

        # Input packets: 123  Output packets: 456
        m = re.search(
            r"Input packets:\s+([\d,]+).*Output packets:\s+([\d,]+)",
            stripped,
            re.I,
        )
        if m:
            current["rx_packets"] = int(m.group(1).replace(",", ""))
            current["tx_packets"] = int(m.group(2).replace(",", ""))

        m = re.search(
            r"Input bytes:\s+([\d,]+).*Output bytes:\s+([\d,]+)",
            stripped,
            re.I,
        )
        if m:
            current["rx_bytes"] = int(m.group(1).replace(",", ""))
            current["tx_bytes"] = int(m.group(2).replace(",", ""))

        m = re.search(r"Input errors:\s+(\d+)", stripped, re.I)
        if m:
            current["input_errors"] = int(m.group(1))
        m = re.search(r"Output errors:\s+(\d+)", stripped, re.I)
        if m:
            current["output_errors"] = int(m.group(1))

    flush()
    return rows


def _parse_juniper_link_flags(line: str) -> tuple[str | None, str | None]:
    """Extract admin/oper from a Juniper Physical interface header line."""
    admin_status = None
    oper_status = None
    lower = line.lower()
    if re.search(r"\bdisabled\b", lower):
        admin_status = "down"
    elif re.search(r"\benabled\b", lower):
        admin_status = "up"
    link = re.search(r"physical link is\s+(\w+)", lower)
    if link:
        token = link.group(1)
        if token == "up":
            oper_status = "up"
        elif token in ("down", "absent"):
            oper_status = "down"
    return admin_status, oper_status


def _speed_token_to_bps(token: str) -> int | None:
    text = (token or "").strip().lower().replace(" ", "")
    text = re.sub(r"^a-", "", text)
    if text in ("auto", "a-auto", "-", "unknown", ""):
        return None
    match = re.match(r"^(\d+(?:\.\d+)?)(g|gbps|gb|m|mbps|mb)?$", text)
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "m").lower()
    if unit in ("g", "gbps", "gb"):
        return int(value * 1_000_000_000)
    if unit in ("m", "mbps", "mb"):
        return int(value * 1_000_000)
    # bare number from Cisco status Speed column → Mbps (1000, 100, 10)
    return int(value * 1_000_000)
