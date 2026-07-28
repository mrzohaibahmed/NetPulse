# Interface Module

Complete reference for **Interface Discovery**, **Port Classification**, **Topology Enrichment**, and **Interface Statistics** in the Network Monitoring System.

This document describes what the module contains, how data flows end-to-end, schemas, APIs, scheduler jobs, frontend surfaces, and how future Storm Protection engines are expected to consume the data.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture & Pipeline](#2-architecture--pipeline)
3. [Directory Map](#3-directory-map)
4. [Part A — Interface Discovery](#4-part-a--interface-discovery)
5. [Part B — Topology Enrichment](#5-part-b--topology-enrichment)
6. [Part C — Device Type Detection](#6-part-c--device-type-detection)
7. [Part D — Port Classification](#7-part-d--port-classification)
8. [Part E — Name Normalization](#8-part-e--name-normalization)
9. [Part F — Interface Statistics](#9-part-f--interface-statistics)
10. [MongoDB Collections & Schema](#10-mongodb-collections--schema)
11. [REST API](#11-rest-api)
12. [Scheduler Integration](#12-scheduler-integration)
13. [Configuration (`.env`)](#13-configuration-env)
14. [Frontend](#14-frontend)
15. [Credentials Resolution](#15-credentials-resolution)
16. [Eligibility Rules](#16-eligibility-rules)
17. [Error Handling & Soft-Fail Behavior](#17-error-handling--soft-fail-behavior)
18. [Storm Protection Readiness](#18-storm-protection-readiness)
19. [Operational Runbook](#19-operational-runbook)
20. [Extending the Module](#20-extending-the-module)

---

## 1. Overview

The Interface module has two complementary jobs:

| Job | Purpose | Transport | Persistence |
|-----|---------|-----------|-------------|
| **Discovery (inventory)** | Learn every interface, VLAN mode, neighbor, and classification flags | SSH (Paramiko) | `interfaces` (upsert) |
| **Statistics (telemetry)** | Collect counters, compute utilization | SNMP first, SSH fallback | `interface_stats` (append-only) |

Discovery builds the **inventory** that Storm Protection and operators use.  
Statistics builds the **time-series** used for utilization charts and future risk scoring.

### Design principles

- **Separation of concerns** — SSH never parses; parser never classifies; classifier never runs SSH.
- **Vendor-independent schema** — Cisco-specific CLI stays inside the collector/parser; MongoDB and API use camelCase fields.
- **Backward compatible** — Existing fields (`mode`, `neighbor.port`, etc.) remain; new fields are additive.
- **Safe rediscovery** — Unique `(deviceId, name)`, alias collapse (`Gi` ↔ `GigabitEthernet`), stale cleanup, admin override preservation (`isProtected`).

---

## 2. Architecture & Pipeline

```
┌─────────────┐
│  Devices   │  (db.devices — Online + credentials / switch-like type)
└──────┬──────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────┐
│                     INTERFACE DISCOVERY                          │
│                                                                  │
│  ssh_collector  →  parser  →  normalizer  →  classifier  →  DB   │
│       │               │           │              │               │
│   raw CLI         raw dicts   camelCase      flags +             │
│   outputs         (vendor)    inventory      deviceType          │
└──────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────┐         ┌─────────────────────┐
│  db.interfaces      │◄────────│  REST API / Frontend│
└─────────────────────┘         └─────────────────────┘
       │
       │  (name / canonical match)
       ▼
┌──────────────────────────────────────────────────────────────────┐
│                     INTERFACE STATISTICS                         │
│                                                                  │
│  snmp (preferred)  ──┐                                           │
│                      ├──► stats_collector ──► db.interface_stats │
│  ssh_stats (fallback)┘         │                                 │
│                         utilization from                         │
│                         previous sample                          │
└──────────────────────────────────────────────────────────────────┘
```

### Strict layer responsibilities

| Layer | File | May do | Must not do |
|-------|------|--------|-------------|
| SSH Collector | `ssh_collector.py` | Connect, run show commands, return raw text | Parse or persist |
| Parser | `parser.py` | Extract fields from CLI text | Classify ports / run SSH |
| Normalizer | `normalizer.py` | Map to vendor-independent schema | Run SSH / set Storm flags (except mode-derived prep) |
| Classifier | `classifier.py` | Set flags + neighbor `deviceType` | Parse CLI / run SSH |
| Collector | `collector.py` | Orchestrate + upsert MongoDB | Embed vendor CLI logic |
| Stats | `stats_collector.py` / `snmp.py` / `ssh_stats.py` | Poll counters + utilization | Rewrite inventory schema |
| API | `routes/interface_routes.py` | Expose JSON | Contain collection logic |
| Frontend | `InterfacesPage` / `InterfaceDetailPage` | Display inventory + stats | |

---

## 3. Directory Map

### Backend

```
backend/
├── models/
│   ├── interface.py              # Factory for interfaces documents
│   └── interface_stats.py        # Factory for interface_stats documents
├── routes/
│   └── interface_routes.py       # REST endpoints under /api
├── scheduler.py                  # APScheduler jobs for discovery + stats
├── utils/
│   └── serializers.py            # serialize_interface / serialize_interface_stat
└── services/interface_collection/
    ├── __init__.py               # Public exports
    ├── naming.py                 # Canonical / storage name helpers
    ├── ssh_collector.py          # SSH transport + command sets
    ├── parser.py                 # CLI → raw dicts
    ├── normalizer.py             # Raw → vendor-independent schema
    ├── classifier.py             # Port flags + neighbor device type
    ├── collector.py              # Discovery orchestration + persistence
    ├── snmp.py                   # IF-MIB / IF-X-MIB stats
    ├── ssh_stats.py              # SSH counter fallback
    └── stats_collector.py        # Stats orchestration + utilization
```

### Frontend

```
frontend/src/
├── pages/
│   ├── InterfacesPage.tsx        # Inventory list + live utilization
│   └── InterfaceDetailPage.tsx   # Detail + history charts
├── components/interfaces/
│   └── InterfaceStatusBadge.tsx  # Status / mode / classification badges
├── utils/
│   └── interfaceNames.ts         # Frontend name canonicalization
├── types/index.ts                # NetworkInterface, InterfaceNeighbor, …
├── api/index.ts                  # HTTP client methods
└── hooks/queries.ts              # React Query hooks + mutations
```

---

## 4. Part A — Interface Discovery

### Entry points

| Function | Purpose |
|----------|---------|
| `discover_device_interfaces(device)` | Discover one device (never raises) |
| `discover_all_switch_interfaces()` | Thread-pooled bulk discovery |
| `ensure_interface_indexes()` | Create unique + query indexes |
| `get_interfaces(...)` | Paginated query with filters |

Called from:

- `POST /api/interfaces/discover/<device_id>` (admin)
- `POST /api/interfaces/discover-all` (admin)
- APScheduler job `interface_discovery_job`

### Discovery flow (per device)

1. Skip if `device.status != "Online"`.
2. Resolve SSH credentials (device → env defaults).
3. Open Paramiko session; disable paging; optional enable secret.
4. Run vendor command set (see below).
5. Parse outputs → list of raw interface dicts.
6. Dedupe by **canonical** interface name.
7. For each interface:
   - `normalize_raw_interface`
   - Preserve `isProtected` / explicit `monitoringEnabled=false` from existing docs
   - `classify_interface`
   - Upsert on `(deviceId, name)`
   - Collapse long/short name aliases
8. Delete stale interfaces whose canonical name was not seen this run.

### SSH commands (Cisco IOS / XE / NX-OS)

| Logical key | Example command | Required? |
|-------------|-----------------|-----------|
| `status` | `show interfaces status` | **Yes** |
| `description` | `show interfaces description` | Optional |
| `switchport` | `show interfaces switchport` | Optional |
| `vlan_brief` | `show vlan brief` | Optional |
| `cdp` | `show cdp neighbors detail` | Optional |
| `lldp` | `show lldp neighbors detail` | Optional |

**Juniper:** `show interfaces terse` only (limited inventory).  
**Aruba / generic:** brief/status-style commands; limited switchport/neighbor coverage.

> If CDP/LLDP/switchport are disabled or fail, discovery **still succeeds** using status (+ description when available).

### What inventory captures

- Interface name (normalized short form, e.g. `Gi1/0/24`)
- Description
- Admin / operational status
- Port mode: `access` | `trunk` | `routed` | `unknown`
- Access VLAN, Voice VLAN, Native VLAN, Allowed VLANs
- Speed / duplex (`speed` string + `speedMbps`)
- Neighbor topology (enriched)
- Classification flags
- `lastUpdated`, `createdAt`, `updatedAt`
- `collectionMethod` (currently `"ssh"` for inventory)

---

## 5. Part B — Topology Enrichment

Neighbors come from CDP (preferred) and LLDP (fill / enrich).

### Merge policy

1. Parse CDP → map `local_interface → neighbor`.
2. Parse LLDP → for each local interface:
   - If no CDP neighbor → use LLDP.
   - If CDP exists → fill blank fields from LLDP (`managementAddress`, `systemDescription`, etc.).
3. Prefer CDP `protocol` when CDP contributed identity.

### Neighbor object (API / MongoDB camelCase)

```json
{
  "hostname": "CORE01",
  "ip": "192.168.10.1",
  "platform": "cisco WS-C9300-48P",
  "deviceType": "Switch",
  "interface": "Gi1/0/48",
  "port": "Gi1/0/48",
  "protocol": "cdp",
  "managementAddress": "192.168.10.1",
  "systemDescription": "Cisco IOS XE Software ...",
  "capabilities": ["Switch", "IGMP"]
}
```

Notes:

- `interface` is the canonical remote port field.
- `port` is retained as a **backward-compatible alias** of `interface`.
- CDP extracts platform, IP, capabilities, version/system description.
- LLDP extracts System Name, Port id / Port Description, Management Address, System Description, System Capabilities.

---

## 6. Part C — Device Type Detection

Implemented in `classifier.classify_neighbor_device_type(...)`.

Reusable; does **not** require SSH. Inputs: `platform`, `capabilities`, `system_description`, `hostname`.

| Detected type | Example hints |
|---------------|---------------|
| `Switch` | Catalyst, Nexus, WS-C, “Switch” capability |
| `Router` | ISR, ASR, “Router” |
| `Firewall` | Fortinet, Palo Alto, ASA, Firepower |
| `Wireless AP` | AIR-, C9115/9120/9130, Aironet |
| `Wireless Controller` | WLC, Catalyst 9800, AIR-CT |
| `IP Phone` | Cisco IP Phone, CP-, SEP… |
| `Server` | VMware, ESXi, Hyper-V, Windows Server |
| `Unknown` | No matching hints |

Stored on the neighbor as `neighbor.deviceType` during classification.

---

## 7. Part D — Port Classification

Module: `services/interface_collection/classifier.py`

Receives a **normalized** interface dict and sets:

| Flag | Rule (summary) |
|------|----------------|
| `isAccess` | `portMode == access` |
| `isTrunk` | `portMode == trunk` |
| `isUplink` | Neighbor type ∈ {Switch, Router, Firewall, Wireless Controller} **or** description matches `UPLINK\|CORE\|DIST\|BACKBONE\|STACK\|PORTCHANNEL` |
| `isInfrastructure` | Neighbor type ∈ {Switch, Router, Firewall, Wireless Controller, Server} **or** description matches `SERVER\|CORE\|DIST\|FW\|RTR\|…` |
| `isManagement` | Name matches `Ma/Mgmt/Management/Lo/Loopback/Vlan1` **or** description matches `MGMT\|OOB` |
| `isProtected` | Default `false`; **preserved** across rediscovery if already set (admin override for future UI) |
| `monitoringEnabled` | Default `true`; forced `false` when admin status is `down`; explicit `false` preserved when not admin-down |

Classifier never parses CLI and never opens SSH sessions.

---

## 8. Part E — Name Normalization

Module: `naming.py` (backend) and `utils/interfaceNames.ts` (frontend).

### Problem

Devices and SNMP report the same port many ways:

- `Gi1/0/1`
- `Gig1/0/1`
- `GigabitEthernet1/0/1`

Without normalization, inventory duplicates appear and stats fail to join.

### Functions

| Function | Role |
|----------|------|
| `canonicalize_interface_name` | Comparison key (`gi1/0/1`) |
| `normalize_storage_interface_name` | Preferred DB form (`Gi1/0/1`) |
| `names_match` / `interfaceNamesMatch` | Equality helper |

### Where applied

- Parser / dedupe during discovery
- Persistence upsert + alias collapse + stale delete
- Stats collection (store short names; match previous samples by canonical key)
- Frontend list merge and detail page lookups

---

## 9. Part F — Interface Statistics

### Strategy

1. Try **SNMP** (IF-MIB / IF-X-MIB) when `pysnmp` is available and credentials resolve.
2. On failure / empty → **SSH** counters (`show interfaces counters`, etc.).
3. **Insert** documents into `interface_stats` (append-only history).
4. Compute utilization from the previous sample:

\[
\text{util\%} = \min\left(100,\ \frac{\Delta\mathrm{bytes} \times 8}{\Delta t \times \mathrm{speedBps}} \times 100\right)
\]

Overall utilization = max(RX%, TX%). Counter wrap (32/64-bit) is handled.

### Stats document fields

`rxBytes`, `txBytes`, `rxPackets`, `txPackets`, `broadcastPackets`, `multicastPackets`, `inputErrors`, `outputErrors`, `discards`, `utilization`, `rxUtilization`, `txUtilization`, `speedBps`, `ifIndex`, `collectionMethod`, `timestamp`.

### Indexes

- `(deviceId, interfaceName, timestamp DESC)`
- `(deviceId, timestamp DESC)`
- `(timestamp DESC)`

---

## 10. MongoDB Collections & Schema

### `interfaces` (inventory — upserted)

```json
{
  "_id": "ObjectId",
  "deviceId": "ObjectId",
  "hostname": "ACCESS-SW-01",
  "ipAddress": "10.0.0.10",
  "name": "Gi1/0/24",
  "description": "Core Uplink",
  "adminStatus": "up",
  "operStatus": "up",
  "mode": "trunk",
  "portMode": "trunk",
  "accessVlan": null,
  "voiceVlan": 20,
  "nativeVlan": 99,
  "allowedVlans": [10, 20, 30],
  "vlan": "trunk",
  "speed": "1000",
  "speedMbps": 1000,
  "duplex": "full",
  "neighbor": {
    "hostname": "CORE01",
    "ip": "192.168.10.1",
    "platform": "cisco WS-C9300-48P",
    "deviceType": "Switch",
    "interface": "Gi1/0/48",
    "port": "Gi1/0/48",
    "protocol": "cdp",
    "managementAddress": "192.168.10.1",
    "systemDescription": "Cisco IOS XE ...",
    "capabilities": ["Switch", "IGMP"]
  },
  "isAccess": false,
  "isTrunk": true,
  "isUplink": true,
  "isInfrastructure": true,
  "isManagement": false,
  "isProtected": false,
  "monitoringEnabled": true,
  "ifIndex": null,
  "macAddress": "",
  "vendor": "cisco",
  "collectionMethod": "ssh",
  "lastUpdated": "ISO-8601",
  "createdAt": "ISO-8601",
  "updatedAt": "ISO-8601"
}
```

**Indexes**

- Unique: `(deviceId, name)` → `uniq_device_interface_name`
- `(deviceId)` → `idx_interfaces_device`
- `(lastUpdated DESC)` → `idx_interfaces_updated`

**Lifecycle**

- Upsert on rediscovery; `createdAt` preserved via `$setOnInsert`.
- Alias docs (long vs short name) collapsed to one record.
- Names no longer present on the device are deleted.
- Deleting a device cascades delete of its interfaces + stats (`device_routes`).

### `interface_stats` (telemetry — append-only)

Keyed logically by `(deviceId, interfaceName, timestamp)`. No unique constraint — history grows over time (retention/TTL is a future ops concern).

---

## 11. REST API

Blueprint prefix: `/api`  
Auth: all routes require authentication; discover/collect require **admin**.

| Method | Path | Role | Description |
|--------|------|------|-------------|
| `GET` | `/interfaces` | any | Paginated inventory (`q`, `adminStatus`, `operStatus`, `mode`, `deviceId`) |
| `GET` | `/interfaces/<device_id>` | any | Interfaces for one device |
| `POST` | `/interfaces/discover/<device_id>` | admin | Discover one online device |
| `POST` | `/interfaces/discover-all` | admin | Discover all eligible online devices |
| `GET` | `/interfaces/<device_id>/stats` | any | Latest stats sample per interface |
| `POST` | `/interfaces/<device_id>/stats/collect` | admin | Collect stats for one device |
| `POST` | `/interfaces/stats/collect-all` | admin | Collect stats for all eligible devices |
| `GET` | `/interfaces/<device_id>/<interface_name>/history` | any | Historical samples (`startDate`, `endDate`, pagination) |

Serialization: `serialize_interface` / `serialize_interface_stat` in `utils/serializers.py` (ObjectIds → strings, datetimes → ISO-Z, full neighbor + classification fields).

---

## 12. Scheduler Integration

Configured in `backend/scheduler.py`, started from `app.py` (non-reloader parent).

| Job ID | Function | Env var | Default | Notes |
|--------|----------|---------|---------|-------|
| `interface_discovery_job` | `discover_all_switch_interfaces` | `INTERFACE_SCAN_INTERVAL` | `3600` s | Min 60s; **`0` disables** |
| `interface_stats_job` | `collect_all_interface_stats` | `INTERFACE_STATS_INTERVAL` | `60` s | Min 15s; **`0` disables**; `max_instances=1`, coalesce |

Both jobs are independent of ping and Nmap monitoring.

---

## 13. Configuration (`.env`)

```env
# Discovery
INTERFACE_SCAN_INTERVAL=3600
MAX_INTERFACE_THREADS=5

# SSH defaults (overridden by per-device credentials)
SSH_DEFAULT_USERNAME=
SSH_DEFAULT_PASSWORD=
SSH_DEFAULT_PORT=22
SSH_DEFAULT_SECRET=
SSH_DEFAULT_VENDOR=cisco_ios
SSH_TIMEOUT=30

# Statistics
INTERFACE_STATS_INTERVAL=60
MAX_INTERFACE_STATS_THREADS=8
INTERFACE_STATS_BATCH_SIZE=500

# SNMP defaults
SNMP_DEFAULT_COMMUNITY=public
SNMP_DEFAULT_VERSION=2c
SNMP_DEFAULT_PORT=161
SNMP_TIMEOUT=3
SNMP_RETRIES=1
```

Supported SSH vendor keys (aliases normalized):  
`cisco_ios`, `cisco_xe`, `cisco_nxos`, `juniper_junos`, `aruba_os`, `generic`.

---

## 14. Frontend

### Routes

| Path | Page |
|------|------|
| `/interfaces` | Inventory table |
| `/interfaces/:deviceId/:interfaceName` | Detail + charts |

### List page (`InterfacesPage`)

- Filters: search, oper status, admin status, port mode
- Columns: name, device, status, port mode, **classification badges**, access/voice/native VLAN, allowed VLANs, speed, duplex, neighbor (+ device type), utilization, updated
- Admin actions: Discover all, Collect stats
- Live utilization merged from per-device latest stats using **canonical name matching**

### Detail page (`InterfaceDetailPage`)

- Status / mode / classification / VLAN badges
- KPI cards: utilization, RX/TX bytes, broadcast/multicast, errors
- Details panel: all classification flags, voice VLAN, full neighbor enrichment
- History charts: utilization, broadcast/multicast, errors
- Recent samples table

### UI components

`InterfaceStatusBadge`, `PortModeBadge`, `PortClassificationBadges` / `ClassificationBadge`, `UtilizationBar`.

---

## 15. Credentials Resolution

### SSH (discovery + SSH stats)

Order:

1. `device.credentials.sshUsername` / `sshPassword` / `sshPort` / `sshSecret` / `sshVendor`
2. Env: `SSH_DEFAULT_*`

Missing username/password → `SSHCollectorError` (device marked failed, others continue).

Legacy SSH algorithms (SHA1 KEX, `ssh-rsa`, CBC) are preferred first for older campus gear.

### SNMP (stats)

Order:

1. `device.credentials.snmpCommunity` / `snmpVersion` / `snmpPort` / …
2. Env: `SNMP_DEFAULT_*`

API never returns secrets — only `sshPasswordConfigured`, `snmpCommunityConfigured`, etc.

---

## 16. Eligibility Rules

A device is included in **bulk** discovery/stats when it is `Online` **and**:

- Has a `credentials` sub-document, **or**
- `deviceType` is switch/router/firewall-like (exact set or contains those tokens)

Single-device discover/collect endpoints still require the device to be Online (and credentials for the chosen transport).

---

## 17. Error Handling & Soft-Fail Behavior

| Situation | Behavior |
|-----------|----------|
| Device offline | Skipped; `success: false`, error `"Device is not online"` |
| SSH auth/connect failure | Device fails; bulk job continues others |
| Optional command fails (CDP/LLDP/switchport/…) | Empty output; discovery continues |
| Required command fails (`status` / `terse`) | Device discovery fails |
| Invalid parsed row | Logged + skipped |
| Stats SNMP fails | Automatic SSH fallback |
| Unexpected exception | Logged with stack; never crashes scheduler |

Discovery and stats functions are designed to **never raise** to the scheduler.

---

## 18. Storm Protection Readiness

This module does **not** implement Storm Protection. It prepares the inventory contract for:

| Future engine | Consumes |
|---------------|----------|
| Port Eligibility | `portMode`, flags, neighbor type, monitoringEnabled |
| Risk Engine | Uplink/infra/management flags, neighbor identity, (later) stats rates |
| Confirmation Engine | Neighbor hostname/IP/platform/deviceType |
| Safety Checks | `isProtected`, `isManagement`, `isUplink` |
| Mitigation Engine | Classified interface documents only — no CLI re-parse |

Contract rule: Storm modules must read **normalized + classified** MongoDB/API objects, never raw CLI.

---

## 19. Operational Runbook

### First-time setup

1. Ensure MongoDB is running and backend `.env` is configured.
2. Add devices with SSH (and preferably SNMP) credentials.
3. Confirm devices show **Online** via ping monitoring.
4. As admin, open **Interfaces** → **Discover all**.
5. Optionally **Collect stats**, or wait for the stats scheduler.

### Verify indexes

On app startup, `ensure_interface_indexes()` and `ensure_interface_stats_indexes()` run automatically.

### Re-classification after code upgrades

Run Discover all again so existing documents pick up:

- Enriched neighbors
- Voice VLAN
- New classification flags

`isProtected=true` on existing docs is preserved.

### Common issues

| Symptom | Likely cause |
|---------|--------------|
| No interfaces | Device offline / no SSH creds / wrong vendor |
| Empty neighbors | CDP & LLDP disabled on switch |
| Missing VLANs / mode unknown | `switchport` unsupported or failed (soft-fail) |
| Utilization blank | Need ≥2 stats samples; or name mismatch (should be fixed by canonicalization) |
| Discover slow | Raise `MAX_INTERFACE_THREADS`; large `show switchport` output |

---

## 20. Extending the Module

### Add a new SSH command (Cisco)

1. Add key → command in `COMMAND_SETS` (`ssh_collector.py`).
2. Mark optional vs required.
3. Parse in `parser.py`.
4. Map fields in `normalizer.py` (and model/serializer/frontend if persisted).
5. Never put classification rules in the parser.

### Add a classification rule

Edit only `classifier.py`. Keep rules deterministic and documented.

### Add a vendor

1. Add command set in `ssh_collector.py`.
2. Add parser branch in `parse_interface_outputs`.
3. Reuse normalizer + classifier unchanged.

### Future SNMP inventory

Plug a new collector that returns the same raw-dict shape expected by `normalize_raw_interface` — persistence and API stay unchanged.

---

## Quick Reference — Public Python API

```python
from services.interface_collection import (
    discover_device_interfaces,
    discover_all_switch_interfaces,
    ensure_interface_indexes,
    get_interfaces,
    collect_device_interface_stats,
    collect_all_interface_stats,
    ensure_interface_stats_indexes,
    get_latest_device_stats,
    get_interface_stats_history,
)
```

Classifier helpers (for tests / Storm prep):

```python
from services.interface_collection.classifier import (
    classify_interface,
    classify_neighbor_device_type,
)
from services.interface_collection.naming import (
    canonicalize_interface_name,
    normalize_storage_interface_name,
)
```

---

## Summary

The Interface module is a layered, production-oriented inventory + telemetry subsystem:

1. **SSH discovery** builds a vendor-neutral interface inventory.
2. **CDP/LLDP enrichment** attaches topology context.
3. **Classifier** stamps eligibility-critical flags without touching the network.
4. **Name normalization** keeps inventory and stats aligned.
5. **SNMP/SSH stats** feed utilization and history.
6. **API + UI** expose everything operators and future Storm engines need.

Keep the pipeline order intact when changing code:

**SSH Collector → Parser → Normalizer → Classifier → MongoDB → API → Frontend**
