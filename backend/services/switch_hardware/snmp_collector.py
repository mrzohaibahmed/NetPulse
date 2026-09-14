"""SNMP hardware collector for Cisco switches (Phase 1: v2c community)."""

from __future__ import annotations

import asyncio
from typing import Any

from services.interface_collection.snmp import (
    SNMPCollectorError,
    SNMPCredentials,
    resolve_snmp_credentials,
    snmp_available,
)
from services.switch_hardware.models import (
    build_fan,
    build_psu,
    build_sensor,
    empty_cpu,
    empty_fans,
    empty_inventory,
    empty_memory,
    empty_power_supplies,
    empty_temperature,
)
from utils.monitor_logger import get_monitor_logger

logger = get_monitor_logger("switch_hardware.snmp")

# ENTITY-MIB
OID_ENT_PHYSICAL_DESCR = "1.3.6.1.2.1.47.1.1.1.1.2"
OID_ENT_PHYSICAL_CLASS = "1.3.6.1.2.1.47.1.1.1.1.5"
OID_ENT_PHYSICAL_NAME = "1.3.6.1.2.1.47.1.1.1.1.7"
OID_ENT_PHYSICAL_SERIAL = "1.3.6.1.2.1.47.1.1.1.1.11"
OID_ENT_PHYSICAL_MODEL = "1.3.6.1.2.1.47.1.1.1.1.13"

# ENTITY-SENSOR-MIB
OID_ENT_SENSOR_TYPE = "1.3.6.1.2.1.99.1.1.1.1"
OID_ENT_SENSOR_VALUE = "1.3.6.1.2.1.99.1.1.1.4"
OID_ENT_SENSOR_STATUS = "1.3.6.1.2.1.99.1.1.1.5"
OID_ENT_SENSOR_UNITS = "1.3.6.1.2.1.99.1.1.1.6"

# Cisco ENVMON (legacy)
OID_CISCO_ENV_TEMP_VALUE = "1.3.6.1.4.1.9.9.13.1.3.1.3"
OID_CISCO_ENV_TEMP_STATE = "1.3.6.1.4.1.9.9.13.1.3.1.6"
OID_CISCO_ENV_FAN_STATE = "1.3.6.1.4.1.9.9.13.1.4.1.3"
OID_CISCO_ENV_SUPPLY_STATE = "1.3.6.1.4.1.9.9.13.1.5.1.3"

OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"

# entPhySensorType values
SENSOR_TYPE_CELSIUS = 8
SENSOR_TYPE_RPM = 10

# Cisco envmon states: 1 normal, 2 warning, 3 critical, 4 shutdown, 5 notPresent, 6 notFunctioning
_CISCO_ENV_STATE = {
    1: "healthy",
    2: "warning",
    3: "critical",
    4: "critical",
    5: "not_available",
    6: "critical",
}


