"""
parser.py
=========
Parse vendor CLI output into loosely-structured interface dicts.

The parser is intentionally transport-agnostic: it accepts raw command-output
strings (from SSH today, SNMP/other collectors later) and returns a list of
dicts that ``normalizer.normalize_raw_interface`` can consume.
"""

from __future__ import annotations

import re
from typing import Iterable

from services.interface_collection.naming import (
    canonicalize_interface_name,
    normalize_storage_interface_name,
)
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("interface")


def expand_cisco_name(short_name: str) -> str:
    """
    Normalise interface names to a stable short storage form.

    Gi1/0/1, Gig1/0/1, and GigabitEthernet1/0/1 all become Gi1/0/1.
    """
    return normalize_storage_interface_name(short_name)


# ---------------------------------------------------------------------------
# Cisco IOS / IOS-XE
# ---------------------------------------------------------------------------

def parse_cisco_interfaces_status(output: str) -> list[dict]:
    """
    Parse ``show interfaces status`` / ``show interface status``.

    Typical columns:
      Port  Name  Status  Vlan  Duplex  Speed  Type
    """
    interfaces: list[dict] = []
    if not output:
        return interfaces

    # Skip until header line containing Port / Status
    lines = output.replace("\r", "").splitlines()
    data_started = False

    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            continue
        if re.search(r"^\s*Port\s+Name\s+Status", stripped, re.I):
            data_started = True
            continue
        if not data_started:
            # Some devices omit the header; detect data rows heuristically.
            if re.match(r"^(Gi|Te|Fa|Et|Po|Vl|Hu|Tw|Fo)\S+\s+", stripped, re.I):
                data_started = True
            else:
                continue

        # Ignore separators / prompts
        if stripped.startswith("-") or stripped.endswith("#") or stripped.endswith(">"):
            continue

        parsed = _parse_cisco_status_row(stripped)
        if parsed:
            interfaces.append(parsed)

    logger.debug("[IFACE] Parsed %d row(s) from show interfaces status", len(interfaces))
    return interfaces


def _parse_cisco_status_row(line: str) -> dict | None:
    """
    Split a status row. Name column may contain spaces, so we anchor on the
    known Status tokens and work outwards.
    """
    # Port is the first token
    match = re.match(r"^(\S+)\s+(.*)$", line)
    if not match:
        return None

    port = match.group(1)
    rest = match.group(2).strip()

    # Skip header echo / non-port lines
    if port.lower() in ("port", "interface"):
        return None

    status_tokens = (
        "connected", "notconnect", "disabled", "err-disabled", "errdisabled",
        "monitoring", "suspended", "inactive", "up", "down",
    )
    status_re = re.compile(
        r"^(?P<name>.*?)\s+(?P<status>" + "|".join(status_tokens) + r")\s+"
        r"(?P<vlan>\S+)\s+(?P<duplex>\S+)\s+(?P<speed>\S+)(?:\s+(?P<type>.*))?$",
        re.I,
    )
    m = status_re.match(rest)
    if not m:
        # Fallback: whitespace-split from the right for vlan/duplex/speed/type
        parts = rest.split()
        if len(parts) < 4:
            return None
        # [name...] status vlan duplex speed [type...]
        # Find status token
        status_idx = None
        for i, part in enumerate(parts):
            if part.lower() in status_tokens:
                status_idx = i
                break
        if status_idx is None or status_idx + 3 >= len(parts):
            return None
        name = " ".join(parts[:status_idx]).strip()
        status = parts[status_idx]
        vlan = parts[status_idx + 1]
        duplex = parts[status_idx + 2]
        speed = parts[status_idx + 3]
    else:
        name = (m.group("name") or "").strip()
        status = m.group("status")
        vlan = m.group("vlan")
        duplex = m.group("duplex")
        speed = m.group("speed")

    mode = "unknown"
    vlan_lower = vlan.lower()
    if vlan_lower == "trunk":
        mode = "trunk"
    elif vlan_lower == "routed":
        mode = "routed"
    elif vlan.isdigit():
        mode = "access"

    return {
        "name": expand_cisco_name(port),
        "description": name,
        "status": status.lower(),
        "vlan": vlan,
        "mode": mode,
        "duplex": duplex,
        "speed": speed,
        "vendor": "cisco",
    }


