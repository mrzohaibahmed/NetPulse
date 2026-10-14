"""Merge Junos interface, switching, configuration, and LLDP CLI output.

Physical interfaces remain inventory rows, including management interfaces.
Logical units enrich their parent rather than becoming duplicate ports.
Optional command failures leave fields unknown instead of guessing.
"""

from __future__ import annotations

import re


_PORT = re.compile(r"^(?:(?:ge|xe|et|fe|mge)-\d+/\d+/\d+(?::\d+)?|ae\d+)$", re.I)
_PHYSICAL_NAME = re.compile(r"^[a-z][\w:/-]*$", re.I)
_VLAN = re.compile(r"^set vlans (\S+) vlan-id (\d+)\b", re.I)
_INTERFACE_CONFIG = re.compile(r"^set interfaces (\S+) (.+)$", re.I)
_PHYSICAL = re.compile(r"^Physical interface:\s*([^,]+)", re.I)
_SWITCHING = re.compile(r"^Interface:\s*([^,\s]+)", re.I)
_LLDP_START = re.compile(r"^LLDP Neighbor Information:", re.I)


def _parent(name: str) -> str:
    return name.strip().split(".", 1)[0]


def _physical(name: str) -> str:
    parent = _parent(name)
    return parent if _PHYSICAL_NAME.fullmatch(parent) else ""


def _port(name: str) -> str:
    parent = _parent(name)
    return parent if _PORT.fullmatch(parent) else ""


def _vlan_id(raw: str, vlan_ids: dict[str, int]) -> int | None:
    name = raw.strip().strip('"')
    if name.isdigit():
        value = int(name)
        return value if 1 <= value <= 4094 else None
    return vlan_ids.get(name.lower())


