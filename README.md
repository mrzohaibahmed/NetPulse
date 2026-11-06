# NetPulse (Network Monitor)

Full-stack LAN monitoring and switch storm-protection platform. NetPulse continuously pings devices (and upstream ISP links across multiple sites), profiles them with Nmap, discovers hosts on your subnet, inventories Cisco and Juniper switch interfaces over SSH, tracks switch and HPE server hardware health (SNMP/SSH and iLO Redfish), visualizes CDP/LLDP topology, scores storm risk from live counters, and can automatically shut down / recover ports when a broadcast storm is confirmed safe to mitigate. Outages and recoveries are reported in-app, by email, and optionally by WhatsApp.

---

## Table of contents

- [What this project does](#what-this-project-does)
- [How it works](#how-it-works)
- [Hardware health monitoring](#hardware-health-monitoring)
- [Network topology](#network-topology)
- [Storm protection pipeline](#storm-protection-pipeline)
- [System architecture](#system-architecture)
- [Tech stack](#tech-stack)
- [Project structure](#project-structure)
- [MongoDB collections](#mongodb-collections)
- [Configuration](#configuration)
- [API overview](#api-overview)
- [Development setup](#development-setup)
- [First-time accounts](#first-time-accounts)
- [Troubleshooting](#troubleshooting)
- [Further documentation](#further-documentation)

---

## What this project does

NetPulse answers eight operational questions continuously:

1. **Is the device reachable?** — ICMP ping on a schedule (or on demand, including bulk **Ping all**).
2. **What is running on it?** — Periodic Nmap scans for OS, open ports, services, MAC, and vendor.
3. **What interfaces exist on the switch?** — SSH discovery (Cisco IOS/XE/NX-OS and Juniper Junos) of port inventory, VLANs, neighbors, and monitoring intent.
4. **How are switches connected?** — CDP/LLDP neighbor graphs rendered as an interactive topology map.
5. **Is the hardware healthy?** — Switch CPU, memory, fans, power supplies, temperature and inventory over SNMP/SSH; HPE server health over iLO 4/5/6 Redfish.
6. **Are the sites and ISP links up?** — Per-site server and ISP status for each configured location.
7. **Is a storm forming?** — Stats → eligibility → risk → confirmation → safety → prepare → mitigation.
8. **Did something important go down, or come back?** — In-app alerts plus optional email and WhatsApp when a **critical** device, ISP link or switch hardware component fails or recovers.

Operators use the React UI to:

| Page | Route | Purpose |
|------|-------|---------|
| **Enterprise Overview** (Dashboard) | `/` | Live KPIs, status donuts, response-time trends, **site monitoring**, **ISP connectivity**, recent activity |
| **Devices** | `/devices` | CRUD inventory, CSV import, manual **Ping** / **Ping all**, **Nmap scan all**, per-device ping, SSH, SNMP and iLO settings |
| **Server Hardware** | `/server-hardware` | HPE iLO health per server: power, processors, memory, storage, network, thermals, fans, firmware |
| **Discovery** | `/discovery` | Suggest local `/24` range and sweep IPs; auto-register new online hosts |
| **History** | `/history` | Filterable ping history and per-device uptime |
| **Reports** | `/reports` | Executive, availability, performance, alerts/incidents and storm reports; export devices, history and storm data as CSV or Excel |
| **Storm Protection** | `/storm` | Overview, risk analysis pipeline, incidents, mitigation and recovery views |
| **Interfaces** | `/interfaces` | Switch port inventory, discovery, stats, monitoring mode, manual shutdown / recover; per-port detail page |
| **Topology** | `/topology` | Level 1 switch-neighbor view and Level 2 live inventory graph from CDP/LLDP, with saved layouts |
| **Switches** | `/switches` | Switch fleet overview and per-switch detail |
| **Hardware Health** | `/switches/hardware` | Switch hardware fleet status; per-switch CPU/memory trends, fans, PSUs, temperature, inventory, hardware events and outage history |
| **Alerts** | `/alerts` | Acknowledge (individually or all at once) or dismiss alerts |
| **Users** (Account) | `/account` | Change username/password; admins manage users |
| **Settings** | `/settings` | Ping, SMTP, WhatsApp, ISP targets per site, mitigation mode, auto-recovery, hardware monitoring toggles, retention, history management |

Roles:

| Role | Access |
|------|--------|
| **admin** | Full write access (devices, discovery, settings, users, storm mitigation/recovery, on-demand hardware collection) |
| **user** | Dashboard, devices, server and switch hardware, interfaces, history, reports, alerts, topology, plus on-demand ping/Nmap (incl. bulk), alert ack/dismiss, and selected storm actions |

---

## How it works

### End-to-end flow

```
┌──────────────┐     JWT REST      ┌─────────────────┐     PyMongo     ┌──────────┐
│ React UI     │ ◄───────────────► │ Flask (app.py)  │ ◄─────────────► │ MongoDB  │
│ (Vite/TS)    │   poll 10–20s     │ + APScheduler   │                 │          │
└──────────────┘                   └────────┬────────┘                 └──────────┘
                                            │
     ┌──────────────┬───────────────┬───────┴───────┬────────────────┬────────────────┐
     ▼              ▼               ▼               ▼                ▼                ▼
 ICMP ping     Nmap profiling  SSH iface       SNMP/SSH stats   Switch hardware   iLO Redfish
 (~60s)        (~1 hour)       discovery       (~60s → storm)   SNMP ~60s,        server health
 ISP ping                      (~1 hour)       MAC/ARP polls    SSH ~10 min       (~5 min)
     │                         CDP/LLDP → topology graph (on read)
     ▼
 Alerts → in-app · SMTP email · WhatsApp (offline + recovery)
```

1. On startup, Flask loads settings, ensures indexes, seeds default users and per-site ISP slots if needed, and starts APScheduler (one leader process owns the jobs via `scheduler_locks`).
2. The **ping job** (dispatch mode) claims due monitored devices every few seconds and pings them with bounded concurrency; results go to `pingHistory` and may create offline or recovery alerts.
3. The **ISP monitor job** pings the configured upstream targets for each site on the same cadence as `pingInterval`.
4. The **Nmap job** scans currently **Online** devices and stores OS/ports under `networkInfo`.
5. The **interface discovery job** SSHs into eligible Cisco and Juniper switches and upserts the `interfaces` inventory (incl. CDP/LLDP neighbors).
6. The **stats + storm chain** polls counters, then runs analysis → confirmation → safety/prepare → optional auto-mitigation as separate scheduled stages.
7. **MAC/ARP jobs** passively poll switch MAC/ARP tables (~90s) and run an active ARP sweep (~30 min) for endpoint IP enrichment.
8. **Switch hardware jobs** collect CPU/memory (SNMP), environment (SSH) and inventory, then raise or resolve hardware alerts.
9. The **iLO job** polls HPE servers over Redfish and stores hardware health snapshots.
10. The **recovery job** (30s) evaluates MITIGATED / MONITORING incidents for auto-recovery and re-mitigation.
11. The **retention job** (daily) refreshes TTL indexes and purges closed incidents per settings.
12. The frontend authenticates with JWT and polls APIs so the UI stays live without WebSockets.

### 1. Automatic ping monitoring

**Where:** `backend/scheduler.py` → `services/monitor_service.py` → `services/ping_service.py`

1. With `MONITOR_RUNTIME_MODE=dispatch` (default), APScheduler runs the due-device **dispatcher** every `MONITOR_DISPATCHER_INTERVAL_SECONDS` (1–5s, default 5). Per-device cadence is Settings `pingInterval` (default **60s** from `SCAN_INTERVAL`), advanced via `nextCheckAt` at claim time—not a 60s “scan all devices” tick.
2. The dispatcher atomically claims due monitored devices up to free `pingConcurrency` worker slots (default **40**), then bounded workers run ICMP via `ping3`.
3. Results update the device:
   - **Success** → `Online`, reset `consecutiveFailures`, set `lastSeen` and `responseTime`
   - **Failure + critical** → after `pingFailureConfirmationScans` (default **2**) failed scans → `Offline (Critical)`
   - **Failure + non-critical** → after the same threshold → `Not Reachable`
4. Every check is stored in `pingHistory` with `scanType` of `Automatic` or `Manual`.
5. **Manual ping:** per-device `POST /api/devices/<id>/scan` or bulk `POST /api/devices/ping-all` (`user` or `admin`) from the Devices page — same `apply_ping_result` path as the scheduler, without partition suppression.
6. Changing `pingInterval` in Settings updates the per-device cadence (legacy mode also retargets the APScheduler period; dispatch mode keeps the fast dispatcher tick).

### 2. Nmap deep scanning

**Where:** `backend/scheduler.py` → `services/nmap_service.py` (also `routes/nmap_routes.py`)

1. Runs every `NMAP_SCAN_INTERVAL` seconds (default 3600).
2. Only **Online** devices are scanned.
3. A thread pool (`MAX_SCAN_THREADS`) runs Nmap with `NMAP_ARGUMENTS` (default `-A -T4`).
4. Results land on the device’s `networkInfo` and appear in the device drawer.
5. `user` and `admin` roles can trigger single-device or “scan all online” Nmap from the UI/API.

Requires the **Nmap binary** on `PATH` (or `NMAP_PATH`). Aggressive flags often need Administrator privileges on Windows.

### 3. Subnet discovery

**Where:** `services/discovery_service.py` via `POST /api/discovery/scan-range`

1. `GET /api/discovery/network-hint` suggests a local `/24` range.
2. A thread pool pings hosts in the range.
3. Online hosts get best-effort reverse DNS.
4. Unknown hosts can be auto-saved (`deviceType: Unknown`, `monitor: true`) so they enter the ping loop immediately.

Discovery logic (classification, enrichment, identity management, SSH hostname lookup) lives in `services/discovery/`.

### 4. Switch interface discovery & stats

**Where:** `services/interface_collection/` (see also [`backend/services/interface_collection/README.md`](backend/services/interface_collection/README.md))

1. **Discovery** (SSH) parses interface status, switchport, CDP/LLDP, and classifies access / trunk / uplink / protected ports. Cisco IOS/XE/NX-OS and Juniper Junos (`juniper_parser.py`) are supported.
2. Documents are upserted into `interfaces` with monitoring intent (`AUTO`, `DISABLED_BY_USER`, …).
3. **Stats** prefer SNMP counters and fall back to SSH; samples are written to `interface_stats`.
4. MAC/ARP tables are polled into `port_mac_table` / `arp_cache` to map endpoints to ports (`MAC_ARP_POLL_INTERVAL`, default 90s; `ARP_ACTIVE_SWEEP_INTERVAL`, default 1800s).
5. From the Interfaces UI (or API), admins can:
   - Change monitoring mode per port
   - Trigger **manual shutdown** (creates a MANUAL incident + mitigation)
   - Trigger **manual recover** for a mitigated incident

### 5. Alerting and notifications

**Where:** `services/alert_service.py`, `services/email_service.py`, `services/whatsapp_service.py`, `services/isp_alert_service.py`, `services/switch_hardware/alerts.py`

Device outage alerts apply only to devices with **`critical: true`**. Non-critical failures become **Not Reachable** with no alert or notification.

1. After **`pingFailureConfirmationScans`** (default **2**) consecutive failed scans, status becomes `Offline (Critical)`.
2. An in-app alert is created once **`consecutiveFailures >= 3`** and no active alert exists for that device (so the alert can lag the UI status by one scan cycle).
3. If SMTP is enabled and fully configured (host, user, password, from, to), email is sent in the same step; the alert stores `emailSent: true/false`. If WhatsApp is enabled, a template message is sent too.
4. At most **one active** critical-offline alert per device (unique index + idempotent insert).
5. When the device returns **Online**, the offline alert is resolved and a **recovery alert** is created, with a recovery email and WhatsApp message.
6. **ISP links** raise offline and recovery alerts and emails the same way.
7. **Switch hardware** (fans, PSUs, temperature, CPU/memory thresholds) raises one alert per hardware condition and resolves it, with a recovery email, when the condition clears.
8. `user` and `admin` roles acknowledge (individually or **Acknowledge all**) or dismiss alerts.

**Channels:**

| Channel | Configure | Notes |
|---------|-----------|-------|
| **Email (SMTP)** | **Settings → SMTP alerts** or `.env` (`EMAIL_PROVIDER`, `ALERT_EMAIL_*`, `SMTP_*`) | `EMAIL_PROVIDER` is `gmail` (default) or `outlook` (also `microsoft365` / `office365`). **Send test email** in Settings (`POST /api/settings/test-email`). |
| **WhatsApp** | `.env` (`WHATSAPP_*`) and **Settings** | Meta WhatsApp Cloud API with approved templates; disabled by default. Test with `POST /api/settings/test-whatsapp`. Setup: [`backend/docs/WHATSAPP_ALERTS.md`](backend/docs/WHATSAPP_ALERTS.md). |
| **Storm emails** | Settings `stormNotifications` | Shutdown / recovery / failure emails to a separate recipient. |

### 6. ISP upstream and multi-site monitoring

**Where:** `services/isp_monitor_service.py`, `services/site_monitoring_service.py`, `models/location.py`, `routes/isp_routes.py`, Dashboard **Site Monitoring** / **ISP Connectivity**

1. Sites are defined in `models/location.py` (`Mills` (default), `Karachi`, `Lahore`), with **three ISP slots per site** (`ispConnections`), seeded on first boot. Legacy slots without a location are migrated to the default site.
2. Each slot has a ping target (IP or hostname); the scheduler pings them on `pingInterval`.
3. Devices carry a `location`; `GET /api/dashboard/site-monitoring` groups servers and ISP links by site for the dashboard.
4. Dashboard shows live status/latency; Settings lets admins configure targets per site.
5. Manual scan: `POST /api/isps/<id>/scan`.

### 7. Authentication and roles

**Where:** `utils/auth.py`, `services/user_service.py`, `routes/auth_routes.py`

- Login returns a JWT (`JWT_SECRET`, `JWT_EXPIRE_HOURS`).
- Passwords are stored with bcrypt.
- SSH / SMTP secrets at rest are encrypted with Fernet (`SECRETS_ENCRYPTION_KEY`).
- Roles inherit privileges: `admin` ⊃ `user`.
- First boot with an empty `users` collection seeds default admin and user accounts.
- Legacy stored roles (`viewer`, `operator`, `super-admin`) are normalized to `user` or `admin` on startup.

### 8. Frontend data loading

**Where:** `frontend/src/hooks/queries.ts`, Vite proxy in `vite.config.ts`

- TanStack Query polls the API (dashboard ~10s, devices ~15s, history ~20s, storm panels as configured).
- In development, Vite proxies `/api` and `/health` to `http://127.0.0.1:5000`.
- In production, `npm run build` produces `frontend/dist`; Flask serves that SPA when present.

---

## Hardware health monitoring

### Switch hardware

**Where:** `services/switch_hardware/`, `routes/switch_hardware_routes.py`, pages `/switches/hardware` and `/switches/:deviceId/hardware`. Deep dive: [`docs/SWITCH_HARDWARE_MONITORING.md`](docs/SWITCH_HARDWARE_MONITORING.md).

| Item | Detail |
|------|--------|
| **Vendors** | Cisco IOS/XE, Cisco NX-OS, Juniper Junos (`parsers/`, `vendor_detection.py`) |
| **Eligibility** | Monitored Cisco or Juniper switch/router/firewall with SSH or SNMP credentials; switch-like devices with an unknown vendor are tried with the Cisco IOS command set |
| **Collected** | CPU, memory, fans, power supplies, temperature sensors, inventory (modules, serials) |
| **Jobs** | `switch_hardware_snmp_job` (CPU/memory, `SWITCH_HARDWARE_POLL_INTERVAL`, default 60s), `switch_hardware_ssh_job` (environment, default 600s), `switch_hardware_inventory_job` (default 3600s) |
| **Health** | `health_evaluator.py` applies CPU/memory warning and critical thresholds (`SWITCH_HARDWARE_*_PERCENT`) and component status |
| **Events and outages** | Hardware state changes go to `switch_hardware_events`; `outage_analyzer.py` records switch outages with an evidence-based root cause in `switch_outage_incidents` |
| **Storage** | `switch_hardware_current` (latest snapshot), `switch_hardware_history` (TTL `switchHardwareHistoryRetentionDays`) |
| **Toggle** | Settings `switchHardwareMonitoringEnabled` (seeded from `SWITCH_HARDWARE_MONITORING_ENABLED`, default **false**) |

### Server hardware (HPE iLO)

**Where:** `services/server_hardware/`, `utils/redfish_client.py`, page `/server-hardware`

| Item | Detail |
|------|--------|
| **Protocol** | Redfish over HTTPS to HPE iLO 4, iLO 5 and iLO 6 |
| **Per-device config** | `iloAddress`, `iloPort`, `iloUsername`, `iloPassword` (encrypted at rest) in the device form |
| **Collected** | System, firmware, power supplies, processors, memory DIMMs, storage, network adapters, thermals, fans |
| **Health** | `health.py` evaluates each subsystem and an aggregate status |
| **Job** | `ilo_hardware_poll_job`, every `SERVER_HARDWARE_POLL_INTERVAL_SECONDS` (default 300s) |
| **Storage** | `server_hardware_current`, `server_hardware_history`, `iloCollectionState` (last attempt / success / error) |
| **Toggle** | Settings `serverHardwareMonitoringEnabled` (seeded from `SERVER_HARDWARE_MONITORING_ENABLED`, default **true**) |

---

## Network topology

**Where:** `services/topology_service.py`, `services/topology_layout_service.py`, `routes/topology.py`, `frontend/src/modules/storm/pages/TopologyPage.tsx`

Interactive CDP/LLDP neighbor graphs built **on read** from MongoDB `interfaces.neighbor` — there is no separate topology collection or WebSocket feed.

### Data sources

| Source | Role |
|--------|------|
| **SSH CDP/LLDP** (Cisco IOS/XE/NX-OS, Juniper LLDP) | Primary — neighbor detail commands during interface discovery |
| **Inventory match** | Neighbor IP/hostname matched to known devices |
| **ARP/MAC** | Optional endpoint IP enrichment via `port_mac_table` / `arp_cache` |
| **SNMP** | Not used for topology neighbors |

### Views

| View | API | Behavior |
|------|-----|----------|
| **Level 1 — Switch neighbors** | `GET /api/topology/switch/<device_id>` | One switch + direct neighbors; stale/offline links shown dashed |
| **Level 2 — Full topology** | `GET /api/topology/full` | **Live only** — edges where **both** ends are inventory devices with status **Online** |

Level 2 intentionally excludes unresolved CDP neighbors and offline links. An empty Full Topology with a populated Level 1 view usually means peers are not Online inventory devices — not a broken pipeline.

Neighbor data refreshes on `INTERFACE_SCAN_INTERVAL` (default hourly). The UI polls topology every 30–60s for device status changes. The canvas uses React Flow with dagre auto-layout; node positions can be saved per view in `topology_layouts`.

---

## Storm protection pipeline

Storm protection runs after each interface-stats cycle (unless disabled via env flags). It is append-only history with live-state gates — there is **no pipeline generation / versioning counter**.

```
Interface Stats
      ↓
Eligibility          (access port? monitoring on? not uplink/trunk/protected?)
      ↓
Risk Score           (broadcast / multicast / unknown unicast / util / errors / …)
      ↓
Confirmation         (consecutive high-risk samples → CONFIRMED)
      ↓
Safety Engine        (device online, SSH OK, not already shut, cooldown, …)
      ↓
Orchestrator Prepare (live CONFIRMED + current risk + fresh SAFE required)
      ↓
Incident + Diagnostics snapshot
      ↓
Mitigation           (SHUTDOWN) — automatic or admin-triggered
      ↓
Recovery             (NO_SHUTDOWN / no shutdown) — policy + Recovery Safety
      ↓
Post-recovery reset  (confirmation reset + safety invalidate + cancel orphan READY)
      ↓
MONITORING           (recoveredAt + stabilization window)
      ↓
RESOLVED  or  re-mitigate if a *fresh* storm appears after recoveredAt
```

### Mitigation modes

Controlled by Settings `mitigationMode`:

| Mode | Behavior |
|------|----------|
| **`manual`** (default) | Pipeline stops after prepare (`READY_FOR_MITIGATION`). Admin executes shutdown / recovery from Storm Protection or Interfaces. |
| **`automatic`** | Scheduler shuts down ready incidents via the Mitigation Engine after prepare. |

### Recovery protections (kept intact)

After a successful recovery verification the engine:

1. Sets incident status to **MONITORING** and writes **`recoveredAt`**
2. **Resets confirmation** to `NOT_CONFIRMED`
3. **Invalidates safety** with a post-recovery UNSAFE row
4. **Cancels orphan** `OPEN` / `PREPARED` / `READY_FOR_MITIGATION` incidents on that interface
5. Returns to monitoring for the stabilization window

Additional gates:

- **Recovery Safety Engine** (rules R0–R8) and **Recovery Policy** before locks / SSH
- **Orchestrator live CONFIRMED gating** — prepare never trusts stale SAFE history alone
- **Stale SAFE protection** — safety must be newer than the current confirmation
- **Re-mitigation freshness** — confirmation / risk after `recoveredAt` only
- Lightweight mitigation verification and recovery verification before status transitions

### Key settings

| Setting | Meaning | Default |
|---------|---------|---------|
| `pingInterval` | Per-device monitoring cadence (seconds) | `60` |
| `pingFailureConfirmationScans` | Failed scans before status leaves Online | `2` |
| `pingConcurrency` | Max parallel ping workers | `40` |
| `mitigationMode` | `automatic` \| `manual` | `manual` |
| `autoRecovery` | Scheduler may recover MITIGATED ports | `true` |
| `cooldownMinutes` | Wait after mitigation before recovery | `5` |
| `stabilizationSeconds` | MONITORING window after recovery | `60` |
| `maximumRecoveryAttempts` | Cap before `RECOVERY_FAILED` | `3` |
| `reMitigationThreshold` | Risk score that can re-trigger after recovery | `60` |
| `pingHistoryRetentionDays` | TTL for `pingHistory` only | `7` |
| `dataRetentionDays` | TTL for interface stats and storm evaluation history | `90` |
| `switchHardwareHistoryRetentionDays` | TTL for switch hardware history | `90` |
| `switchHardwareMonitoringEnabled` | Run switch hardware jobs | `false` |
| `serverHardwareMonitoringEnabled` | Run iLO polling | `true` |
| `incidentRetentionDays` | Retention for closed incidents + attempt logs | `365` |
| `stormNotifications` | Enable storm emails (shutdown / recovery / failure) + recipient | enabled |

Deep dive on interface collection: [`backend/services/interface_collection/README.md`](backend/services/interface_collection/README.md).

---

## System architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    React frontend (NetPulse)                │
│         Vite + TypeScript + Tailwind + TanStack Query       │
│  Overview · Devices · Server/Switch Hardware · Interfaces · │
│  Storm · Topology · Switches · Discovery · Reports · …      │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP + Bearer JWT
┌──────────────────────────────▼──────────────────────────────┐
│                   Flask backend (app.py)                    │
│  Blueprints: auth, devices, isps, nmap, scan, history,      │
│  dashboard, discovery, interfaces, storm, alerts, settings, │
│  reports, topology, switch_hardware                         │
│                                                             │
│  APScheduler (leader-elected via scheduler_locks)           │
│  • device_monitor_job        → ping monitoring (dispatch)   │
│  • isp_monitor_job           → per-site ISP ping targets    │
│  • nmap_scan_job             → Online device profiling      │
│  • interface_discovery_job   → SSH inventory + CDP/LLDP     │
│  • interface_stats_job       → counter collection           │
│  • storm_analysis_job        → eligibility + risk           │
│  • storm_confirmation_job    → confirmation                 │
│  • storm_safety_prepare_job  → safety + prepare (+ auto)    │
│  • storm_recovery_job        → auto-recovery / re-mitigation│
│  • mac_arp_poll_job          → passive MAC/ARP tables       │
│  • arp_active_sweep_job      → active ARP sweep             │
│  • switch_hardware_snmp_job  → CPU / memory                 │
│  • switch_hardware_ssh_job   → fans, PSU, temperature       │
│  • switch_hardware_inventory_job → modules / serials        │
│  • ilo_hardware_poll_job     → HPE iLO Redfish health       │
│  • data_retention_job        → TTL + closed-incident purge  │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│                         MongoDB                             │
│  Core: devices · pingHistory · alerts · settings · users ·  │
│  ispConnections · auditLogs · login_rate_limits ·           │
│  scheduler_locks · topology_layouts                         │
│  Interfaces: interfaces · interface_stats · port_mac_table ·│
│  arp_cache                                                  │
│  Storm: eligibility_results · storm_risk_history/latest ·   │
│  storm_confirmation_history · storm_safety_history ·        │
│  storm_incidents · storm_pipeline_cycles ·                  │
│  storm_mitigation_history · storm_recovery_history ·        │
│  storm_*_locks                                              │
│  Hardware: switch_hardware_current/history/events ·         │
│  switch_outage_incidents · server_hardware_current/history ·│
│  iloCollectionState                                         │
└─────────────────────────────────────────────────────────────┘
```

---

## Tech stack

| Layer | Technologies |
|-------|----------------|
| Frontend | React 19, TypeScript, Vite 8, Tailwind CSS 4, React Router 7, TanStack Query/Table, Recharts, React Flow (`@xyflow/react`) + dagre, Radix UI, Framer Motion, react-hook-form + zod, sonner |
| Backend | Flask 3, Flask-CORS, APScheduler, PyMongo; Waitress / Gunicorn for serving |
| Monitoring | `ping3` (ICMP), `python-nmap` (Nmap), Paramiko (SSH), `pysnmp` (SNMP), `requests` (iLO Redfish) |
| Notifications | SMTP (Gmail / Outlook), Meta WhatsApp Cloud API |
| Auth / secrets | PyJWT, bcrypt, cryptography (Fernet), login rate limiting |
| Export | openpyxl (Excel), CSV |
| Testing | pytest (backend), Vitest + Testing Library (frontend), oxlint |
| Database | MongoDB |

---

## Project structure

```
NetPulse/
├── README.md
├── USER_MANUAL.md                  # Operator guide
├── PRESENTATION_REPORT.md
├── start.bat / stop.bat            # Windows launcher for the production build
├── docs/
│   ├── INSTALLATION_GUIDE.md       # Full install + deployment walkthrough
│   └── SWITCH_HARDWARE_MONITORING.md
├── backend/
│   ├── app.py                      # Flask app, indexes, bootstrap, SPA hosting
│   ├── scheduler.py                # All APScheduler jobs (see System architecture)
│   ├── run_scheduler.py            # Run the scheduler as its own process
│   ├── gunicorn.conf.py
│   ├── requirements.txt
│   ├── .env / .env.example
│   ├── DEPLOYMENT.md
│   ├── docs/WHATSAPP_ALERTS.md
│   ├── config/                     # MongoDB, CORS, deployment, email, WhatsApp, Nmap
│   ├── models/                     # Device, interface, ping history, ISP, location
│   ├── routes/                     # REST blueprints (incl. storm, topology, switch hardware)
│   ├── services/
│   │   ├── discovery/              # Host classification, enrichment, identity
│   │   ├── interface_collection/   # SSH discovery (Cisco + Juniper), stats, MAC/ARP
│   │   ├── switch_hardware/        # SNMP/SSH hardware collectors, health, alerts, outages
│   │   ├── server_hardware/        # iLO Redfish collector, parsers, health
│   │   ├── storm/                  # Eligibility → risk → confirm → safety → …
│   │   │   ├── analyzers/          # Broadcast, multicast, unknown unicast, errors, …
│   │   │   ├── diagnostics/        # Read-only evidence capture
│   │   │   ├── mitigation/         # Shutdown engine, verifier, audit
│   │   │   └── recovery/           # Policy, safety, engine, post-recovery
│   │   ├── topology_service.py     # CDP/LLDP graph builder (on-read)
│   │   ├── topology_layout_service.py
│   │   ├── site_monitoring_service.py
│   │   ├── whatsapp_service.py
│   │   └── …                       # Ping, monitor dispatch, Nmap, alerts, email, retention, …
│   ├── tests/                      # pytest suite (+ fixtures/redfish)
│   ├── tools/                      # Capacity / load validation scripts
│   ├── utils/                      # JWT, Redfish client, secrets, SSRF guard, logging
│   └── logs/monitor.log
└── frontend/
    ├── src/
    │   ├── App.tsx                 # Routes
    │   ├── api/                    # Endpoint helpers (core, topology, switch hardware)
    │   ├── hooks/                  # React Query hooks (polling)
    │   ├── lib/                    # Health, status, theme helpers
    │   ├── modules/
    │   │   ├── ping/               # Dashboard, devices, server hardware, discovery, history, reports
    │   │   ├── storm/              # Storm, interfaces, topology, switches, hardware health
    │   │   └── settings/           # Settings sections
    │   ├── pages/                  # Login, alerts, account, settings
    │   ├── shared/                 # API client, auth context, layout, UI kit
    │   └── types/
    ├── package.json
    ├── vitest.config.ts
    └── vite.config.ts              # Dev server + /api proxy
```

More detail: [`backend/README.md`](backend/README.md), [`frontend/README.md`](frontend/README.md).

---

## MongoDB collections

| Collection | Purpose |
|------------|---------|
| `devices` | Inventory, status, location, ping overrides, SSH/SNMP/iLO creds, Nmap `networkInfo` |
| `pingHistory` | Time-series of every manual/automatic ping |
| `alerts` | Critical offline/recovery, ISP, switch hardware and storm alerts (acknowledge / dismiss) |
| `settings` | Global ping, SMTP, WhatsApp, storm mitigation/recovery, hardware toggles, retention |
| `users` | Accounts with bcrypt password hashes |
| `login_rate_limits` | Failed-login throttling |
| `ispConnections` | Upstream ISP ping targets (three per site) |
| `auditLogs` | Admin / storm action trail |
| `scheduler_locks` | Scheduler leadership leases |
| `topology_layouts` | Saved topology canvas positions |
| `interfaces` | Discovered switch ports + CDP/LLDP `neighbor` + monitoring intent |
| `interface_stats` | Counter / rate samples for risk scoring |
| `port_mac_table` / `arp_cache` | MAC/ARP tables for endpoint IP enrichment |
| `eligibility_results` | Latest eligibility decisions |
| `storm_risk_history` / `storm_risk_latest` | Append-only risk scores / latest score per interface |
| `storm_pipeline_cycles` | Per-stage pipeline run leases and status |
| `storm_confirmation_history` | Append-only confirmation / reset rows |
| `storm_safety_history` | Append-only safety evaluations |
| `storm_incidents` | Storm + manual incidents, timeline, `recoveredAt` |
| `storm_mitigation_history` | Mitigation attempt audit |
| `storm_recovery_history` | Recovery attempt audit (incl. blocked policy) |
| `storm_mitigation_locks` / `storm_recovery_locks` | Lease locks (TTL) |
| `switch_hardware_current` / `switch_hardware_history` | Latest switch hardware snapshot / polling history |
| `switch_hardware_events` | Hardware state-change events |
| `switch_outage_incidents` | Switch outages with root-cause analysis |
| `server_hardware_current` / `server_hardware_history` | Latest iLO health snapshot / history |
| `iloCollectionState` | Per-server iLO collection attempt / success / error |

**Device status values:** `Online`, `Not Reachable`, `Offline (Critical)`, `Unknown`.

**Common incident statuses:** `OPEN` → `READY_FOR_MITIGATION` → `MITIGATED` → `MONITORING` → `RESOLVED` (also `MITIGATION_FAILED`, `RECOVERY_FAILED`, `CANCELLED`, …).

---

## Configuration

Copy and edit `backend/.env` from `backend/.env.example`. Core variables:

```env
# Database
MONGO_URI=mongodb://localhost:27017
DATABASE_NAME=NetworkMonitor

# Flask (keep false in production / shared hosts)
FLASK_DEBUG=false

# Auth + secrets at rest
JWT_SECRET=change-me-in-production
JWT_EXPIRE_HOURS=8
SECRETS_ENCRYPTION_KEY=replace-with-fernet-generate-key-output
DEFAULT_ADMIN_USER=admin
DEFAULT_ADMIN_PASSWORD=
DEFAULT_USER_NAME=user
DEFAULT_USER_PASSWORD=

# Ping defaults (also adjustable in Settings UI; runtime SoT is Mongo `settings`)
SCAN_INTERVAL=60
PING_TIMEOUT_MS=1000
PING_RETRIES=3
PING_FAILURE_CONFIRMATION_SCANS=2
MONITOR_PING_CONCURRENCY=40
MONITOR_RUNTIME_MODE=dispatch
MONITOR_DISPATCHER_INTERVAL_SECONDS=5

# Email alerts (optional) — EMAIL_PROVIDER: gmail | outlook
EMAIL_PROVIDER=gmail
ALERT_EMAIL_ENABLED=true
ALERT_EMAIL_TO=recipient@example.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=sender@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=sender@gmail.com
SMTP_FROM_NAME=NetPulse
SMTP_USE_TLS=true

# WhatsApp alerts (optional, Meta WhatsApp Cloud API — see backend/docs/WHATSAPP_ALERTS.md)
WHATSAPP_ALERTS_ENABLED=false
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_RECIPIENT_NUMBERS=           # comma-separated E.164 without +
WHATSAPP_CRITICAL_ALERT_TEMPLATE=netpulse_critical_alert
WHATSAPP_RECOVERY_ALERT_TEMPLATE=netpulse_device_recovery

# Nmap
NMAP_SCAN_INTERVAL=3600
NMAP_ARGUMENTS=-A -T4
MAX_SCAN_THREADS=5
NMAP_TIMEOUT=300
NMAP_PATH=

# Interface discovery + stats
INTERFACE_SCAN_INTERVAL=3600
INTERFACE_STATS_INTERVAL=60
MAX_INTERFACE_THREADS=5
MAX_INTERFACE_STATS_THREADS=8
SSH_DEFAULT_USERNAME=
SSH_DEFAULT_PASSWORD=
SSH_DEFAULT_VENDOR=cisco_ios
SNMP_DEFAULT_COMMUNITY=public
MAC_ARP_POLL_INTERVAL=90
ARP_ACTIVE_SWEEP_INTERVAL=1800

# Switch hardware monitoring
SWITCH_HARDWARE_MONITORING_ENABLED=false
SWITCH_HARDWARE_POLL_INTERVAL=60          # SNMP CPU/memory
SWITCH_HARDWARE_SSH_INTERVAL=600          # fans, PSU, temperature
SWITCH_HARDWARE_INVENTORY_INTERVAL=3600
SWITCH_HARDWARE_CPU_WARNING_PERCENT=80
SWITCH_HARDWARE_CPU_CRITICAL_PERCENT=95
SWITCH_HARDWARE_MEMORY_WARNING_PERCENT=85
SWITCH_HARDWARE_MEMORY_CRITICAL_PERCENT=95

# Server hardware (HPE iLO) — not in .env.example; defaults shown
SERVER_HARDWARE_MONITORING_ENABLED=true
SERVER_HARDWARE_POLL_INTERVAL_SECONDS=300

# Retention
PING_HISTORY_RETENTION_DAYS=7
DATA_RETENTION_DAYS=90
INCIDENT_RETENTION_DAYS=365
SWITCH_HARDWARE_HISTORY_RETENTION_DAYS=90

# Storm (high-level; many thresholds live in .env.example)
STORM_ENABLE_ELIGIBILITY=true
STORM_ENABLE_RISK=true
STORM_MITIGATION_MODE=manual
STORM_AUTO_RECOVERY=true
STORM_RE_MITIGATION_THRESHOLD=75
```

| Variable | Meaning |
|----------|---------|
| `MONGO_URI` / `DATABASE_NAME` | MongoDB connection (required) |
| `JWT_*` / `SECRETS_ENCRYPTION_KEY` | Token signing + encrypted SSH/SMTP secrets |
| `SCAN_INTERVAL` | Default ping interval (seconds) |
| `PING_FAILURE_CONFIRMATION_SCANS` | Failed scans before leaving `Online` (default 2) |
| `MONITOR_PING_CONCURRENCY` | Max parallel ping workers (1–64, default 40) |
| `MONITOR_RUNTIME_MODE` | `dispatch` (default) or `legacy` wave scheduler |
| `MONITOR_DISPATCHER_INTERVAL_SECONDS` | Dispatch tick when mode is `dispatch` (1–5s) |
| `INTERFACE_SCAN_INTERVAL` | SSH rediscovery interval (`0` disables schedule) |
| `INTERFACE_STATS_INTERVAL` | Stats + storm chain interval (`0` disables) |
| `STORM_MITIGATION_MODE` | Bootstrap default for Settings `mitigationMode` |
| `EMAIL_PROVIDER` | SMTP sender provider: `gmail` (default) or `outlook` |
| `WHATSAPP_*` | WhatsApp Cloud API credentials, recipients and templates |
| `SWITCH_HARDWARE_*` | Switch hardware toggle, intervals, CPU/memory thresholds, history retention |
| `SERVER_HARDWARE_*` | iLO polling toggle (seeds Settings) and interval |
| `PING_HISTORY_RETENTION_DAYS` | `pingHistory` retention (default 7) |
| `DATA_RETENTION_DAYS` / `INCIDENT_RETENTION_DAYS` | Telemetry history / closed-incident retention |

Use `NMAP_ARGUMENTS=-sV -T4` if you cannot run elevated. Prefer per-device SSH credentials via the Devices UI over global `SSH_DEFAULT_*`.

---

## API overview

All JSON APIs are under `/api` except `/health`. Most routes require `Authorization: Bearer <token>`.

### Core

| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/auth/login` | Login, returns JWT |
| GET | `/api/auth/me` | Current user |
| PUT | `/api/auth/account` | Update own account |
| GET/PUT | `/api/users` … | User management (admin+) |
| CRUD | `/api/devices` … | Device inventory; `POST /api/devices/import` for CSV; `GET /api/devices/networks` |
| POST | `/api/devices/<id>/scan` | Manual ICMP ping (`user` or `admin`) |
| POST | `/api/devices/ping-all` | Bulk manual ping — all inventory devices (`user` or `admin`) |
| POST | `/api/devices/<id>/scan-details` | Manual Nmap scan (`user` or `admin`) |
| POST | `/api/devices/scan-all-details` | Bulk Nmap on all Online devices (`user` or `admin`) |
| GET | `/api/history` · `/api/devices/<id>/history` | Ping history |
| CRUD | `/api/isps` … | ISP upstream targets (admin configure; all roles read) |
| POST | `/api/isps/<id>/scan` | Manual ISP ping |
| GET | `/api/discovery/network-hint` | Suggest LAN range |
| POST | `/api/discovery/scan-range` | Subnet sweep |
| GET | `/api/dashboard/*` | Summary, stats, charts, ops metrics |
| GET | `/api/dashboard/site-monitoring` | Servers and ISP links grouped by site |
| GET | `/api/alerts` | List alerts |
| POST | `/api/alerts/<id>/acknowledge` · `/dismiss` | Acknowledge / dismiss one alert |
| POST | `/api/alerts/acknowledge-all` | Bulk acknowledge |
| GET/PUT | `/api/settings` | Global settings (incl. storm, hardware, retention) |
| DELETE | `/api/settings/history` | Scoped history deletion (admin, history management) |
| POST | `/api/settings/test-email` · `/test-whatsapp` | Send a test notification |
| GET | `/api/reports/*` | Uptime, executive, availability, performance, alerts/incidents, storm |
| GET | `/api/reports/export/*` | CSV/XLSX export (devices, history, storm incidents/mitigations/recoveries) |
| GET | `/health` | Server + MongoDB ping |

### Hardware

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/devices/<id>/server-hardware/health` | iLO server hardware health + collection state |
| GET | `/api/switches/<id>/hardware` | Latest switch hardware snapshot |
| GET | `/api/switches/<id>/hardware/history` | CPU/memory/temperature history |
| GET | `/api/switches/<id>/hardware/events` | Hardware state-change events |
| GET | `/api/switches/<id>/outages` · `/outages/<incident_id>` | Outage history + root-cause detail |
| POST | `/api/switches/<id>/hardware/collect` | Collect now (admin) |

### Topology

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/topology/switches` | List switch devices for the topology picker |
| GET | `/api/topology/switch/<device_id>` | Level 1 — switch + neighbors (incl. stale links) |
| GET | `/api/topology/full` | Level 2 — live Online↔Online inventory graph only |
| GET/PUT | `/api/topology/layout` | Load / save canvas node positions for a view |

### Interfaces

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/interfaces` | List / filter discovered interfaces |
| GET | `/api/interfaces/<device_id>` | Interfaces for one device |
| POST | `/api/interfaces/discover-all` | Bulk SSH discovery |
| POST | `/api/interfaces/discover/<device_id>` | Discover one device |
| POST | `/api/interfaces/stats/collect-all` | Bulk stats poll |
| POST | `/api/interfaces/<device_id>/stats/collect` | Stats for one device |
| POST | `/api/interfaces/<device_id>/<iface>/monitoring` | Set monitoring mode |
| POST | `/api/interfaces/<device_id>/<iface>/manual-shutdown` | Operator shutdown |
| POST | `/api/interfaces/<device_id>/<iface>/manual-recover` | Operator recovery |
| GET | `/api/interfaces/<device_id>/<iface>/history` | Stats history |

### Storm protection

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/storm/config` | Effective storm config |
| POST | `/api/storm/*/evaluate` / `evaluate-all` | Run eligibility / risk / confirmation / safety |
| GET | `/api/storm/eligibility` · `/risk` · `/confirmation` · `/safety` | History queries |
| GET | `/api/storm/incidents` … | Incident list / detail |
| POST | `/api/storm/orchestrator/prepare` · `prepare-all` | Prepare mitigation |
| POST | `/api/storm/mitigation/execute` · `rollback` | Shutdown / rollback |
| GET | `/api/storm/mitigation/history` … | Mitigation audit |
| POST | `/api/storm/recovery/execute` · `retry` | Recovery |
| GET | `/api/storm/recovery/history` … | Recovery audit |

Full endpoint tables: [`backend/README.md`](backend/README.md).

---

## Development setup

### Prerequisites

- Python 3.10+
- Node.js 18+ (frontend)
- MongoDB (local or Atlas)
- [Nmap](https://nmap.org/download.html) for deep scans
- SSH reachability to managed switches for interface / storm features
- On Windows, run the terminal **as Administrator** for reliable ICMP and aggressive Nmap

### 1. Backend

```powershell
cd backend
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
# Create backend/.env from .env.example
python app.py
```

API: `http://127.0.0.1:5000`

### 2. Frontend (dev)

```powershell
cd frontend
npm install
npm run dev
```

UI: `http://127.0.0.1:5173` (proxies `/api` to Flask)

### 3. LAN / production UI (recommended for other computers on the network)

Use the **production React build** served by Flask. Do **not** point LAN users at the Vite dev server.

| Port | Purpose |
|------|---------|
| **5173** | Frontend development only (`npm run dev`) |
| **5000** | Flask API + production UI (`npm run build` then start backend) |

```powershell
cd frontend
npm install
npm run build

cd ..\backend
.\venv\Scripts\activate
python app.py
```

Open from any computer on the LAN:

```text
http://<HOST-LAN-IP>:5000
```

`python app.py` binds to `0.0.0.0:5000` by default (localhost still works on the host). Set `FLASK_RUN_HOST=127.0.0.1` in `backend/.env` for localhost-only.

With Gunicorn instead of `python app.py`:

```powershell
$env:GUNICORN_BIND = "0.0.0.0:5000"
gunicorn -c gunicorn.conf.py "app:app"
```

**Validate on a LAN client:** DevTools → Network should show a few `assets/*.js` / `assets/*.css` files, not hundreds of `/src/*.tsx` modules.

### 4. Single-process UI on localhost (optional)

Same build as above, but bind to localhost only (default):

```powershell
cd frontend
npm run build
cd ..\backend
python app.py
```

Flask serves `frontend/dist` at `http://127.0.0.1:5000`.

### 5. Tests

Backend (pytest, ~100 test modules):

```powershell
cd backend
.\venv\Scripts\activate
python -m pytest tests -q
```

Frontend (Vitest + Testing Library) and lint:

```powershell
cd frontend
npm test
npm run lint
```

Backend coverage spans ping dispatch and monitor runtime, alerts and notifications (email, WhatsApp, ISP), discovery, interface collection (incl. Juniper), switch hardware parsers/SNMP/alerts, iLO Redfish parsing (fixtures for iLO 4/5/6), reports, retention, and the full storm pipeline (eligibility, confirmation, safety, orchestrator, mitigation, recovery).

---

## First-time accounts

The login page does **not** display, auto-fill, or hint any passwords.

When the `users` collection is empty, the **backend** seeds accounts at startup (`ensure_default_admin`). Seeded users have `mustChangePassword=true` and must change their password before using the rest of the app. There is no `POST /api/users` create-user API.

| Environment | How the first accounts are created |
|-------------|-------------------------------------|
| **Production** (`FLASK_DEBUG` off) | Set `DEFAULT_ADMIN_PASSWORD` and `DEFAULT_USER_PASSWORD` in `backend/.env` to strong unique values (min 12 characters). Well-known lab passwords are refused. |
| **Local debug only** (`FLASK_DEBUG=true`) | If those env passwords are unset, the backend may seed lab accounts for an empty database. Those values live in backend bootstrap code only and must never be used on a shared or production host. |

Change bootstrap passwords immediately. Generate strong `JWT_SECRET` and `SECRETS_ENCRYPTION_KEY` before production use (see `.env.example`).

---

## Troubleshooting

| Issue | Likely cause | Fix |
|-------|----------------|-----|
| Pings always fail | ICMP needs elevation on Windows | Run the terminal / IDE as Administrator |
| Nmap errors or empty OS info | Missing binary or no admin rights | Install Nmap, set `NMAP_PATH`, run elevated, or use `-sV -T4` |
| Interface discovery skipped | Device not `Online` or missing SSH creds | Fix reachability; set per-device SSH credentials |
| Storm never prepares | Not CONFIRMED, risk low, or SAFE stale vs confirmation | Check Storm Protection panels; wait for fresh confirmation + safety |
| Mitigation loops after recovery | Should not happen with post-recovery reset | Confirm `recoveredAt` is set and latest confirmation is `NOT_CONFIRMED` |
| UI not updating from Flask alone | Stale or missing build | Run `npm run build` in `frontend/` |
| MongoDB connection errors | Bad URI / DB down | Check `MONGO_URI` and that MongoDB is reachable |
| Duplicate scheduler jobs | Debug reloader | Scheduler starts only in the child process when `FLASK_DEBUG=true` |
| Decrypt / SSH secret errors | Key rotated | Keep `SECRETS_ENCRYPTION_KEY` stable or re-enter secrets |
| No email on outage | SMTP off or misconfigured | Enable in Settings / `.env`; check `EMAIL_PROVIDER`; use an app password for Gmail or enable SMTP AUTH for Outlook; device must be **critical** with **3+** consecutive failures. Use **Send test email**. |
| No WhatsApp messages | Disabled, bad token, or unapproved template | Set `WHATSAPP_ALERTS_ENABLED=true`, check the template names are approved in Meta Business Manager, run `POST /api/settings/test-whatsapp` |
| Alert missing but device offline | Failure hysteresis / alert threshold | Status flips after `pingFailureConfirmationScans` (default 2); alert fires at `consecutiveFailures >= 3` |
| Full Topology empty | Level 2 live filter | Peers must be Online inventory devices; use Level 1 per-switch view for CDP-only neighbors |
| Topology has no links | No CDP/LLDP on the switches | Enable CDP/LLDP (Cisco) or LLDP (Juniper) and re-run interface discovery |
| Hardware Health page empty | Switch hardware monitoring off, or switch not eligible | Enable `switchHardwareMonitoringEnabled` in Settings; device must be a monitored Cisco/Juniper switch with SSH or SNMP credentials |
| Server Hardware shows no data | iLO unreachable, wrong credentials, or polling off | Check `iloAddress` / credentials on the device, `serverHardwareMonitoringEnabled`, and `lastError` in the collection state |

Logs: `backend/logs/monitor.log` (also printed to the console).

---

## Further documentation

| Document | Contents |
|----------|----------|
| [`docs/INSTALLATION_GUIDE.md`](docs/INSTALLATION_GUIDE.md) | Step-by-step install and deployment from a fresh machine |
| [`USER_MANUAL.md`](USER_MANUAL.md) | Operator guide to the UI |
| [`docs/SWITCH_HARDWARE_MONITORING.md`](docs/SWITCH_HARDWARE_MONITORING.md) | Switch hardware collection, health rules and alerts |
| [`backend/DEPLOYMENT.md`](backend/DEPLOYMENT.md) | Production deployment notes |
| [`backend/docs/WHATSAPP_ALERTS.md`](backend/docs/WHATSAPP_ALERTS.md) | WhatsApp Cloud API setup |
| [`backend/services/interface_collection/README.md`](backend/services/interface_collection/README.md) | Interface discovery and stats internals |
| [`backend/README.md`](backend/README.md) · [`frontend/README.md`](frontend/README.md) | Backend endpoints and frontend notes |
