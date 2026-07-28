"""
snmp.py
=======
SNMP IF-MIB / IF-X-MIB collector for interface inventory and statistics.

Prefer 64-bit HC counters (IF-X-MIB) when present; fall back to 32-bit IF-MIB.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("interface.snmp")

# ---------------------------------------------------------------------------
# OID map (numeric, no MIB dependency)
# ---------------------------------------------------------------------------

OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
OID_IF_SPEED = "1.3.6.1.2.1.2.2.1.5"
OID_IF_IN_OCTETS = "1.3.6.1.2.1.2.2.1.10"
OID_IF_IN_UCAST = "1.3.6.1.2.1.2.2.1.11"
OID_IF_IN_NUCAST = "1.3.6.1.2.1.2.2.1.12"
OID_IF_IN_DISCARDS = "1.3.6.1.2.1.2.2.1.13"
OID_IF_IN_ERRORS = "1.3.6.1.2.1.2.2.1.14"
OID_IF_OUT_OCTETS = "1.3.6.1.2.1.2.2.1.16"
OID_IF_OUT_UCAST = "1.3.6.1.2.1.2.2.1.17"
OID_IF_OUT_NUCAST = "1.3.6.1.2.1.2.2.1.18"
OID_IF_OUT_DISCARDS = "1.3.6.1.2.1.2.2.1.19"
OID_IF_OUT_ERRORS = "1.3.6.1.2.1.2.2.1.20"

# IF-X-MIB (64-bit + multicast/broadcast)
OID_IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
OID_IF_IN_MCAST = "1.3.6.1.2.1.31.1.1.1.2"
OID_IF_IN_BCAST = "1.3.6.1.2.1.31.1.1.1.3"
OID_IF_OUT_MCAST = "1.3.6.1.2.1.31.1.1.1.4"
OID_IF_OUT_BCAST = "1.3.6.1.2.1.31.1.1.1.5"
OID_IF_HC_IN_OCTETS = "1.3.6.1.2.1.31.1.1.1.6"
OID_IF_HC_IN_UCAST = "1.3.6.1.2.1.31.1.1.1.7"
OID_IF_HC_OUT_OCTETS = "1.3.6.1.2.1.31.1.1.1.10"
OID_IF_HC_OUT_UCAST = "1.3.6.1.2.1.31.1.1.1.11"
OID_IF_HIGH_SPEED = "1.3.6.1.2.1.31.1.1.1.15"


class SNMPCollectorError(Exception):
    """Raised when SNMP collection fails in a recoverable way."""


@dataclass(frozen=True)
class SNMPCredentials:
    host: str
    community: str = "public"
    port: int = 161
    timeout: float = 3.0
    retries: int = 1
    version: str = "2c"


def resolve_snmp_credentials(device: dict) -> SNMPCredentials:
    """Build SNMP credentials from device.credentials + environment defaults."""
    creds = device.get("credentials") or {}
    host = (device.get("ipAddress") or "").strip()
    if not host:
        raise SNMPCollectorError("Device has no ipAddress for SNMP collection")

    community = (
        creds.get("snmpCommunity")
        or os.getenv("SNMP_DEFAULT_COMMUNITY")
        or "public"
    ).strip()

    try:
        port = int(creds.get("snmpPort") or os.getenv("SNMP_DEFAULT_PORT") or 161)
    except (TypeError, ValueError) as exc:
        raise SNMPCollectorError(f"Invalid SNMP port: {exc}") from exc

    try:
        timeout = float(
            creds.get("snmpTimeout")
            or os.getenv("SNMP_TIMEOUT")
            or 3
        )
    except (TypeError, ValueError):
        timeout = 3.0

    try:
        retries = int(os.getenv("SNMP_RETRIES", "1"))
    except ValueError:
        retries = 1

    version = str(
        creds.get("snmpVersion")
        or os.getenv("SNMP_DEFAULT_VERSION")
        or "2c"
    ).strip().lower()

    return SNMPCredentials(
        host=host,
        community=community,
        port=port,
        timeout=max(timeout, 0.5),
        retries=max(retries, 0),
        version=version,
    )


def snmp_available() -> bool:
    """Return True when a usable pysnmp stack is importable."""
    try:
        from pysnmp.hlapi.v3arch.asyncio import bulk_cmd  # noqa: F401
        return True
    except ImportError:
        try:
            from pysnmp.hlapi import bulkCmd  # noqa: F401
            return True
        except ImportError:
            return False


class SNMPInterfaceCollector:
    """
    Collect per-interface counters via SNMP GETBULK walks.
    """

    def __init__(self, credentials: SNMPCredentials):
        if not snmp_available():
            raise SNMPCollectorError(
                "pysnmp is not installed. Run: pip install pysnmp"
            )
        self.credentials = credentials

    def collect_interface_stats(self) -> list[dict]:
        """
        Walk IF-MIB / IF-X-MIB and return a list of raw stats dicts.

        Each dict keys:
          name, if_index, rx_bytes, tx_bytes, rx_packets, tx_packets,
          broadcast_packets, multicast_packets, input_errors, output_errors,
          discards, speed_bps
        """
        creds = self.credentials
        logger.info(
            "[SNMP] Collecting interface stats | host=%s community=%s",
            creds.host,
            creds.community,
        )

        try:
            tables = asyncio.run(self._walk_all())
        except SNMPCollectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SNMPCollectorError(
                f"SNMP walk failed for {creds.host}: {exc}"
            ) from exc

        return self._merge_tables(tables)

    def probe(self) -> bool:
        """Lightweight sysDescr GET to test SNMP reachability."""
        try:
            rows = asyncio.run(self._walk_oid("1.3.6.1.2.1.1.1", max_rows=1))
            return bool(rows)
        except Exception as exc:  # noqa: BLE001
            logger.info("[SNMP] Probe failed | host=%s | %s", self.credentials.host, exc)
            return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _walk_all(self) -> dict[str, dict[int, Any]]:
        oid_keys = {
            "descr": OID_IF_DESCR,
            "name": OID_IF_NAME,
            "speed": OID_IF_SPEED,
            "high_speed": OID_IF_HIGH_SPEED,
            "in_octets": OID_IF_IN_OCTETS,
            "out_octets": OID_IF_OUT_OCTETS,
            "in_ucast": OID_IF_IN_UCAST,
            "out_ucast": OID_IF_OUT_UCAST,
            "in_nucast": OID_IF_IN_NUCAST,
            "out_nucast": OID_IF_OUT_NUCAST,
            "in_discards": OID_IF_IN_DISCARDS,
            "out_discards": OID_IF_OUT_DISCARDS,
            "in_errors": OID_IF_IN_ERRORS,
            "out_errors": OID_IF_OUT_ERRORS,
            "hc_in_octets": OID_IF_HC_IN_OCTETS,
            "hc_out_octets": OID_IF_HC_OUT_OCTETS,
            "hc_in_ucast": OID_IF_HC_IN_UCAST,
            "hc_out_ucast": OID_IF_HC_OUT_UCAST,
            "in_mcast": OID_IF_IN_MCAST,
            "in_bcast": OID_IF_IN_BCAST,
            "out_mcast": OID_IF_OUT_MCAST,
            "out_bcast": OID_IF_OUT_BCAST,
        }

        tables: dict[str, dict[int, Any]] = {}
        # Sequential walks keep device CPU load predictable; still fast on LAN.
        for key, oid in oid_keys.items():
            try:
                tables[key] = await self._walk_oid(oid)
            except SNMPCollectorError as exc:
                # HC / IF-X columns are optional on older gear.
                if key.startswith("hc_") or key in {
                    "name", "high_speed", "in_mcast", "in_bcast",
                    "out_mcast", "out_bcast",
                }:
                    logger.debug(
                        "[SNMP] Optional OID unavailable | host=%s oid=%s | %s",
                        self.credentials.host,
                        oid,
                        exc,
                    )
                    tables[key] = {}
                else:
                    raise

        if not tables.get("descr") and not tables.get("name"):
            raise SNMPCollectorError(
                f"No interface table data from {self.credentials.host}"
            )

        return tables

    async def _walk_oid(self, oid: str, max_rows: int = 5000) -> dict[int, Any]:
        """GETBULK walk; return {ifIndex: value}."""
        try:
            return await self._walk_oid_v3arch(oid, max_rows=max_rows)
        except ImportError:
            return await asyncio.to_thread(self._walk_oid_legacy_sync, oid, max_rows)

    async def _walk_oid_v3arch(self, oid: str, max_rows: int = 5000) -> dict[int, Any]:
        from pysnmp.hlapi.v3arch.asyncio import (
            CommunityData,
            ContextData,
            ObjectIdentity,
            ObjectType,
            SnmpEngine,
            UdpTransportTarget,
            bulk_cmd,
        )

        creds = self.credentials
        results: dict[int, Any] = {}
        engine = SnmpEngine()
        community = CommunityData(creds.community, mpModel=1)  # v2c
        if creds.version in ("1", "v1"):
            community = CommunityData(creds.community, mpModel=0)

        target = await UdpTransportTarget.create(
            (creds.host, creds.port),
            timeout=creds.timeout,
            retries=creds.retries,
        )
        context = ContextData()

        start_oid = ObjectIdentity(oid)
        current = ObjectType(start_oid)
        fetched = 0

        while fetched < max_rows:
            error_indication, error_status, error_index, var_binds = await bulk_cmd(
                engine,
                community,
                target,
                context,
                0,
                25,
                current,
            )

            if error_indication:
                raise SNMPCollectorError(str(error_indication))
            if error_status:
                raise SNMPCollectorError(
                    f"{error_status.prettyPrint()} at "
                    f"{error_index and var_binds[int(error_index) - 1][0] or '?'}"
                )

            finished = False
            for var_bind in var_binds:
                oid_obj, value = var_bind[0], var_bind[1]
                oid_str = str(oid_obj)
                if not oid_str.startswith(oid + ".") and oid_str != oid:
                    finished = True
                    break

                if_index = _oid_index(oid_str, oid)
                if if_index is None:
                    continue
                results[if_index] = _coerce_snmp_value(value)
                fetched += 1
                current = ObjectType(ObjectIdentity(oid_str))

            if finished or not var_binds:
                break

        return results

    def _walk_oid_legacy_sync(self, oid: str, max_rows: int = 5000) -> dict[int, Any]:
        """Fallback for older pysnmp that still exposes sync hlapi.bulkCmd."""
        from pysnmp.hlapi import (
            CommunityData,
            ContextData,
            ObjectIdentity,
            ObjectType,
            SnmpEngine,
            UdpTransportTarget,
            bulkCmd,
        )

        creds = self.credentials
        results: dict[int, Any] = {}
        community = CommunityData(creds.community, mpModel=1)
        if creds.version in ("1", "v1"):
            community = CommunityData(creds.community, mpModel=0)

        iterator = bulkCmd(
            SnmpEngine(),
            community,
            UdpTransportTarget(
                (creds.host, creds.port),
                timeout=creds.timeout,
                retries=creds.retries,
            ),
            ContextData(),
            0,
            25,
            ObjectType(ObjectIdentity(oid)),
            lexicographicMode=False,
        )

        for error_indication, error_status, error_index, var_binds in iterator:
            if error_indication:
                raise SNMPCollectorError(str(error_indication))
            if error_status:
                raise SNMPCollectorError(error_status.prettyPrint())
            for var_bind in var_binds:
                oid_obj, value = var_bind[0], var_bind[1]
                oid_str = str(oid_obj)
                if not oid_str.startswith(oid + "."):
                    return results
                if_index = _oid_index(oid_str, oid)
                if if_index is None:
                    continue
                results[if_index] = _coerce_snmp_value(value)
                if len(results) >= max_rows:
                    return results

        return results

    def _merge_tables(self, tables: dict[str, dict[int, Any]]) -> list[dict]:
        indexes = set()
        for table in tables.values():
            indexes.update(table.keys())

        rows: list[dict] = []
        for if_index in sorted(indexes):
            name = (
                _as_str(tables.get("name", {}).get(if_index))
                or _as_str(tables.get("descr", {}).get(if_index))
            )
            if not name:
                continue
            # Skip SNMP null / empty
            if name.lower() in ("", "null", "none"):
                continue

            hc_in = tables.get("hc_in_octets", {}).get(if_index)
            hc_out = tables.get("hc_out_octets", {}).get(if_index)
            rx_bytes = _as_int(hc_in if hc_in is not None else tables.get("in_octets", {}).get(if_index))
            tx_bytes = _as_int(hc_out if hc_out is not None else tables.get("out_octets", {}).get(if_index))

            hc_in_pkts = tables.get("hc_in_ucast", {}).get(if_index)
            hc_out_pkts = tables.get("hc_out_ucast", {}).get(if_index)
            in_ucast = _as_int(
                hc_in_pkts if hc_in_pkts is not None
                else tables.get("in_ucast", {}).get(if_index)
            )
            out_ucast = _as_int(
                hc_out_pkts if hc_out_pkts is not None
                else tables.get("out_ucast", {}).get(if_index)
            )

            in_mcast = _as_int(tables.get("in_mcast", {}).get(if_index))
            out_mcast = _as_int(tables.get("out_mcast", {}).get(if_index))
            in_bcast = _as_int(tables.get("in_bcast", {}).get(if_index))
            out_bcast = _as_int(tables.get("out_bcast", {}).get(if_index))

            # Older IF-MIB non-unicast when IF-X mcast/bcast absent
            if in_mcast == 0 and in_bcast == 0:
                in_nucast = _as_int(tables.get("in_nucast", {}).get(if_index))
                in_bcast = in_nucast  # best-effort bucket
            if out_mcast == 0 and out_bcast == 0:
                out_nucast = _as_int(tables.get("out_nucast", {}).get(if_index))
                out_bcast = out_nucast

            in_errors = _as_int(tables.get("in_errors", {}).get(if_index))
            out_errors = _as_int(tables.get("out_errors", {}).get(if_index))
            discards = (
                _as_int(tables.get("in_discards", {}).get(if_index))
                + _as_int(tables.get("out_discards", {}).get(if_index))
            )

            high_speed = tables.get("high_speed", {}).get(if_index)
            speed = tables.get("speed", {}).get(if_index)
            if high_speed not in (None, 0, "0"):
                speed_bps = _as_int(high_speed) * 1_000_000
            else:
                speed_bps = _as_int(speed)
                # ifSpeed saturates at 4,294,967,295 for >4Gbps links
                if speed_bps >= 4294967295:
                    speed_bps = 0

            rows.append({
                "name": name.strip(),
                "if_index": if_index,
                "rx_bytes": rx_bytes,
                "tx_bytes": tx_bytes,
                "rx_packets": in_ucast + in_mcast + in_bcast,
                "tx_packets": out_ucast + out_mcast + out_bcast,
                "broadcast_packets": in_bcast + out_bcast,
                "multicast_packets": in_mcast + out_mcast,
                "input_errors": in_errors,
                "output_errors": out_errors,
                "discards": discards,
                "speed_bps": speed_bps or None,
            })

        logger.info(
            "[SNMP] Collected %d interface(s) | host=%s",
            len(rows),
            self.credentials.host,
        )
        return rows


def _oid_index(oid_str: str, base_oid: str) -> int | None:
    suffix = oid_str[len(base_oid):].lstrip(".")
    if not suffix:
        return None
    first = suffix.split(".", 1)[0]
    try:
        return int(first)
    except ValueError:
        return None


def _coerce_snmp_value(value: Any) -> Any:
    if value is None:
        return None
    # pysnmp typed values expose prettyPrint / integer conversion
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        text = value.prettyPrint() if hasattr(value, "prettyPrint") else str(value)
    except Exception:  # noqa: BLE001
        text = str(value)
    # Strip hex-string prefixes like "0x..."
    if isinstance(text, str) and text.startswith("0x"):
        try:
            return bytes.fromhex(text[2:]).decode("utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            return text
    return text


def _as_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


# Backwards-compatible name used by earlier stub docs
class SNMPInterfaceStatsCollector(SNMPInterfaceCollector):
    """Alias kept for Storm Detection / future call sites."""
