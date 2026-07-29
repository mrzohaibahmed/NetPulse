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
            rows = merge_cisco_counter_tables(
                parse_cisco_counters(outputs.get("counters", "")),
                parse_cisco_counter_errors(outputs.get("errors", "")),
                parse_cisco_speed_map(outputs.get("status", "")),
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


def parse_cisco_speed_map(output: str) -> dict[str, int]:
    """Extract port → speed_bps from ``show interfaces status`` Speed column."""
    speeds: dict[str, int] = {}
    if not output:
        return speeds

    for line in output.replace("\r", "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("port"):
            continue
        parts = stripped.split()
        if len(parts) < 5:
            continue
        port = parts[0]
        # Speed is typically second-to-last before Type, or after duplex
        # Reuse a light heuristic: look for a-1000 / 1000 / 10G tokens
        speed_token = ""
        for token in parts:
            tl = token.lower()
            if re.match(r"^a?-?\d+g?$", tl) or tl in ("auto", "a-auto"):
                # Prefer numeric speed-looking tokens that aren't vlan ids early
                if "g" in tl or tl.lstrip("a-").isdigit():
                    speed_token = token
        if not speed_token:
            continue
        bps = _speed_token_to_bps(speed_token)
        if bps:
            speeds[port] = bps
    return speeds


def merge_cisco_counter_tables(
    counters: dict[str, dict[str, int]],
    errors: dict[str, dict[str, int]],
    speeds: dict[str, int],
) -> list[dict]:
    names = set(counters) | set(errors)
    rows: list[dict] = []
    for name in sorted(names):
        c = counters.get(name, {})
        e = errors.get(name, {})
        rows.append({
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
            "speed_bps": speeds.get(name),
        })
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
                "name": name_match.group(1),
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


def _speed_token_to_bps(token: str) -> int | None:
    text = (token or "").strip().lower().replace(" ", "")
    text = re.sub(r"^a-", "", text)
    if text in ("auto", "a-auto", "-", "unknown"):
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
    # bare number from Cisco status → Mbps
    return int(value * 1_000_000)