def _vlan_ids(output: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in output.replace("\r", "").splitlines():
        match = _VLAN.match(line.strip())
        if match and 1 <= int(match.group(2)) <= 4094:
            result[match.group(1).strip('"').lower()] = int(match.group(2))
    return result


def parse_terse(output: str) -> list[dict]:
    """Read physical rows from ``show interfaces terse`` without unit duplicates."""
    rows: list[dict] = []
    for line in output.replace("\r", "").splitlines():
        parts = line.split()
        if len(parts) < 3 or not _PHYSICAL_NAME.fullmatch(parts[0]):
            continue
        if parts[1].lower() not in {"up", "down"} or parts[2].lower() not in {"up", "down"}:
            continue
        rows.append({
            "name": parts[0],
            "admin_status": parts[1].lower(),
            "oper_status": parts[2].lower(),
            "mode": "unknown",
            "vlan": "",
            "speed": "",
            "duplex": "",
            "description": "",
            "vendor": "juniper",
        })
    return rows


def _descriptions(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.replace("\r", "").splitlines():
        match = re.match(r"^\s*(\S+)\s+(?:up|down)\s+(?:up|down)\s+(.+)$", line, re.I)
        if match:
            name = _physical(match.group(1))
            if name and name not in result:
                result[name] = match.group(2).strip()
    return result


def _switching(output: str, vlan_ids: dict[str, int]) -> dict[str, dict]:
    """Read EX/QFX detail; QFX output can omit the explicit Port mode."""
    result: dict[str, dict] = {}
    current = ""
    in_membership = False
    for line in output.replace("\r", "").splitlines():
        stripped = line.strip()
        match = _SWITCHING.match(stripped)
        if match:
            current = _port(match.group(1))
            in_membership = False
            if not current:
                continue
            entry = result.setdefault(current, {"vlans": set(), "untagged": set()})
            mode = re.search(r"\b(?:Port|Interface) mode:\s*(access|trunk|tagged-access)\b", stripped, re.I)
            if mode:
                entry["mode"] = mode.group(1).lower()
            continue
        if not current:
            continue
        qfx_vlans = re.match(r"^(?:Vlans|VLAN members):\s*(.*)$", stripped, re.I)
        if qfx_vlans:
            entry = result[current]
            for member in re.split(r"[,\s]+", qfx_vlans.group(1)):
                member = member.strip()
                if not member:
                    continue
                vlan_id = _vlan_id(member.split("(", 1)[0], vlan_ids)
                if vlan_id is not None:
                    entry["vlans"].add(vlan_id)
                    if "untagged" in member.lower():
                        entry["untagged"].add(vlan_id)
            continue
        if stripped.lower().startswith("vlan membership:"):
            in_membership = True
            continue
        if not in_membership or not stripped:
            continue
        tag = re.search(r"\b802\.1Q Tag:\s*(\d+)\b", stripped, re.I)
        if not tag:
            if ":" in stripped or stripped.lower().startswith("number of"):
                in_membership = False
            continue
        vlan_id = int(tag.group(1))
        if not 1 <= vlan_id <= 4094:
            continue
        result[current]["vlans"].add(vlan_id)
        if re.search(r"\buntagged\b", stripped, re.I):
            result[current]["untagged"].add(vlan_id)
    return result


def _configuration(output: str, vlan_ids: dict[str, int]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for line in output.replace("\r", "").splitlines():
        match = _INTERFACE_CONFIG.match(line.strip())
        if not match:
            continue
        name = _port(match.group(1))
        if not name:
            continue
        rest = match.group(2)
        entry = result.setdefault(name, {})
        mode = re.search(r"\bfamily ethernet-switching (?:interface-mode|port-mode) (access|trunk|tagged-access)\b", rest, re.I)
        if mode:
            entry["mode"] = mode.group(1).lower()
        native = re.search(r"\bnative-vlan-id (\d+)\b", rest, re.I)
        if native and 1 <= int(native.group(1)) <= 4094:
            entry["native_vlan"] = int(native.group(1))
        members = re.search(r"\bfamily ethernet-switching vlan members (.+)$", rest, re.I)
        if members:
            tokens = members.group(1).strip().strip("[]").split()
            known = {_vlan_id(token, vlan_ids) for token in tokens}
            entry.setdefault("vlans", set()).update(v for v in known if v is not None)
            if any(token.lower() == "all" for token in tokens):
                entry["vlans"].update(vlan_ids.values())
        if re.search(r"\bunit \d+ family inet(?:6)?(?:\s|$)", rest, re.I):
            entry["routed"] = True
    return result


def _media(output: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    current = ""
    for line in output.replace("\r", "").splitlines():
        stripped = line.strip()
        physical = _PHYSICAL.match(stripped)
        if physical:
            current = _physical(physical.group(1))
            if current:
                result.setdefault(current, {})
            continue
        if not current:
            continue
        speed = re.search(r"\bSpeed:\s*(\d+(?:\.\d+)?\s*(?:[GMK]bps|[GMK]))\b", stripped, re.I)
        if speed:
            result[current]["speed"] = speed.group(1).replace(" ", "")
        mac = re.search(r"\bCurrent address:\s*([0-9a-f:]{17})\b", stripped, re.I)
        if mac:
            result[current]["mac_address"] = mac.group(1).lower()
        index = re.search(r"\bSNMP ifIndex:\s*(\d+)\b", stripped, re.I)
        if index:
            result[current]["if_index"] = int(index.group(1))
        duplex = re.search(r"\b(?:Duplex|Duplex mode):\s*(full|half)\b", stripped, re.I)
        if duplex:
            result[current]["duplex"] = duplex.group(1).lower()
    return result


def _lldp(output: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    current: dict[str, str] = {}
    local = ""
    collecting_description = False

    def flush() -> None:
        if current.get("interface", "").isdigit() and current.get("port_description"):
            current["interface"] = current["port_description"]
        current.pop("port_description", None)
        if local and (current.get("hostname") or current.get("interface")):
            result.setdefault(local, {**current, "protocol": "lldp"})

    for line in output.replace("\r", "").splitlines() + ["LLDP Neighbor Information:"]:
        stripped = line.strip()
        if _LLDP_START.match(stripped):
            flush()
            current = {}
            local = ""
            collecting_description = False
            continue
        match = re.match(r"^Local Interface\s*:\s*(\S+)", stripped, re.I)
        if match:
            local = _physical(match.group(1))
            collecting_description = False
            continue
        if stripped.lower() in {"system capabilities", "management address", "management info", "organization info"}:
            collecting_description = False
            continue
        match = re.match(r"^([^:]+?)\s*:\s*(.*)$", stripped)
        if match:
            key, value = match.group(1).strip().lower(), match.group(2).strip()
            collecting_description = False
            if key == "system name":
                current["hostname"] = value
            elif key == "port id":
                current["interface"] = value
            elif key == "port description":
                current["port_description"] = value
            elif key == "system description":
                current["system_description"] = value
                collecting_description = True
            elif key in {"management address", "address"}:
                address = re.search(r"(?<![\w:])(?:\d{1,3}\.){3}\d{1,3}(?!\w)", value)
                if address:
                    current["management_address"] = address.group(0)
                    current["ip"] = address.group(0)
            elif key in {"system capabilities", "enabled capabilities", "enabled", "supported"}:
                current["capabilities"] = value
            continue
        if collecting_description and stripped:
            current["system_description"] = (current.get("system_description", "") + " " + stripped).strip()
    return result


def parse_juniper_outputs(outputs: dict[str, str]) -> list[dict]:
    rows = parse_terse(outputs.get("status") or outputs.get("terse") or "")
    if not rows:
        return rows
    descriptions = _descriptions(outputs.get("description", ""))
    vlan_ids = _vlan_ids(outputs.get("vlan_config", ""))
    switching = _switching(outputs.get("switching", ""), vlan_ids)
    config = _configuration(outputs.get("interface_config", ""), vlan_ids)
    neighbors = _lldp(outputs.get("lldp", ""))
    media = _media(outputs.get("media", ""))

    for row in rows:
        name = row["name"]
        row["description"] = descriptions.get(name, "")
        operational = switching.get(name, {})
        configured = config.get(name, {})
        mode = operational.get("mode") or configured.get("mode")
        if not mode and configured.get("routed") and not operational:
            mode = "routed"
        if mode in {"access", "trunk", "routed"}:
            row["mode"] = mode
        vlans = sorted(operational.get("vlans", set()) or configured.get("vlans", set()))
        untagged = sorted(operational.get("untagged", set()))
        if mode == "access" and len(vlans) == 1:
            row["access_vlan"] = vlans[0]
            row["vlan"] = str(vlans[0])
        elif mode == "trunk":
            row["allowed_vlans"] = vlans
            row["vlan"] = "trunk"
            native = configured.get("native_vlan") or (untagged[0] if len(untagged) == 1 else None)
            if native:
                row["native_vlan"] = native
        elif mode == "routed":
            row["vlan"] = ""
        if name in neighbors:
            row["neighbor"] = neighbors[name]
        row.update(media.get(name, {}))
    return rows
