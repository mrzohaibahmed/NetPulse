# Cisco Switch Hardware Monitoring (Phase 1)

## Overview

Phase 1 adds Cisco switch hardware monitoring as an isolated capability under `backend/services/switch_hardware/`.

Default state: **disabled** (`SWITCH_HARDWARE_MONITORING_ENABLED=false`).

## Supported platforms

- Cisco IOS
- Cisco IOS-XE
- Cisco NX-OS

## Access methods

- **SNMP v2c** — reuses existing encrypted `snmpCommunity` credentials
- **SSH** — read-only show commands via existing Paramiko infrastructure

SNMPv3 is deferred to Phase 2.

## Environment variables

See `backend/.env.example` (`SWITCH_HARDWARE_*`).

History retention uses existing `DATA_RETENTION_DAYS` (default 90).

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
