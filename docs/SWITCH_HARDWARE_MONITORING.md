# Switch Hardware Monitoring

## Overview

Cisco and Juniper switch hardware monitoring lives under `backend/services/switch_hardware/`.

Default state: **disabled** (`SWITCH_HARDWARE_MONITORING_ENABLED=false`).

## Supported platforms

- Cisco IOS
- Cisco IOS-XE
- Cisco NX-OS
- Juniper Junos (EX and QFX chassis CLI output)

## Access methods

- **SNMP v2c** — reuses existing encrypted `snmpCommunity` credentials
- **SSH** — read-only show commands via existing Paramiko infrastructure

For Juniper devices, set Vendor to `Juniper` (and preferably SSH vendor to
`juniper_junos`). SSH reads `show version`, `show chassis hardware`,
`show chassis environment`, `show chassis routing-engine`, and
`show chassis alarms`. SNMP uses standard ENTITY-MIB and ENTITY-SENSOR-MIB
where the switch exposes them; Juniper enterprise chassis MIB polling is not
implemented. Sensor availability varies by model and Junos release.

SNMPv3 is deferred to Phase 2.

## Environment variables

See `backend/.env.example` (`SWITCH_HARDWARE_*`).

History retention has its own setting, `switchHardwareHistoryRetentionDays`
(default 90 days), configurable in Settings → Data retention — independent of
`dataRetentionDays`. Seeded from `SWITCH_HARDWARE_HISTORY_RETENTION_DAYS`.

## MongoDB collections

- `switch_hardware_current`
- `switch_hardware_history`
- `switch_hardware_events`
- `switch_outage_incidents`

## API endpoints

- `GET /api/switches/<device_id>/hardware`
- `GET /api/switches/<device_id>/hardware/history`
- `GET /api/switches/<device_id>/hardware/events`
- `GET /api/switches/<device_id>/outages`
- `GET /api/switches/<device_id>/outages/<incident_id>`
- `POST /api/switches/<device_id>/hardware/collect` (admin only)

## Outage detection

Primary trigger: existing ping offline transition (`Not Reachable` / `Offline (Critical)`).

SNMP/SSH collection failures are supporting evidence only and do not create outages.

## Known limitations

- Partial MIB/CLI support varies by platform
- Missing sensors display as unavailable — never fabricated
- Root cause analysis is evidence-based and may return `unknown`
