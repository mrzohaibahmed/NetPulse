# Network Monitor (NetPulse)

A full-stack LAN monitoring system that continuously pings devices, profiles them with Nmap, discovers hosts on your subnet, fires email alerts on critical outages, and shows live status in a React dashboard.

---

## Table of contents

- [What this project does](#what-this-project-does)
- [How it works](#how-it-works)
- [Storm protection & interfaces](#storm-protection--interfaces)
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

NetPulse watches devices on your local network and answers three questions continuously:

1. **Is the device reachable?** — ICMP ping on a schedule (or on demand).
2. **What is running on it?** — Periodic Nmap scans for OS, open ports, services, MAC, and vendor.
3. **Did something important go down?** — In-app alerts plus optional email when a critical device goes offline.

Operators use the React UI to:

| Page | Purpose |
|------|---------|
| **Dashboard** | Live KPIs, status charts, response-time trends, recent activity |
| **Devices** | CRUD inventory, CSV import, manual ping / Nmap, per-device ping overrides |
| **Interfaces** | Switch interface inventory, discovery, and stats |
| **Storm Protection** | Eligibility, risk, confirmation, safety, incidents, mitigation/recovery |
| **Discovery** | Suggest local `/24` range and sweep IPs; auto-register new online hosts |
| **History** | Filterable ping history and per-device uptime |
| **Alerts** | Acknowledge or dismiss critical outage alerts |
| **Reports** | Uptime reports; export devices/history as CSV or Excel |
| **Settings** | Global ping interval/timeout/retries, SMTP, storm mitigation mode |
| **Account** | Change username/password; admins manage users |

Roles:

- **super-admin** — full admin rights plus exclusive user/role management for other super-admins
- **admin** — full write access (devices, discovery, settings, users, storm mitigation controls)
- **operator** — viewer access plus on-demand Nmap scans and alert acknowledge/dismiss
- **viewer** — read-only dashboard, devices, history, reports, alerts

---

## How it works

### End-to-end flow

```
┌──────────────┐     JWT REST      ┌─────────────────┐     PyMongo     ┌──────────┐
│ React UI     │ ◄───────────────► │ Flask (app.py)  │ ◄─────────────► │ MongoDB  │
│ (Vite/TS)    │   poll 10–20s     │ + APScheduler   │                 │          │
└──────────────┘                   └────────┬────────┘                 └──────────┘
                                            │
                     ┌──────────────────────┼──────────────────────┐
                     ▼                      ▼                      ▼
              ICMP ping (ping3)      Nmap (python-nmap)      SMTP email
              every ~30s             every ~1 hour          on critical
              (Settings)             (.env interval)        offline
```

1. On startup, Flask loads settings, seeds default users if needed, and starts APScheduler.
2. The **ping job** loads devices with `monitor: true`, respects per-device intervals, pings each host, updates status, writes `pingHistory`, and may create an alert.
3. The **Nmap job** scans currently **Online** devices only, stores OS/ports/services under `networkInfo` on the device document.
4. The frontend authenticates with JWT and polls dashboard/device APIs so the UI stays live without WebSockets.

### 1. Automatic ping monitoring

**Where:** `backend/scheduler.py` → `services/monitor_service.py` → `services/ping_service.py`

1. APScheduler runs `monitor_all_devices` on the global interval from Settings (`pingInterval`, default from `SCAN_INTERVAL`, usually 30s).
2. For each device with `monitor: true`, if enough time has passed since `lastCheckedAt` (honoring optional `pingInterval` / timeout / retries overrides), the service sends an ICMP echo via `ping3`.
3. Results update the device:
   - **Success** → `Online`, reset `consecutiveFailures`, set `lastSeen` and `responseTime`
   - **Failure + critical** → `Offline (Critical)`
   - **Failure + non-critical** → `Not Reachable`
4. Every check (automatic or manual) is stored in `pingHistory` with `scanType` of `Automatic` or `Manual`.
5. Changing `pingInterval` in Settings calls `reschedule_monitor_job` so the loop updates without restarting Flask.

### 2. Nmap deep scanning

**Where:** `backend/scheduler.py` → `services/nmap_service.py` (also triggered from `routes/nmap_routes.py`)

1. A separate scheduler job runs every `NMAP_SCAN_INTERVAL` seconds (default 3600).
2. Only devices currently marked `Online` are scanned (avoids hanging on dead hosts).
3. A thread pool (`MAX_SCAN_THREADS`, default 5) runs Nmap with `NMAP_ARGUMENTS` (default `-A -T4`).
4. Parsed results are written to the device’s `networkInfo` (OS, ports, services, MAC, vendor) and shown in the device details drawer.
5. Operator+ roles can also trigger a single-device or “scan all online” Nmap run from the API/UI (viewers cannot).

Requires the **Nmap binary** on `PATH` (or set `NMAP_PATH`). Aggressive flags often need Administrator privileges on Windows.

### 3. Subnet discovery

**Where:** `services/discovery_service.py` via `POST /api/discovery/scan-range`

1. `GET /api/discovery/network-hint` probes the local IP (UDP connect to `8.8.8.8`) and suggests a `/24` start/end range.
2. The range scan uses a thread pool to ping hosts (capped to protect resources).
3. Online hosts get a best-effort reverse DNS name.
4. Hosts not already in MongoDB can be auto-saved as devices (`deviceType: Unknown`, `monitor: true`, status `Online`) so they enter the ping loop immediately.

### 4. Alerting and email

**Where:** `services/alert_service.py` + `services/email_service.py`

1. After each ping update, the monitor compares previous vs new status.
2. Transition of a **critical** device into `Offline (Critical)` creates an `alerts` document (once per outage transition, not on every failed ping).
3. If SMTP is enabled, a background thread sends email using Settings / `.env` values.
4. Operators (and admins) acknowledge or dismiss alerts in the UI; viewers can view alerts only.

### 5. Authentication and roles

**Where:** `utils/auth.py`, `services/user_service.py`, `routes/auth_routes.py`

- Login returns a JWT (`JWT_SECRET`, `JWT_EXPIRE_HOURS`).
- Passwords are stored with bcrypt.
- Roles inherit privileges: `super-admin` ⊃ `admin` ⊃ `operator` ⊃ `viewer`.
- Route handlers enforce the minimum required role (e.g. Nmap scan and alert ack/dismiss require `operator` or higher).
- First boot with an empty `users` collection seeds default admin and viewer accounts.

### 6. Frontend data loading

**Where:** `frontend/src/hooks/queries.ts`, Vite proxy in `vite.config.ts`

- TanStack Query polls the API (dashboard ~10s, devices ~15s, history ~20s).
- In development, Vite proxies `/api` and `/health` to `http://127.0.0.1:5000`.
- In production, `npm run build` produces `frontend/dist`; Flask serves that SPA at `/` when the folder exists.

---

## Storm protection & interfaces

NetPulse also discovers switch interfaces, collects stats, and runs a storm-protection
pipeline: eligibility → risk → confirmation → safety → diagnostics/prepare → mitigation.

**Mitigation is fully automatic by design.** There is no separate emergency or manual
port-shutdown path. Behavior is controlled by `mitigationMode` in settings:

- **`automatic`** — after prepare, the scheduler shuts down `READY_FOR_MITIGATION`
  interfaces via the Mitigation Engine.
- **`manual`** (default) — the pipeline stops after prepare; an admin triggers
  shutdown/recovery from the Storm Protection UI.

Recovery (re-enable) follows the same automatic/admin model via recovery settings
(`autoRecovery`, cooldown, stabilization). Historical incident logs may still show
older incident types from before this design; new incidents are storm-pipeline only.

---

## System architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    React frontend (NetPulse)                │
│         Vite + TypeScript + Tailwind + TanStack Query       │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP + Bearer JWT
┌──────────────────────────────▼──────────────────────────────┐
│                   Flask backend (app.py)                    │
│  Blueprints: auth, devices, scan, nmap, history, dashboard, │
│              discovery, alerts, settings, reports           │
│                                                             │
│  APScheduler                                                │
│  • device_monitor_job  → monitor_all_devices (ping)         │
│  • nmap_scan_job       → scan_all_online_devices            │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│                         MongoDB                             │
│  devices · pingHistory · alerts · settings · users ·        │
│  auditLogs                                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## Tech stack

| Layer | Technologies |
|-------|----------------|
| Frontend | React 19, TypeScript, Vite 8, Tailwind CSS 4, TanStack Query/Table, Recharts, Radix UI, Framer Motion |
| Backend | Flask 3, Flask-CORS, APScheduler, PyMongo |
| Monitoring | `ping3` (ICMP), `python-nmap` (Nmap) |
| Auth | PyJWT, bcrypt |
| Export | openpyxl (Excel), CSV |
| Database | MongoDB |

---

## Project structure

```
Network Monitor/
├── README.md
├── backend/
│   ├── app.py                 # Flask app, blueprints, SPA static serving
│   ├── scheduler.py           # Ping + Nmap background jobs
│   ├── requirements.txt
│   ├── .env / .env.example
│   ├── config/                # MongoDB + env (incl. Nmap settings)
│   ├── models/                # Device / ping history document helpers
│   ├── routes/                # REST blueprints
│   ├── services/              # Ping, monitor, Nmap, discovery, alerts, email, …
│   ├── utils/                 # JWT, serializers, pagination, logging
│   └── logs/monitor.log
└── frontend/
    ├── src/
    │   ├── api/               # HTTP client + endpoint helpers
    │   ├── auth/              # Auth context
    │   ├── components/        # Layout, devices, shared UI, Radix primitives
    │   ├── hooks/             # React Query hooks (polling)
    │   ├── pages/             # Dashboard, Devices, Discovery, …
    │   └── types/
    ├── package.json
    └── vite.config.ts         # Dev server + /api proxy
```

More detail: [`backend/README.md`](backend/README.md), [`frontend/README.md`](frontend/README.md).

---

## MongoDB collections

| Collection | Purpose |
|------------|---------|
| `devices` | Inventory, status, ping overrides, Nmap `networkInfo` |
| `pingHistory` | Time-series of every manual/automatic ping |
| `alerts` | Critical offline events (acknowledge / dismiss) |
| `settings` | Global ping + SMTP config (editable in UI) |
| `users` | Accounts with bcrypt password hashes |
| `auditLogs` | Admin action trail (creates, imports, settings changes, …) |

**Device status values:** `Online`, `Not Reachable`, `Offline (Critical)`, `Unknown`.

---

## Configuration

Copy and edit `backend/.env` (see also `backend/.env.example`):

```env
# Database
MONGO_URI=mongodb://localhost:27017
DATABASE_NAME=NetworkMonitor

# Flask
FLASK_DEBUG=true

# Ping defaults (also adjustable in Settings UI)
SCAN_INTERVAL=30
PING_TIMEOUT_MS=1000
PING_RETRIES=3

# Auth
JWT_SECRET=change-me-in-production
JWT_EXPIRE_HOURS=8
DEFAULT_ADMIN_USER=admin
DEFAULT_ADMIN_PASSWORD=admin123

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
```

| Variable | Meaning |
|----------|---------|
| `MONGO_URI` / `DATABASE_NAME` | MongoDB connection (required) |
| `SCAN_INTERVAL` | Default ping interval in seconds |
| `JWT_*` | Token signing and lifetime |
| `DEFAULT_ADMIN_*` | First admin credentials when DB has no users |
| `ALERT_*` / `SMTP_*` | Critical offline email |
| `NMAP_*` | Background profiling interval, flags, concurrency, binary path |

Use `NMAP_ARGUMENTS=-sV -T4` if you cannot run elevated (skips aggressive OS detection).

---

## API overview

All JSON APIs are under `/api` except `/health`. Most routes require `Authorization: Bearer <token>`.

| Method | Route | Description | Role |
|--------|-------|-------------|------|
| POST | `/api/auth/login` | Login, returns JWT | Public |
| GET | `/api/auth/me` | Current user | Any |
| PUT | `/api/auth/account` | Update own account | Any |
| GET/PUT | `/api/users` / `/api/users/<id>` | List / update users | Admin |
| POST/GET/PUT/DELETE | `/api/devices` … | Device CRUD + CSV import | Admin write |
| POST | `/api/devices/<id>/scan` | Manual ICMP ping | Any |
| POST | `/api/devices/<id>/scan-details` | Manual Nmap scan | Any |
| POST | `/api/devices/scan-all-details` | Nmap all online devices | Any |
| GET | `/api/history`, `/api/devices/<id>/history` | Ping history / uptime | Any |
| GET | `/api/discovery/network-hint` | Suggest LAN range | Any |
| POST | `/api/discovery/scan-range` | Subnet sweep | Admin |
| GET | `/api/dashboard/*` | Summary, stats, charts | Any |
| GET/POST | `/api/alerts` … | List / acknowledge / dismiss | Any |
| GET/PUT | `/api/settings` | Read / update global settings | Admin write |
| GET | `/api/reports/uptime` | Uptime report | Any |
| GET | `/api/reports/export/devices` | Export devices (csv/xlsx) | Any |
| GET | `/api/reports/export/history` | Export history (csv/xlsx) | Any |
| GET | `/health` | Server + MongoDB ping | Public |

Full endpoint tables: [`backend/README.md`](backend/README.md).

---

## Development setup

### Prerequisites

- Python 3.10+
- Node.js 18+ (for the frontend)
- MongoDB (local or Atlas)
- [Nmap](https://nmap.org/download.html) installed for deep scans
- On Windows, run the terminal **as Administrator** for reliable ICMP and aggressive Nmap

### 1. Backend

```powershell
cd backend
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
# Create backend/.env from the Configuration section above
python app.py
```

API: `http://127.0.0.1:5000`

### 2. Frontend (dev)

```powershell
cd frontend
npm install
npm run dev
```

UI: `http://127.0.0.1:5173` (proxies `/api` to the Flask app)

### 3. Single-process UI (optional)

```powershell
cd frontend
npm run build
cd ..\backend
python app.py
```

Flask serves the built SPA from `frontend/dist` at `http://127.0.0.1:5000`.

---

## Default login credentials

Created on first run when the `users` collection is empty:

| Username | Password | Role |
|----------|----------|------|
| `admin` (or `DEFAULT_ADMIN_USER`) | `admin123` (or `DEFAULT_ADMIN_PASSWORD`) | admin |
| `viewer` | `viewer123` | viewer |

Change these immediately for any shared or production environment.

---

## Troubleshooting

| Issue | Likely cause | Fix |
|-------|----------------|-----|
| Pings always fail | ICMP needs elevation on Windows | Run the terminal / IDE as Administrator |
| Nmap errors or empty OS info | Missing binary or no admin rights | Install Nmap, set `NMAP_PATH`, run elevated, or use `NMAP_ARGUMENTS=-sV -T4` |
| UI not updating from Flask alone | Stale or missing build | Run `npm run build` in `frontend/` |
| MongoDB connection errors | Bad URI / DB down | Check `MONGO_URI` and that MongoDB is reachable |
| Duplicate scheduler jobs | Debug reloader | App only starts the scheduler in the child process when `FLASK_DEBUG=true` |
| No email on outage | SMTP off or misconfigured | Enable in Settings / `.env`; use an app password for Gmail |

Logs: `backend/logs/monitor.log` (also printed to the console).
