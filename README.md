# NetPulse (Network Monitor)

Full-stack LAN monitoring and switch storm-protection platform. NetPulse continuously pings devices, profiles them with Nmap, discovers hosts on your subnet, inventories switch interfaces over SSH, scores storm risk from live counters, and can automatically shut down / recover ports when a broadcast storm is confirmed safe to mitigate.

---

## Table of contents

- [What this project does](#what-this-project-does)
- [How it works](#how-it-works)
- [Storm protection pipeline](#storm-protection-pipeline)
- [System architecture](#system-architecture)
- [Tech stack](#tech-stack)
- [Project structure](#project-structure)
- [MongoDB collections](#mongodb-collections)
- [Configuration](#configuration)
- [API overview](#api-overview)
- [Development setup](#development-setup)
- [Default login credentials](#default-login-credentials)
- [Troubleshooting](#troubleshooting)

---

## What this project does

NetPulse answers five operational questions continuously:

1. **Is the device reachable?** — ICMP ping on a schedule (or on demand).
2. **What is running on it?** — Periodic Nmap scans for OS, open ports, services, MAC, and vendor.
3. **What interfaces exist on the switch?** — SSH discovery of port inventory, VLANs, neighbors, and monitoring intent.
4. **Is a storm forming?** — Stats → eligibility → risk → confirmation → safety → prepare → mitigation.
5. **Did something important go down?** — In-app alerts plus optional email when a critical device goes offline.

Operators use the React UI to:

| Page | Purpose |
|------|---------|
| **Dashboard** | Live KPIs, status charts, response-time trends, recent activity |
| **Devices** | CRUD inventory, CSV import, manual ping / Nmap, per-device ping + SSH overrides |
| **Interfaces** | Switch inventory, discovery, stats, monitoring mode, manual shutdown / recover |
| **Storm Protection** | Eligibility, risk, confirmation, safety, incidents, mitigation & recovery history |
| **Discovery** | Suggest local `/24` range and sweep IPs; auto-register new online hosts |
| **History** | Filterable ping history and per-device uptime |
| **Alerts** | Acknowledge or dismiss critical outage alerts |
| **Reports** | Uptime reports; export devices/history as CSV or Excel |
| **Settings** | Ping interval, SMTP, mitigation mode, auto-recovery, retention |
| **Account** | Change username/password; admins manage users |

Roles:

| Role | Access |
|------|--------|
| **super-admin** | Full admin rights plus exclusive management of other super-admins |
| **admin** | Full write access (devices, discovery, settings, users, storm mitigation/recovery) |
| **operator** | Viewer access plus on-demand Nmap, alert ack/dismiss, selected storm actions |
| **viewer** | Read-only dashboard, devices, interfaces, history, reports, alerts |

---

## How it works

### End-to-end flow

```
┌──────────────┐     JWT REST      ┌─────────────────┐     PyMongo     ┌──────────┐
│ React UI     │ ◄───────────────► │ Flask (app.py)  │ ◄─────────────► │ MongoDB  │
│ (Vite/TS)    │   poll 10–20s     │ + APScheduler   │                 │          │
└──────────────┘                   └────────┬────────┘                 └──────────┘
                                            │
          ┌─────────────┬───────────────────┼───────────────────┬──────────────┐
          ▼             ▼                   ▼                   ▼              ▼
     ICMP ping     Nmap profiling    SSH iface discovery   SNMP/SSH stats   SMTP email
     (~60s)        (~1 hour)         (~1 hour)             (~60s → storm)   critical
```

1. On startup, Flask loads settings, ensures indexes, seeds default users if needed, and starts APScheduler.
2. The **ping job** monitors devices with `monitor: true`, writes `pingHistory`, and may create alerts.
3. The **Nmap job** scans currently **Online** devices and stores OS/ports under `networkInfo`.
4. The **interface discovery job** SSHs into eligible switches and upserts the `interfaces` inventory.
5. The **stats + storm chain** polls counters, then runs eligibility → risk → confirmation → safety → prepare → optional auto-mitigation.
6. The **recovery job** (30s) evaluates MITIGATED / MONITORING incidents for auto-recovery and re-mitigation.
7. The **retention job** (daily) refreshes TTL indexes and purges closed incidents per settings.
8. The frontend authenticates with JWT and polls APIs so the UI stays live without WebSockets.

### 1. Automatic ping monitoring

**Where:** `backend/scheduler.py` → `services/monitor_service.py` → `services/ping_service.py`

1. With `MONITOR_RUNTIME_MODE=dispatch` (default), APScheduler runs the due-device **dispatcher** every `MONITOR_DISPATCHER_INTERVAL_SECONDS` (1–5s, default 5). Per-device cadence is Settings `pingInterval` (default **60s** from `SCAN_INTERVAL`), advanced via `nextCheckAt` at claim time—not a 60s “scan all devices” tick.
2. The dispatcher atomically claims due monitored devices up to free `pingConcurrency` worker slots (default **40**), then bounded workers run ICMP via `ping3`.
3. Results update the device:
   - **Success** → `Online`, reset `consecutiveFailures`, set `lastSeen` and `responseTime`
   - **Failure + critical** → `Offline (Critical)`
   - **Failure + non-critical** → `Not Reachable`
4. Every check is stored in `pingHistory` with `scanType` of `Automatic` or `Manual`.
5. Changing `pingInterval` in Settings updates the per-device cadence (legacy mode also retargets the APScheduler period; dispatch mode keeps the fast dispatcher tick).

### 2. Nmap deep scanning

**Where:** `backend/scheduler.py` → `services/nmap_service.py` (also `routes/nmap_routes.py`)

1. Runs every `NMAP_SCAN_INTERVAL` seconds (default 3600).
2. Only **Online** devices are scanned.
3. A thread pool (`MAX_SCAN_THREADS`) runs Nmap with `NMAP_ARGUMENTS` (default `-A -T4`).
4. Results land on the device’s `networkInfo` and appear in the device drawer.
5. Operator+ roles can trigger single-device or “scan all online” Nmap from the UI/API.

Requires the **Nmap binary** on `PATH` (or `NMAP_PATH`). Aggressive flags often need Administrator privileges on Windows.

### 3. Subnet discovery

**Where:** `services/discovery_service.py` via `POST /api/discovery/scan-range`

1. `GET /api/discovery/network-hint` suggests a local `/24` range.
2. A thread pool pings hosts in the range.
3. Online hosts get best-effort reverse DNS.
4. Unknown hosts can be auto-saved (`deviceType: Unknown`, `monitor: true`) so they enter the ping loop immediately.

### 4. Switch interface discovery & stats

**Where:** `services/interface_collection/` (see also [`backend/services/interface_collection/README.md`](backend/services/interface_collection/README.md))

1. **Discovery** (SSH) parses interface status, switchport, CDP/LLDP, and classifies access / trunk / uplink / protected ports.
2. Documents are upserted into `interfaces` with monitoring intent (`AUTO`, `DISABLED_BY_USER`, …).
3. **Stats** prefer SNMP counters and fall back to SSH; samples are written to `interface_stats`.
4. From the Interfaces UI (or API), admins can:
   - Change monitoring mode per port
   - Trigger **manual shutdown** (creates a MANUAL incident + mitigation)
   - Trigger **manual recover** for a mitigated incident

### 5. Alerting and email

**Where:** `services/alert_service.py` + `services/email_service.py`

1. Transition of a **critical** device into `Offline (Critical)` creates an `alerts` document once per outage.
2. If SMTP is enabled, a background thread sends email using Settings / `.env`.
3. Operators (and admins) acknowledge or dismiss alerts; viewers can view only.

### 6. Authentication and roles

**Where:** `utils/auth.py`, `services/user_service.py`, `routes/auth_routes.py`

- Login returns a JWT (`JWT_SECRET`, `JWT_EXPIRE_HOURS`).
- Passwords are stored with bcrypt.
- SSH / SMTP secrets at rest are encrypted with Fernet (`SECRETS_ENCRYPTION_KEY`).
- Roles inherit privileges: `super-admin` ⊃ `admin` ⊃ `operator` ⊃ `viewer`.
- First boot with an empty `users` collection seeds default admin and viewer accounts.

### 7. Frontend data loading

**Where:** `frontend/src/hooks/queries.ts`, Vite proxy in `vite.config.ts`

- TanStack Query polls the API (dashboard ~10s, devices ~15s, history ~20s, storm panels as configured).
- In development, Vite proxies `/api` and `/health` to `http://127.0.0.1:5000`.
- In production, `npm run build` produces `frontend/dist`; Flask serves that SPA when present.

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
| `mitigationMode` | `automatic` \| `manual` | `manual` |
| `autoRecovery` | Scheduler may recover MITIGATED ports | `true` |
| `cooldownMinutes` | Wait after mitigation before recovery | `5` |
| `stabilizationSeconds` | MONITORING window after recovery | `60` |
| `maximumRecoveryAttempts` | Cap before `RECOVERY_FAILED` | `3` |
| `reMitigationThreshold` | Risk score that can re-trigger after recovery | `75` |
| `dataRetentionDays` | TTL for ping/stats/evaluation history | `90` |
| `incidentRetentionDays` | Retention for closed incidents + attempt logs | `365` |
| `stormNotifications` | Enable storm emails (shutdown / recovery / failure) + recipient | enabled |

Deep dive on interface collection: [`backend/services/interface_collection/README.md`](backend/services/interface_collection/README.md).

---

## System architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    React frontend (NetPulse)                │
│         Vite + TypeScript + Tailwind + TanStack Query       │
│  Dashboard · Devices · Interfaces · Storm · Discovery · …   │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP + Bearer JWT
┌──────────────────────────────▼──────────────────────────────┐
│                   Flask backend (app.py)                    │
│  Blueprints: auth, devices, scan, nmap, history, dashboard, │
│  discovery, interfaces, storm, alerts, settings, reports    │
│                                                             │
│  APScheduler                                                │
│  • device_monitor_job      → ping monitoring                │
│  • nmap_scan_job           → Online device profiling        │
│  • interface_discovery_job → SSH inventory                  │
│  • interface_stats_job     → stats → storm pipeline chain   │
│  • storm_recovery_job      → auto-recovery / remmitigation  │
│  • data_retention_job      → TTL + closed-incident purge    │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│                         MongoDB                             │
│  devices · pingHistory · alerts · settings · users ·        │
│  auditLogs · interfaces · interface_stats ·                 │
│  eligibility_results · storm_risk_history ·                 │
│  storm_confirmation_history · storm_safety_history ·        │
│  storm_incidents · storm_mitigation_history ·               │
│  storm_recovery_history · storm_*_locks                     │
└─────────────────────────────────────────────────────────────┘
```

---

## Tech stack

| Layer | Technologies |
|-------|----------------|
| Frontend | React 19, TypeScript, Vite, Tailwind CSS 4, TanStack Query/Table, Recharts, Radix UI, Framer Motion |
| Backend | Flask 3, Flask-CORS, APScheduler, PyMongo |
| Monitoring | `ping3` (ICMP), `python-nmap` (Nmap), Paramiko (SSH), SNMP for interface stats |
| Auth / secrets | PyJWT, bcrypt, cryptography (Fernet) |
| Export | openpyxl (Excel), CSV |
| Database | MongoDB |

---

## Project structure

```
NetPulse/
├── README.md
├── backend/
│   ├── app.py                      # Flask app, indexes, bootstrap, SPA hosting
│   ├── scheduler.py                # Ping, Nmap, interfaces, storm, retention jobs
│   ├── requirements.txt
│   ├── .env / .env.example
│   ├── config/                     # MongoDB + env (Nmap, SSH, SNMP, storm)
│   ├── models/                     # Device, interface, ping history helpers
│   ├── routes/                     # REST blueprints (incl. interfaces + storm)
│   ├── services/
│   │   ├── interface_collection/   # SSH discovery, stats, monitoring state
│   │   ├── storm/                  # Eligibility → risk → confirm → safety → …
│   │   │   ├── diagnostics/        # Read-only evidence capture
│   │   │   ├── mitigation/         # Shutdown engine, verifier, audit
│   │   │   └── recovery/           # Policy, safety, engine, post-recovery
│   │   └── …                       # Ping, monitor, Nmap, discovery, alerts, …
│   ├── tests/                      # Unit tests (recovery, safety, orchestrator, …)
│   ├── utils/                      # JWT, serializers, pagination, logging
│   └── logs/monitor.log
└── frontend/
    ├── src/
    │   ├── api/                    # HTTP client + endpoint helpers
    │   ├── auth/                   # Auth context
    │   ├── components/             # Layout, devices, interfaces, shared UI
    │   ├── hooks/                  # React Query hooks (polling)
    │   ├── pages/                  # Dashboard, Devices, Interfaces, Storm, …
    │   └── types/
    ├── package.json
    └── vite.config.ts              # Dev server + /api proxy
```

More detail: [`backend/README.md`](backend/README.md), [`frontend/README.md`](frontend/README.md).

---

## MongoDB collections

| Collection | Purpose |
|------------|---------|
| `devices` | Inventory, status, ping overrides, SSH/SNMP creds, Nmap `networkInfo` |
| `pingHistory` | Time-series of every manual/automatic ping |
| `alerts` | Critical offline events (acknowledge / dismiss) |
| `settings` | Global ping, SMTP, storm mitigation/recovery, retention |
| `users` | Accounts with bcrypt password hashes |
| `auditLogs` | Admin / storm action trail |
| `interfaces` | Discovered switch ports + monitoring intent |
| `interface_stats` | Counter / rate samples for risk scoring |
| `eligibility_results` | Latest eligibility decisions |
| `storm_risk_history` | Append-only risk scores |
| `storm_confirmation_history` | Append-only confirmation / reset rows |
| `storm_safety_history` | Append-only safety evaluations |
| `storm_incidents` | Storm + manual incidents, timeline, `recoveredAt` |
| `storm_mitigation_history` | Mitigation attempt audit |
| `storm_recovery_history` | Recovery attempt audit (incl. blocked policy) |
| `storm_mitigation_locks` / `storm_recovery_locks` | Lease locks (TTL) |

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
DEFAULT_ADMIN_PASSWORD=admin123

# Ping defaults (also adjustable in Settings UI)
SCAN_INTERVAL=60
PING_TIMEOUT_MS=1000
PING_RETRIES=3

# Email alerts (optional)
ALERT_EMAIL_ENABLED=true
ALERT_EMAIL_TO=recipient@example.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=sender@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=sender@gmail.com
SMTP_USE_TLS=true

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

# Retention
DATA_RETENTION_DAYS=90
INCIDENT_RETENTION_DAYS=365

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
| `INTERFACE_SCAN_INTERVAL` | SSH rediscovery interval (`0` disables schedule) |
| `INTERFACE_STATS_INTERVAL` | Stats + storm chain interval (`0` disables) |
| `STORM_MITIGATION_MODE` | Bootstrap default for Settings `mitigationMode` |
| `DATA_RETENTION_DAYS` / `INCIDENT_RETENTION_DAYS` | History / closed-incident retention |

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
| CRUD | `/api/devices` … | Device inventory + CSV import |
| POST | `/api/devices/<id>/scan` | Manual ICMP ping |
| POST | `/api/devices/<id>/scan-details` | Manual Nmap scan |
| GET | `/api/history` | Ping history |
| GET | `/api/discovery/network-hint` | Suggest LAN range |
| POST | `/api/discovery/scan-range` | Subnet sweep |
| GET | `/api/dashboard/*` | Summary, stats, charts |
| GET/POST | `/api/alerts` … | List / acknowledge / dismiss |
| GET/PUT | `/api/settings` | Global settings (incl. storm) |
| GET | `/api/reports/*` | Uptime + CSV/XLSX export |
| GET | `/health` | Server + MongoDB ping |

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

### 3. Single-process UI (optional)

```powershell
cd frontend
npm run build
cd ..\backend
python app.py
```

Flask serves `frontend/dist` at `http://127.0.0.1:5000`.

### 4. Tests

```powershell
cd backend
.\venv\Scripts\activate
python -m unittest discover -s tests -p "test_*.py" -q
```

Coverage includes confirmation, safety, diagnostics/orchestrator, recovery engine, recovery safety, and post-recovery invalidation.

---

## Default login credentials

Created on first run when the `users` collection is empty:

| Username | Password | Role |
|----------|----------|------|
| `admin` (or `DEFAULT_ADMIN_USER`) | `admin123` (or `DEFAULT_ADMIN_PASSWORD`) | admin |
| `viewer` | `viewer123` | viewer |

Change these immediately for any shared or production environment. Generate strong `JWT_SECRET` and `SECRETS_ENCRYPTION_KEY` before production use (see `.env.example`).

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
| No email on outage | SMTP off or misconfigured | Enable in Settings / `.env`; use an app password for Gmail |

Logs: `backend/logs/monitor.log` (also printed to the console).