def parse_cisco_interfaces_description(output: str) -> dict[str, dict]:
    """
    Parse ``show interfaces description``.

    Returns a map of interface name → {admin_status, oper_status, description}.
    """
    result: dict[str, dict] = {}
    if not output:
        return result

    lines = output.replace("\r", "").splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("interface"):
            continue
        if stripped.endswith("#") or stripped.endswith(">"):
            continue

        # Interface  Status  Protocol  Description
        match = re.match(
            r"^(\S+)\s+(up|down|admin\s+down|administratively down)\s+"
            r"(up|down)\s*(.*)$",
            stripped,
            re.I,
        )
        if not match:
            continue

        name = expand_cisco_name(match.group(1))
        admin = match.group(2).lower().replace("administratively down", "down").replace("admin down", "down")
        oper = match.group(3).lower()
        desc = (match.group(4) or "").strip()

        result[name] = {
            "admin_status": "down" if "down" in admin else "up",
            "oper_status": oper,
            "description": desc,
        }

    return result


def parse_cisco_switchport(output: str) -> dict[str, dict]:
    """
    Parse ``show interfaces switchport`` (multi-interface block output).

    Returns map of interface name → {
      mode, access_vlan, native_vlan, allowed_vlans, trunk_vlans_all,
      voice_vlan, switchport_enabled
    }
    """
    result: dict[str, dict] = {}
    if not output:
        return result

    current: str | None = None
    current_data: dict = {}

    def _flush() -> None:
        nonlocal current, current_data
        if current:
            result[current] = current_data
        current = None
        current_data = {}

    for line in output.replace("\r", "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        name_match = re.match(r"^Name:\s+(\S+)", stripped, re.I)
        if name_match:
            _flush()
            current = expand_cisco_name(name_match.group(1))
            current_data = {
                "mode": "unknown",
                "access_vlan": None,
                "native_vlan": None,
                "allowed_vlans": [],
                "trunk_vlans_all": False,
                "voice_vlan": None,
                "switchport_enabled": True,
            }
            continue

        if current is None:
            continue

        switchport_match = re.match(r"^Switchport:\s+(Enabled|Disabled)", stripped, re.I)
        if switchport_match:
            enabled = switchport_match.group(1).lower() == "enabled"
            current_data["switchport_enabled"] = enabled
            if not enabled:
                current_data["mode"] = "routed"
            continue

        # Prefer Operational Mode; fall back to Administrative Mode.
        oper_mode = re.match(r"^Operational Mode:\s+(.+)$", stripped, re.I)
        if oper_mode:
            current_data["mode"] = oper_mode.group(1).strip().lower()
            continue

        admin_mode = re.match(r"^Administrative Mode:\s+(.+)$", stripped, re.I)
        if admin_mode and current_data.get("mode") in ("unknown", "", None):
            current_data["mode"] = admin_mode.group(1).strip().lower()
            continue

        access_match = re.match(
            r"^Access Mode VLAN:\s+(\d+|unassigned|none)",
            stripped,
            re.I,
        )
        if access_match:
            token = access_match.group(1)
            if token.isdigit():
                current_data["access_vlan"] = int(token)
            continue

        native_match = re.match(
            r"^Trunking Native Mode VLAN:\s+(\d+)",
            stripped,
            re.I,
        )
        if native_match:
            current_data["native_vlan"] = int(native_match.group(1))
            continue

        trunk_match = re.match(
            r"^Trunking VLANs Enabled:\s+(.+)$",
            stripped,
            re.I,
        )
        if trunk_match:
            raw_vlans = trunk_match.group(1).strip()
            if raw_vlans.upper() in ("ALL", "1-4094", "1-4095"):
                current_data["trunk_vlans_all"] = True
                current_data["allowed_vlans"] = []
            else:
                current_data["trunk_vlans_all"] = False
                current_data["allowed_vlans"] = expand_vlan_list(raw_vlans)
            continue

        voice_match = re.match(r"^Voice VLAN:\s+(\d+|none)", stripped, re.I)
        if voice_match and voice_match.group(1).isdigit():
            current_data["voice_vlan"] = int(voice_match.group(1))

    _flush()
    return result


def expand_vlan_list(raw: str) -> list[int]:
    """
    Expand Cisco VLAN lists such as ``10,20,30-40`` into sorted unique ints.
    """
    vlans: set[int] = set()
    text = (raw or "").strip()
    if not text or text.upper() in ("NONE", "N/A", "-", ""):
        return []

    for part in re.split(r"[,\s]+", text):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            ends = part.split("-", 1)
            if len(ends) == 2 and ends[0].isdigit() and ends[1].isdigit():
                start, end = int(ends[0]), int(ends[1])
                if start > end:
                    start, end = end, start
                # Cap expansion to a sane range for storm-protection consumers
                if end - start > 4094:
                    continue
                vlans.update(range(start, end + 1))
            continue
        if part.isdigit():
            vlans.add(int(part))

    return sorted(v for v in vlans if 1 <= v <= 4094)


def parse_cisco_vlan_brief(output: str) -> list[int]:
    """Return VLAN IDs from ``show vlan brief`` (for expanding trunk ALL)."""
    ids: list[int] = []
    if not output:
        return ids

    for line in output.replace("\r", "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("vlan"):
            continue
        match = re.match(r"^(\d+)\s+", stripped)
        if match:
            vlan_id = int(match.group(1))
            if 1 <= vlan_id <= 4094:
                ids.append(vlan_id)
    return sorted(set(ids))


def _split_capabilities(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text or text.lower() in ("none", "n/a", "-"):
        return []
    parts = re.split(r"[,/\s]+", text)
    return [p.strip() for p in parts if p.strip()]


def parse_cisco_cdp_neighbors(output: str) -> dict[str, dict]:
    """
    Parse ``show cdp neighbors detail``.

    Returns map of local interface → enriched neighbor dict.
    """
    result: dict[str, dict] = {}
    if not output or re.search(r"%\s*CDP.*not|CDP is not enabled", output, re.I):
        return result

    blocks = re.split(r"(?=\n\s*-{5,}|\nDevice ID:)", "\n" + output.replace("\r", ""))
    for block in blocks:
        if "Device ID:" not in block and "device id:" not in block.lower():
            continue

        device_id = ""
        local_intf = ""
        remote_port = ""
        platform = ""
        ip_address = ""
        capabilities: list[str] = []
        version = ""
        collecting_version = False
        version_lines: list[str] = []

        for line in block.splitlines():
            stripped = line.strip()
            m = re.match(r"^Device ID:\s*(.+)$", stripped, re.I)
            if m:
                device_id = m.group(1).strip()
                collecting_version = False
                continue
            m = re.match(
                r"^Interface:\s*([^,]+),\s*Port ID \(outgoing port\):\s*(.+)$",
                stripped,
                re.I,
            )
            if m:
                local_intf = expand_cisco_name(m.group(1).strip())
                remote_port = m.group(2).strip()
                collecting_version = False
                continue
            m = re.match(r"^Platform:\s*([^,]+)", stripped, re.I)
            if m:
                platform = m.group(1).strip()
                cap_m = re.search(r"Capabilities:\s*(.+)$", stripped, re.I)
                if cap_m:
                    capabilities = _split_capabilities(cap_m.group(1))
                collecting_version = False
                continue
            m = re.match(r"^Capabilities:\s*(.+)$", stripped, re.I)
            if m:
                capabilities = _split_capabilities(m.group(1))
                collecting_version = False
                continue
            m = re.search(r"IP address:\s*([\d.]+)", stripped, re.I)
            if m and not ip_address:
                ip_address = m.group(1)
                collecting_version = False
                continue
            m = re.match(r"^Version\s*:\s*(.*)$", stripped, re.I)
            if m:
                first = (m.group(1) or "").strip()
                version_lines = [first] if first else []
                collecting_version = True
                continue
            if collecting_version:
                if re.match(
                    r"^(advertisement|Native VLAN|Duplex|Management address|"
                    r"Interface:|Platform:|Device ID:|----------------)",
                    stripped,
                    re.I,
                ):
                    collecting_version = False
                elif stripped:
                    version_lines.append(stripped)

        if version_lines:
            version = " ".join(version_lines).strip()

        if local_intf and device_id:
            result[local_intf] = {
                "hostname": device_id.split("(")[0].strip(),
                "interface": remote_port,
                "port": remote_port,
                "protocol": "cdp",
                "platform": platform,
                "ip": ip_address,
                "management_address": ip_address,
                "system_description": version,
                "capabilities": capabilities,
            }

    return result


def parse_cisco_lldp_neighbors(output: str) -> dict[str, dict]:
    """
    Parse ``show lldp neighbors detail``.

    Extracts System Name, Port id/Description, Management Address,
    System Description, and System Capabilities.
    """
    result: dict[str, dict] = {}
    if not output or re.search(r"%\s*LLDP.*not|LLDP is not enabled", output, re.I):
        return result

    blocks = re.split(r"(?=Local Intf:|Local Port id:)", output.replace("\r", ""), flags=re.I)
    for block in blocks:
        local_intf = ""
        hostname = ""
        remote_port = ""
        port_description = ""
        management_address = ""
        system_description = ""
        capabilities: list[str] = []
        collecting_desc = False
        desc_lines: list[str] = []

        for line in block.splitlines():
            stripped = line.strip()
            m = re.match(r"^Local Intf:\s*(\S+)", stripped, re.I)
            if m:
                local_intf = expand_cisco_name(m.group(1))
                collecting_desc = False
                continue
            m = re.match(r"^Local Port id:\s*(\S+)", stripped, re.I)
            if m and not local_intf:
                local_intf = expand_cisco_name(m.group(1))
                collecting_desc = False
                continue
            m = re.match(r"^System Name:\s*(.+)$", stripped, re.I)
            if m:
                hostname = m.group(1).strip()
                collecting_desc = False
                continue
            m = re.match(r"^Port id:\s*(.+)$", stripped, re.I)
            if m:
                remote_port = m.group(1).strip()
                collecting_desc = False
                continue
            m = re.match(r"^Port Description:\s*(.*)$", stripped, re.I)
            if m:
                port_description = (m.group(1) or "").strip()
                collecting_desc = False
                continue
            m = re.match(
                r"^System Capabilities:\s*(.+)$",
                stripped,
                re.I,
            )
            if m:
                capabilities = _split_capabilities(m.group(1))
                collecting_desc = False
                continue
            m = re.match(
                r"^Enabled Capabilities:\s*(.+)$",
                stripped,
                re.I,
            )
            if m and not capabilities:
                capabilities = _split_capabilities(m.group(1))
                collecting_desc = False
                continue
            m = re.match(
                r"^Management Address(?:es)?\s*:\s*(.*)$",
                stripped,
                re.I,
            )
            if m:
                addr = (m.group(1) or "").strip()
                if addr:
                    ip_m = re.search(r"([\d.]+|[A-Fa-f0-9:]+)", addr)
                    if ip_m:
                        management_address = ip_m.group(1)
                collecting_desc = False
                continue
            # Nested "IP: x.x.x.x" under Management Addresses
            m = re.match(r"^IP:\s*([\d.]+)", stripped, re.I)
            if m and not management_address:
                management_address = m.group(1)
                collecting_desc = False
                continue
            m = re.match(r"^System Description:\s*(.*)$", stripped, re.I)
            if m:
                first = (m.group(1) or "").strip()
                desc_lines = [first] if first else []
                collecting_desc = True
                continue
            if collecting_desc:
                # Stop at next known LLDP key
                if re.match(
                    r"^(Time remaining|System Capabilities|Enabled Capabilities|"
                    r"Management Address|Chassis id|Port id|Port Description|"
                    r"System Name|Local Intf|----------------)",
                    stripped,
                    re.I,
                ):
                    collecting_desc = False
                elif stripped:
                    desc_lines.append(stripped)

        if desc_lines:
            system_description = " ".join(desc_lines).strip()

        remote_interface = remote_port or port_description
        if local_intf and (hostname or remote_interface):
            result[local_intf] = {
                "hostname": hostname,
                "interface": remote_interface,
                "port": remote_interface,
                "protocol": "lldp",
                "platform": "",
                "ip": management_address,
                "management_address": management_address,
                "system_description": system_description,
                "capabilities": capabilities,
            }

    return result


def _merge_neighbor_records(primary: dict, secondary: dict) -> dict:
    """Prefer ``primary`` values; fill blanks from ``secondary`` (CDP over LLDP)."""
    merged = dict(secondary)
    for key, value in primary.items():
        if value is None or value == "" or value == []:
            continue
        merged[key] = value
    # Prefer primary protocol when it contributed core identity
    if primary.get("hostname") or primary.get("interface") or primary.get("port"):
        merged["protocol"] = primary.get("protocol") or merged.get("protocol")
    return merged


def merge_neighbor_maps(
    cdp: dict[str, dict],
    lldp: dict[str, dict],
) -> dict[str, dict]:
    """
    Prefer CDP; use LLDP when CDP is missing; enrich CDP blanks from LLDP.
    """
    neighbors: dict[str, dict] = {k: dict(v) for k, v in cdp.items()}

    for local_intf, info in lldp.items():
        existing_key = None
        if local_intf in neighbors:
            existing_key = local_intf
        else:
            canon = canonicalize_interface_name(local_intf)
            for key in neighbors:
                if canonicalize_interface_name(key) == canon:
                    existing_key = key
                    break

        if existing_key is None:
            neighbors[local_intf] = dict(info)
        else:
            neighbors[existing_key] = _merge_neighbor_records(
                neighbors[existing_key],
                info,
            )

    return neighbors


def merge_cisco_parsed(
    status_rows: list[dict],
    descriptions: dict[str, dict] | None = None,
    switchports: dict[str, dict] | None = None,
    neighbors: dict[str, dict] | None = None,
    vlan_ids: list[int] | None = None,
) -> list[dict]:
    """Merge Cisco parse products into a single list of raw interface dicts."""
    descriptions = descriptions or {}
    switchports = switchports or {}
    neighbors = neighbors or {}
    known_vlans = vlan_ids or []

    by_name: dict[str, dict] = {}
    for row in status_rows:
        name = row.get("name") or ""
        if not name:
            continue
        by_name[name] = dict(row)

    all_names = set(by_name) | set(descriptions) | set(switchports) | set(neighbors)

    merged: list[dict] = []
    for name in sorted(all_names):
        item = by_name.get(name, {"name": name, "vendor": "cisco"})
        desc = descriptions.get(name) or _fuzzy_get(descriptions, name) or {}
        sw = switchports.get(name) or _fuzzy_get(switchports, name) or {}
        neigh = neighbors.get(name) or _fuzzy_get(neighbors, name) or {}

        if desc:
            if desc.get("description"):
                item["description"] = desc["description"]
            item["admin_status"] = desc.get("admin_status", item.get("admin_status"))
            item["oper_status"] = desc.get("oper_status", item.get("oper_status"))

        if sw:
            if sw.get("mode"):
                item["mode"] = sw["mode"]
            item["access_vlan"] = sw.get("access_vlan")
            item["native_vlan"] = sw.get("native_vlan")
            item["voice_vlan"] = sw.get("voice_vlan")
            item["trunk_vlans_all"] = bool(sw.get("trunk_vlans_all"))

            allowed = list(sw.get("allowed_vlans") or [])
            if sw.get("trunk_vlans_all") and known_vlans:
                allowed = list(known_vlans)
            item["allowed_vlans"] = allowed

            # Compatibility vlan display field
            mode_lower = str(sw.get("mode") or "").lower()
            if "trunk" in mode_lower:
                item["vlan"] = "trunk"
            elif "access" in mode_lower and sw.get("access_vlan") is not None:
                item["vlan"] = str(sw["access_vlan"])
            elif sw.get("mode") == "routed":
                item["vlan"] = ""

        if neigh:
            item["neighbor"] = {
                "hostname": neigh.get("hostname") or "",
                "ip": neigh.get("ip") or "",
                "platform": neigh.get("platform") or "",
                "interface": neigh.get("interface") or neigh.get("port") or "",
                "port": neigh.get("port") or neigh.get("interface") or "",
                "protocol": neigh.get("protocol") or "",
                "management_address": (
                    neigh.get("management_address")
                    or neigh.get("managementAddress")
                    or neigh.get("ip")
                    or ""
                ),
                "system_description": (
                    neigh.get("system_description")
                    or neigh.get("systemDescription")
                    or ""
                ),
                "capabilities": list(neigh.get("capabilities") or []),
            }

        item["vendor"] = item.get("vendor") or "cisco"
        merged.append(item)

    return merged


def _fuzzy_get(mapping: dict[str, dict], name: str) -> dict | None:
    """Match interface keys ignoring case / Cisco short vs long names."""
    if name in mapping:
        return mapping[name]
    lower = name.lower()
    for key, value in mapping.items():
        if key.lower() == lower:
            return value

    # Gi1/0/24 ↔ GigabitEthernet1/0/24
    canon = canonicalize_interface_name(name)
    if not canon:
        return None
    for key, value in mapping.items():
        if canonicalize_interface_name(key) == canon:
            return value
    return None


def _cisco_canonical(name: str) -> str:
    """Backward-compatible alias for canonicalize_interface_name."""
    return canonicalize_interface_name(name)


# ---------------------------------------------------------------------------
# Generic / multi-vendor entry point
# ---------------------------------------------------------------------------

def parse_interface_outputs(
    outputs: dict[str, str],
    vendor: str = "cisco",
) -> list[dict]:
    """
    Parse a dict of command → output into raw interface records.

    Parameters
    ----------
    outputs : dict
        Keys are logical command names, e.g.
        ``status``, ``description``, ``switchport``.
    vendor : str
        Vendor hint (``cisco``, ``juniper``, ``aruba``, ``generic``).
    """
    vendor_key = (vendor or "cisco").lower().strip()

    if vendor_key in ("cisco", "cisco_ios", "cisco_xe", "cisco_nxos", "ios", "ios-xe"):
        status_rows = parse_cisco_interfaces_status(outputs.get("status", ""))
        descriptions = parse_cisco_interfaces_description(outputs.get("description", ""))
        switchports = parse_cisco_switchport(outputs.get("switchport", ""))
        vlan_ids = parse_cisco_vlan_brief(outputs.get("vlan_brief", ""))

        # Prefer CDP; fill gaps / enrich blanks with LLDP. Missing either is OK.
        cdp = parse_cisco_cdp_neighbors(outputs.get("cdp", ""))
        lldp = parse_cisco_lldp_neighbors(outputs.get("lldp", ""))
        neighbors = merge_neighbor_maps(cdp, lldp)
        if not neighbors:
            logger.info(
                "[IFACE] No CDP/LLDP neighbors discovered (protocols may be disabled)"
            )

        return merge_cisco_parsed(
            status_rows,
            descriptions,
            switchports,
            neighbors=neighbors,
            vlan_ids=vlan_ids,
        )

    if vendor_key in ("juniper", "juniper_junos", "junos"):
        return parse_juniper_terse(outputs.get("status", "") or outputs.get("terse", ""))

    # Generic fallback: try Cisco status parser (many vendors mimic it)
    logger.info("[IFACE] Using generic/Cisco status parser for vendor=%s", vendor_key)
    return parse_cisco_interfaces_status(
        outputs.get("status", "") or next(iter(outputs.values()), "")
    )


def parse_juniper_terse(output: str) -> list[dict]:
    """
    Parse ``show interfaces terse`` (Juniper Junos).

    Columns: Interface  Admin  Link  Proto  Local  Remote
    """
    interfaces: list[dict] = []
    if not output:
        return interfaces

    for line in output.replace("\r", "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("interface"):
            continue
        parts = stripped.split()
        if len(parts) < 3:
            continue
        name, admin, link = parts[0], parts[1], parts[2]
        if name.endswith("."):  # unit-only continuation
            continue
        interfaces.append({
            "name": expand_cisco_name(name),
            "admin_status": admin.lower(),
            "oper_status": link.lower(),
            "mode": "unknown",
            "vlan": "",
            "speed": "",
            "duplex": "",
            "description": "",
            "vendor": "juniper",
        })

    return interfaces


def dedupe_by_name(interfaces: Iterable[dict]) -> list[dict]:
    """Keep the last occurrence of each canonical interface name."""
    by_canon: dict[str, dict] = {}
    for item in interfaces:
        name = expand_cisco_name(item.get("name") or "")
        if not name:
            continue
        item = dict(item)
        item["name"] = name
        by_canon[canonicalize_interface_name(name)] = item
    return list(by_canon.values())
