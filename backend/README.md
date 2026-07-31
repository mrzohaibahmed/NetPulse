# NetPulse — Backend API

Flask REST API for LAN monitoring, switch interface collection, and storm protection. Stores inventory in MongoDB, runs APScheduler jobs for ping / Nmap / SSH discovery / stats / recovery, and can serve the built React SPA from `frontend/dist`.

## Features

- **Authentication** — JWT login, roles (`super-admin` / `admin` / `operator` / `viewer`), account management
- **Device CRUD** — create, list, update, delete, CSV import, cascade delete
- **ICMP monitoring** — scheduled + manual ping with per-device overrides
- **Nmap profiling** — scheduled + on-demand deep scans for Online devices
- **Subnet discovery** — IP range sweep with optional auto-register
- **Interface discovery** — SSH inventory (status, VLANs, neighbors, monitoring intent)
- **Interface stats** — SNMP preferred, SSH fallback; feeds the storm pipeline
- **Storm protection** — eligibility → risk → confirmation → safety → prepare → mitigation → recovery
- **Manual shutdown / recover** — operator actions on individual interfaces
- **Alerts + email** — critical offline transitions
- **Settings** — ping, SMTP, mitigation mode, auto-recovery, retention
- **Audit logging** — administrative and storm execution trail
- **Data retention** — TTL indexes + daily closed-incident purge
- **Frontend hosting** — serves `frontend/dist` when present

## Tech stack

| Layer | Technology |
|-------|------------|
| API | Flask, Flask-CORS |
| Database | MongoDB (PyMongo) |
| Auth / secrets | PyJWT, bcrypt, cryptography (Fernet) |
| Scheduling | APScheduler |
| Ping / scan | ping3, python-nmap |
| Switch access | Paramiko (SSH), SNMP |
| Export | openpyxl |
| Config | python-dotenv |

## Project structure

```
backend/
├── app.py                  # Entry point, indexes, bootstrap, SPA static
├── scheduler.py            # Ping, Nmap, discovery, stats→storm, recovery, retention
├── requirements.txt
├── .env / .env.example
├── config/                 # MongoDB + env
├── models/                 # Device, interface, ping history helpers
├── routes/                 # REST blueprints (auth … interfaces … storm)
├── services/
│   ├── interface_collection/
│   ├── storm/              # eligibility, risk, confirmation, safety, …
│   │   ├── diagnostics/
│   │   ├── mitigation/
│   │   └── recovery/       # policy, safety, engine, post_recovery
│   └── …                   # ping, monitor, nmap, discovery, alerts, …
├── tests/
├── utils/
└── logs/monitor.log
```

## Setup

```powershell
cd backend
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
# Copy .env.example → .env and set MONGO_URI, JWT_SECRET, SECRETS_ENCRYPTION_KEY
python app.py
```

- API: `http://127.0.0.1:5000`
- With built frontend: UI also at `http://127.0.0.1:5000`

See the root [`README.md`](../README.md) for the full env table, storm pipeline, and collections.

## Authentication

All `/api/*` routes except login require a Bearer token.

```http
POST /api/auth/login
Content-Type: application/json

{ "username": "admin", "password": "admin123" }
```

```http
Authorization: Bearer <token>
```

| Role | Access |
|------|--------|
| `super-admin` | Full access + manage other super-admins |
| `admin` | Full write (devices, discovery, settings, storm mitigation/recovery) |
| `operator` | Read + Nmap, alert ack/dismiss, selected storm actions |
| `viewer` | Read-only |

Default users are created on first run if `users` is empty.

## Scheduler jobs

| Job | Default interval | Purpose |
|-----|------------------|---------|
| `device_monitor_job` | Settings `pingInterval` (~30s) | ICMP monitoring |
| `nmap_scan_job` | `NMAP_SCAN_INTERVAL` (3600s) | Online device profiling |
| `interface_discovery_job` | `INTERFACE_SCAN_INTERVAL` (3600s) | SSH inventory |
| `interface_stats_job` | `INTERFACE_STATS_INTERVAL` (60s) | Stats → eligibility → risk → confirmation → safety → prepare → auto-mitigation |
| `storm_recovery_job` | 30s | Auto-recovery / remmitigation / stabilization |
| `data_retention_job` | Daily 03:15 | TTL refresh + closed-incident purge |

Set `INTERFACE_SCAN_INTERVAL=0` or `INTERFACE_STATS_INTERVAL=0` to disable those schedules (manual API triggers still work).

## Storm pipeline (summary)

```
Stats → Eligibility → Risk → Confirmation → Safety → Prepare → Mitigation → Recovery
```

After successful recovery:

1. Status → `MONITORING` + `recoveredAt`
2. Confirmation reset + safety invalidation
3. Orphan READY incidents cancelled
4. Stabilization → `RESOLVED`, or remmitigate only on a **fresh** post-`recoveredAt` storm

Prepare requires **live CONFIRMED**, current high risk, and SAFE fresher than confirmation. There is no pipeline-generation versioning layer.

## API groups

| Prefix | Purpose |
|--------|---------|
| `/api/auth`, `/api/users` | Login, account, users |
| `/api/devices`, `/api/devices/<id>/scan*` | Inventory, ping, Nmap |
| `/api/history`, `/api/dashboard`, `/api/reports` | History, KPIs, export |
| `/api/discovery` | Subnet sweep |
| `/api/interfaces` | Discovery, stats, monitoring, manual shutdown/recover |
| `/api/storm` | Eligibility, risk, confirmation, safety, incidents, mitigation, recovery |
| `/api/alerts`, `/api/settings` | Alerts, global settings |
| `/health` | Liveness + MongoDB |

Detailed path tables live in the root README.

## MongoDB collections (high level)

`devices`, `pingHistory`, `alerts`, `settings`, `users`, `auditLogs`, `interfaces`, `interface_stats`, `eligibility_results`, `storm_risk_history`, `storm_confirmation_history`, `storm_safety_history`, `storm_incidents`, `storm_mitigation_history`, `storm_recovery_history`, mitigation/recovery lock collections.

## Tests

```powershell
cd backend
.\venv\Scripts\activate
python -m unittest discover -s tests -p "test_*.py" -q
```

## Development notes

- Run commands from `backend/` so imports resolve.
- Keep `FLASK_DEBUG=false` in production; debug binds to `127.0.0.1` only when enabled.
- ICMP and aggressive Nmap often need elevation on Windows.
- Prefer per-device SSH credentials over `SSH_DEFAULT_*`.
- Build the frontend (`npm run build` in `frontend/`) to serve the UI from Flask.