class SwitchHardwareSNMPCollector:
    def __init__(self, credentials: SNMPCredentials):
        if not snmp_available():
            raise SNMPCollectorError("pysnmp is not installed")
        self.credentials = credentials

    def collect(self) -> dict[str, Any]:
        try:
            tables = asyncio.run(self._walk_tables())
        except Exception as exc:  # noqa: BLE001
            raise SNMPCollectorError(str(exc)) from exc
        return self._normalize(tables)

    async def _walk_tables(self) -> dict[str, dict[int, Any]]:
        keys = {
            "sys_descr": OID_SYS_DESCR,
            "ent_descr": OID_ENT_PHYSICAL_DESCR,
            "ent_class": OID_ENT_PHYSICAL_CLASS,
            "ent_name": OID_ENT_PHYSICAL_NAME,
            "ent_serial": OID_ENT_PHYSICAL_SERIAL,
            "ent_model": OID_ENT_PHYSICAL_MODEL,
            "sensor_type": OID_ENT_SENSOR_TYPE,
            "sensor_value": OID_ENT_SENSOR_VALUE,
            "sensor_status": OID_ENT_SENSOR_STATUS,
            "sensor_units": OID_ENT_SENSOR_UNITS,
            "cisco_temp_value": OID_CISCO_ENV_TEMP_VALUE,
            "cisco_temp_state": OID_CISCO_ENV_TEMP_STATE,
            "cisco_fan_state": OID_CISCO_ENV_FAN_STATE,
            "cisco_supply_state": OID_CISCO_ENV_SUPPLY_STATE,
        }
        tables: dict[str, dict[int, Any]] = {}
        for key, oid in keys.items():
            try:
                if oid.endswith(".0"):
                    tables[key] = await self._get_scalar(oid)
                else:
                    tables[key] = await self._walk_oid(oid, max_rows=500)
            except SNMPCollectorError as exc:
                logger.debug("[HW-SNMP] Optional OID unavailable | %s | %s", key, exc)
                tables[key] = {} if not oid.endswith(".0") else None
        return tables

    async def _walk_oid(self, oid: str, max_rows: int = 500) -> dict[int, Any]:
        try:
            return await self._walk_oid_v3arch(oid, max_rows=max_rows)
        except ImportError:
            return await asyncio.to_thread(self._walk_oid_legacy_sync, oid, max_rows)

    async def _get_scalar(self, oid: str) -> str | None:
        rows = await self._walk_oid(oid.replace(".0", ""), max_rows=1)
        if rows:
            return str(next(iter(rows.values())))
        return None

    async def _walk_oid_v3arch(self, oid: str, max_rows: int) -> dict[int, Any]:
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
        community = CommunityData(creds.community, mpModel=1)
        if creds.version in ("1", "v1"):
            community = CommunityData(creds.community, mpModel=0)
        target = await UdpTransportTarget.create(
            (creds.host, creds.port),
            timeout=creds.timeout,
            retries=creds.retries,
        )
        context = ContextData()
        current = ObjectType(ObjectIdentity(oid))
        fetched = 0
        prefix = oid + "."

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
                raise SNMPCollectorError(str(error_status))
            if not var_binds:
                break
            for oid_val, val in var_binds:
                oid_str = str(oid_val)
                if not oid_str.startswith(prefix) and oid_str != oid:
                    return results
                idx_part = oid_str.rsplit(".", 1)[-1]
                try:
                    idx = int(idx_part)
                except ValueError:
                    continue
                results[idx] = val.prettyPrint() if hasattr(val, "prettyPrint") else val
                fetched += 1
            current = ObjectType(var_binds[-1][0])
        return results

    def _walk_oid_legacy_sync(self, oid: str, max_rows: int) -> dict[int, Any]:
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
        engine = SnmpEngine()
        community = CommunityData(creds.community, mpModel=1)
        target = UdpTransportTarget((creds.host, creds.port), timeout=creds.timeout, retries=creds.retries)
        context = ContextData()
        current = ObjectType(ObjectIdentity(oid))
        fetched = 0
        prefix = oid + "."

        while fetched < max_rows:
            error_indication, error_status, error_index, var_binds = bulkCmd(
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
                raise SNMPCollectorError(str(error_status))
            if not var_binds:
                break
            for oid_val, val in var_binds:
                oid_str = str(oid_val)
                if not oid_str.startswith(prefix) and oid_str != oid:
                    return results
                idx_part = oid_str.rsplit(".", 1)[-1]
                try:
                    idx = int(idx_part)
                except ValueError:
                    continue
                results[idx] = val.prettyPrint() if hasattr(val, "prettyPrint") else val
                fetched += 1
            current = ObjectType(var_binds[-1][0])
        return results

    def _normalize(self, tables: dict[str, Any]) -> dict[str, Any]:
        inventory = empty_inventory()
        temperature = empty_temperature()
        fans = empty_fans()
        psus = empty_power_supplies()

        sys_descr = tables.get("sys_descr")
        if sys_descr:
            inventory["iosVersion"] = None

        ent_descr = tables.get("ent_descr") or {}
        ent_serial = tables.get("ent_serial") or {}
        ent_model = tables.get("ent_model") or {}
        modules = []
        for idx, descr in ent_descr.items():
            modules.append(
                {
                    "name": str(descr),
                    "serialNumber": str(ent_serial.get(idx) or "") or None,
                    "productId": str(ent_model.get(idx) or "") or None,
                }
            )
        if modules:
            inventory["modules"] = modules
            inventory["model"] = modules[0].get("productId")
            inventory["serialNumber"] = modules[0].get("serialNumber")
            inventory["productId"] = modules[0].get("productId")

        sensors: list[dict[str, Any]] = []
        sensor_type = tables.get("sensor_type") or {}
        sensor_value = tables.get("sensor_value") or {}
        sensor_status = tables.get("sensor_status") or {}
        for idx, stype in sensor_type.items():
            try:
                stype_int = int(stype)
            except (TypeError, ValueError):
                continue
            name = f"Sensor {idx}"
            if stype_int == SENSOR_TYPE_CELSIUS:
                try:
                    value = float(sensor_value.get(idx, 0)) / 1000.0
                except (TypeError, ValueError):
                    value = None
                status = _map_sensor_status(sensor_status.get(idx))
                sensors.append(
                    build_sensor(
                        name=name,
                        value=value,
                        unit="C",
                        status=status,
                        source="snmp",
                    )
                )
            elif stype_int == SENSOR_TYPE_RPM:
                try:
                    rpm = int(float(sensor_value.get(idx, 0)))
                except (TypeError, ValueError):
                    rpm = None
                fans.setdefault("_rpm_items", []).append(
                    build_fan(name=name, status="unknown", rpm=rpm, source="snmp")
                )

        cisco_temp_value = tables.get("cisco_temp_value") or {}
        cisco_temp_state = tables.get("cisco_temp_state") or {}
        for idx, val in cisco_temp_value.items():
            try:
                value = float(val)
            except (TypeError, ValueError):
                value = None
            state = cisco_temp_state.get(idx)
            try:
                status = _CISCO_ENV_STATE.get(int(state), "unknown")
            except (TypeError, ValueError):
                status = "unknown"
            sensors.append(
                build_sensor(
                    name=f"Temperature {idx}",
                    value=value,
                    unit="C",
                    status=status,
                    source="snmp",
                )
            )

        if sensors:
            temperature["sensors"] = sensors
            temperature["status"] = _worst_status(s["status"] for s in sensors)

        fan_items = list(fans.pop("_rpm_items", []))
        cisco_fan_state = tables.get("cisco_fan_state") or {}
        for idx, state in cisco_fan_state.items():
            try:
                status = _CISCO_ENV_STATE.get(int(state), "unknown")
            except (TypeError, ValueError):
                status = "unknown"
            fan_items.append(build_fan(name=f"Fan {idx}", status=status, source="snmp"))

        if fan_items:
            fans["items"] = fan_items
            fans["count"] = len(fan_items)
            fans["failedCount"] = sum(1 for f in fan_items if f["status"] == "critical")
            fans["healthyCount"] = sum(1 for f in fan_items if f["status"] == "healthy")

        psu_items = []
        cisco_supply_state = tables.get("cisco_supply_state") or {}
        for idx, state in cisco_supply_state.items():
            try:
                status = _CISCO_ENV_STATE.get(int(state), "unknown")
            except (TypeError, ValueError):
                status = "unknown"
            psu_items.append(build_psu(name=f"Power Supply {idx}", status=status, source="snmp"))
        if psu_items:
            psus["items"] = psu_items
            psus["count"] = len(psu_items)
            psus["failedCount"] = sum(1 for p in psu_items if p["status"] == "critical")
            psus["healthyCount"] = sum(1 for p in psu_items if p["status"] == "healthy")

        return {
            "inventory": inventory,
            "temperature": temperature,
            "fans": fans,
            "powerSupplies": psus,
            "cpu": empty_cpu(),
            "memory": empty_memory(),
            "alarms": [],
            "sysDescr": sys_descr,
            "availability": {"snmp": "available"},
        }


def _map_sensor_status(raw: Any) -> str:
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return "unknown"
    if val == 1:
        return "healthy"
    if val == 2:
        return "warning"
    if val in (3, 4):
        return "critical"
    return "unknown"


def _worst_status(statuses) -> str:
    order = {"unknown": 0, "not_available": 1, "healthy": 2, "warning": 3, "critical": 4}
    best = "unknown"
    for status in statuses:
        if order.get(status, 0) > order.get(best, 0):
            best = status
    return best


def collect_snmp_hardware(device: dict, *, timeout: float | None = None) -> dict[str, Any]:
    creds = resolve_snmp_credentials(device)
    if timeout is not None:
        creds = SNMPCredentials(
            host=creds.host,
            community=creds.community,
            port=creds.port,
            timeout=timeout,
            retries=creds.retries,
            version=creds.version,
        )
    collector = SwitchHardwareSNMPCollector(creds)
    return collector.collect()
