# NetPulse — Complete Installation & Deployment Guide

This guide takes a person who has never installed NetPulse from a fresh machine to a running system: prerequisites, clone, backend, frontend, MongoDB, first login, and verification of monitoring.

It reflects the **current source code** in this repository. Do not treat older notes or screenshots as authoritative if they conflict with this document.

**Replace `C:\Path\To\NetPulse` (Windows) or `/path/to/NetPulse` (Linux) with the directory where you placed the project.**

---

## IMPORTANT

- Never commit `backend/.env`. It is listed in `.gitignore`.
- Never expose JWT secrets, SMTP passwords, WhatsApp tokens, MongoDB credentials, SSH passwords, SNMP communities, or Fernet keys.
- Use a strong unique `JWT_SECRET` (≥ 32 characters, not a placeholder).
- Use a strong unique `SECRETS_ENCRYPTION_KEY` (Fernet). Keep it stable; rotating it without re-encrypting secrets breaks decrypt.
- Restrict MongoDB so it is not reachable from the Internet.
- Restrict who can open the NetPulse web UI (firewall / reverse proxy).
- Back up MongoDB before upgrades and before any troubleshooting that touches data.
- Do **not** drop the `NetworkMonitor` database (or whatever `DATABASE_NAME` you set) during normal troubleshooting.
- On Windows, ICMP ping (`ping3`) and aggressive Nmap usually need an **elevated** (Administrator) process.

---

## 1. Overview

NetPulse is a LAN monitoring and switch storm-protection platform:

- ICMP ping of devices on a schedule
- Optional Nmap profiling of **Online** devices
- SSH interface discovery (Cisco IOS/XE/NX-OS style commands, including CDP/LLDP)
- SNMP-preferred interface statistics (SSH fallback)
- MAC/ARP polling for port-to-IP enrichment
- CDP/LLDP topology graphs built on read from MongoDB
- ISP upstream ping (separate from device inventory)
- Server inventory as devices with type **Server**
- In-app alerts, optional email, optional WhatsApp
- Storm pipeline (eligibility → risk → confirmation → safety → mitigation → recovery)

**Supported operating systems:** Windows and Linux. The repository includes Windows launchers (`start.bat`, `stop.bat`) and Linux-oriented Gunicorn config. Both are documented below.

**There is no Docker, Kubernetes, Redis, Celery, or nginx configuration in this repository.** Those are not required. A reverse proxy is optional for production TLS.

---

## 2. System Requirements

| Item | Requirement |
|------|-------------|
| Python | README states **3.10+**. `backend/requirements.txt` does **not** pin a Python version. |
| Node.js | README states **18+**. `frontend/package.json` has **no** `engines` field. |
| npm | Used by `frontend/package.json` scripts (`npm install`, `npm run build`). |
| MongoDB | Required. **Version is not specified** in the repository. Use a current MongoDB Community Server that supports TTL indexes and `collMod`. |
| Git | Optional (ZIP extract is also supported). |
| Nmap | Required only for Nmap features. Binary on `PATH` or `NMAP_PATH`. |
| Disk / RAM | Not certified in-repo. `backend/config/mongo_config.py` comments assume a **single** backend + scheduler process on the order of ~500 devices and ~40 switches — that is an implementation comment, not a guarantee. |

---

## 3. Architecture Overview

Production UI is the React build served by Flask (same origin as the API):

```text
Browser
   ↓
Flask (app.py)  —  default HTTP port 5000
   ↓
React production build  (frontend/dist)
   ↓
NetPulse REST API  (/api/...)
   ↓
MongoDB  (DATABASE_NAME, typically NetworkMonitor)
```

The same Flask process also starts **APScheduler** when `NETPULSE_ROLE` is `all` (default) and scheduler enablement is not disabled.

```text
APScheduler (in-process unless you split API / scheduler)
   ├── device_monitor_job       ICMP (dispatch mode by default)
   ├── isp_monitor_job          ISP ping targets
   ├── nmap_scan_job            Online device profiling
   ├── interface_discovery_job  SSH inventory + CDP/LLDP
   ├── interface_stats_job      counters → storm chain
   ├── storm_analysis_job
   ├── storm_confirmation_job
   ├── storm_safety_prepare_job
   ├── mac_arp_poll_job         passive MAC/ARP
   ├── arp_active_sweep_job     optional ARP-forcing sweep
   ├── storm_recovery_job       every 30 seconds
   └── data_retention_job       daily 03:15
```

**Do not run multiple processes that each start APScheduler** unless you intend Option A (API workers + one `run_scheduler.py`). MongoDB leadership reduces duplicate work, but extra schedulers waste resources.

Development-only alternative: Vite on port **5173** proxies `/api` and `/health` to `http://127.0.0.1:5000`. **Do not use Vite for LAN / production users.**

---

## 4. Prerequisites

Install and verify:

| Tool | Why |
|------|-----|
| Python 3.10+ | Flask backend (`backend/requirements.txt`) |
| Node.js 18+ and npm | Frontend install and `npm run build` |
| MongoDB | Persistence; `MONGO_URI` + `DATABASE_NAME` are required |
| Git | Clone (or skip and use ZIP) |
| Nmap | Optional until you use Scan Details / scheduled Nmap |

---

## 5. Install Prerequisites

### Windows

#### Step 1 — Install Python

1. Download Python 3.10 or newer from [python.org](https://www.python.org/downloads/).
2. During setup, enable **Add python.exe to PATH**.
3. Verify:

```powershell
python --version
pip --version
```

Success looks like a version string such as `Python 3.12.x`.

#### Step 2 — Install Node.js (includes npm)

1. Download the LTS installer from [nodejs.org](https://nodejs.org/).
2. Verify:

```powershell
node --version
npm --version
```

Success: both commands print a version. README recommends Node **18+**.

#### Step 3 — Install Git (optional)

```powershell
git --version
```

If Git is missing, install from [git-scm.com](https://git-scm.com/) or use ZIP installation instead.

#### Step 4 — Install MongoDB Community Server

1. Install MongoDB Community from MongoDB’s official Windows installer.
2. The repository **does not document a Windows service name**. After a typical Community install, the service is often named `MongoDB`. Confirm on your machine:

```powershell
Get-Service *mongo*
```

3. Start MongoDB if it is not running. If the service is named `MongoDB`:

```powershell
net start MongoDB
```

If that name does not exist, use the name shown by `Get-Service`, or start `mongod` using the data path from your installer.

4. Verify the `mongod` binary if it is on PATH:

```powershell
mongod --version
```

If `mongod` is not on PATH, that is still OK as long as the MongoDB **service** is running and listening (default `27017`).

#### Step 5 — Install Nmap (optional but recommended)

Install from [nmap.org](https://nmap.org/download.html). Aggressive flags (`-A`, `-O`) often need Administrator. You can set `NMAP_PATH` to the full path of `nmap.exe` later.

Verify if Nmap is on PATH:

```powershell
nmap --version
```

### Linux

Package names vary by distribution. Use your distro’s packages or official installers. Then verify:

```bash
python3 --version
pip3 --version
node --version
npm --version
git --version
mongod --version
nmap --version
```

Start MongoDB using your distro’s service (examples; **confirm the unit name on your host**):

```bash
sudo systemctl start mongod
sudo systemctl status mongod
```

Some distributions use `mongodb` instead of `mongod`. Use the unit that exists on your system.

---

## 6. Download NetPulse

Current `origin` remote for this checkout:

```text
https://github.com/mrzohaibahmed/NetPulse.git
```

If you use a fork or private mirror, replace the URL with yours.

### Git installation

**Windows**

```powershell
cd C:\Path\To\Parent
git clone https://github.com/mrzohaibahmed/NetPulse.git
cd NetPulse
```

**Linux**

```bash
cd /path/to/parent
git clone https://github.com/mrzohaibahmed/NetPulse.git
cd NetPulse
```

If that URL is not available to you, use:

```text
YOUR_REPOSITORY_URL
```

### ZIP installation

1. Extract the archive so you have a folder containing `backend/`, `frontend/`, `README.md`, `start.bat` (Windows).
2. Open a terminal in that folder (`NetPulse`).

**Windows**

```powershell
cd C:\Path\To\NetPulse
```

**Linux**

```bash
cd /path/to/NetPulse
```

---

## 7. Project Directory Structure

Relevant layout (not every file):

```text
NetPulse/
├── README.md
├── USER_MANUAL.md
├── start.bat                 # Windows: Waitress + open browser
├── stop.bat                  # Windows: stop backend window / ports 5000, 5173
├── backend/
│   ├── app.py                # Flask entry, bootstrap, SPA hosting
│   ├── scheduler.py          # APScheduler jobs
│   ├── run_scheduler.py      # Dedicated scheduler process
│   ├── gunicorn.conf.py      # Gunicorn (typically Linux)
│   ├── requirements.txt
│   ├── .env.example
│   ├── .env                  # You create this (never commit)
│   ├── DEPLOYMENT.md
│   └── docs/WHATSAPP_ALERTS.md
└── frontend/
    ├── package.json
    ├── vite.config.ts        # Dev server port 5173 + /api proxy
    └── dist/                 # Created by npm run build
```

There is **no** root `requirements.txt` and **no** Docker files in this repository.

---

## 8. MongoDB Installation & Configuration

### Connection (required)

`backend/config/database.py` **raises** if either variable is missing:

- `MONGO_URI`
- `DATABASE_NAME`

There are **no code defaults**. Copy from `backend/.env.example` and set:

```env
MONGO_URI=mongodb://127.0.0.1:27017
DATABASE_NAME=NetworkMonitor
```

Authenticated URI shape (placeholder only):

```env
MONGO_URI=mongodb://NETPULSE_DB_USER:REPLACE_WITH_PASSWORD@127.0.0.1:27017/NetworkMonitor?authSource=admin
```

Do not paste real passwords into this guide or into Git.

### Verify MongoDB is running

**Windows**

```powershell
Get-Service *mongo*
```

Optional connectivity check if `mongosh` is installed:

```powershell
mongosh --eval "db.runCommand({ ping: 1 })"
```

**Linux**

```bash
sudo systemctl status mongod
```

Or:

```bash
mongosh --eval "db.runCommand({ ping: 1 })"
```

When the backend starts successfully you should see:

```text
MongoDB Connected Successfully!
Database: NetworkMonitor
```

(The database name is whatever you set in `DATABASE_NAME`.)

---

## 9. Backend Installation

Run from the **backend** directory so Python imports resolve.

### Windows

```powershell
cd C:\Path\To\NetPulse\backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\venv\Scripts\Activate.ps1
```

Or use Command Prompt:

```text
Run from: C:\Path\To\NetPulse\backend
```

```cmd
venv\Scripts\activate.bat
pip install -r requirements.txt
```

### Linux

```bash
cd /path/to/NetPulse/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Verify dependencies

With the venv activated:

**Windows**

```powershell
python -c "import flask, pymongo, apscheduler, dotenv; print('ok')"
pip show Flask gunicorn waitress
```

**Linux**

```bash
python -c "import flask, pymongo, apscheduler, dotenv; print('ok')"
pip show Flask gunicorn waitress
```

Success: `ok` prints and packages list without `Package(s) not found`.

Pinned / constrained packages live in `backend/requirements.txt` (Flask 3.1.3, APScheduler 3.11.3, gunicorn 23.0.0, waitress 3.0.2, pymongo 4.17.0, and others).

---

## 10. Environment Configuration

### Create `.env`

`load_dotenv` loads **`backend/.env`** (see `backend/config/database.py`).

**Windows**

```powershell
cd C:\Path\To\NetPulse\backend
copy .env.example .env
```

**Linux**

```bash
cd /path/to/NetPulse/backend
cp .env.example .env
```

Generate secrets (venv activated):

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the first output into `JWT_SECRET` and the second into `SECRETS_ENCRYPTION_KEY`.

**Restart is required** after changing `.env`. Runtime ping/SMTP/storm settings that already exist in MongoDB **Settings** are the source of truth; changing `SCAN_INTERVAL` in `.env` later does **not** rewrite an existing settings document.

### Production vs local debug

| Mode | Typical flags | Notes |
|------|----------------|-------|
| Production / shared host | `FLASK_DEBUG=false`, `NETPULSE_ENV=production` | `JWT_SECRET` and `SECRETS_ENCRYPTION_KEY` required. `CORS_ALLOWED_ORIGINS` required. Bootstrap passwords must be strong. |
| Local lab only | `FLASK_DEBUG=true` | Debug binds Flask to **127.0.0.1 only**. Weak bootstrap passwords are allowed only in this mode. Never enable debug on a LAN-exposed host. |

### Safe example `.env` (no real secrets)

Use this as a starting point. Fill secrets yourself. Uncomment and set `CORS_ALLOWED_ORIGINS` for production.

```env
FLASK_DEBUG=false
NETPULSE_ROLE=all
NETPULSE_ENABLE_SCHEDULER=auto
NETPULSE_ENV=production

# Production: explicit browser origins (no wildcards with credentials).
# For same-origin Flask UI on this host, include the URL users open:
CORS_ALLOWED_ORIGINS=http://127.0.0.1:5000,http://REPLACE_WITH_LAN_IP:5000

FLASK_RUN_HOST=0.0.0.0
FLASK_RUN_PORT=5000

MONGO_URI=mongodb://127.0.0.1:27017
DATABASE_NAME=NetworkMonitor

JWT_SECRET=CHANGE_THIS_TO_A_LONG_RANDOM_SECRET_AT_LEAST_32_CHARS
SECRETS_ENCRYPTION_KEY=REPLACE_WITH_FERNET_GENERATE_KEY_OUTPUT

DEFAULT_ADMIN_USER=admin
DEFAULT_ADMIN_PASSWORD=REPLACE_WITH_STRONG_PASSWORD_MIN_12
DEFAULT_USER_NAME=user
DEFAULT_USER_PASSWORD=REPLACE_WITH_STRONG_PASSWORD_MIN_12

PING_HISTORY_RETENTION_DAYS=7
DATA_RETENTION_DAYS=90
INCIDENT_RETENTION_DAYS=365

MONITOR_RUNTIME_MODE=dispatch
MONITOR_DISPATCHER_INTERVAL_SECONDS=5
SCAN_INTERVAL=60
PING_TIMEOUT_MS=1000
PING_RETRIES=3
PING_FAILURE_CONFIRMATION_SCANS=2
MONITOR_PING_CONCURRENCY=40

ALERT_EMAIL_ENABLED=false
EMAIL_PROVIDER=gmail
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_FROM_NAME=NetPulse
SMTP_USE_TLS=true
ALERT_EMAIL_TO=

WHATSAPP_ALERTS_ENABLED=false

NMAP_SCAN_INTERVAL=3600
INTERFACE_SCAN_INTERVAL=3600
INTERFACE_STATS_INTERVAL=60
```

`MAC_ARP_POLL_INTERVAL` and `ARP_ACTIVE_SWEEP_INTERVAL` are **not** listed in `backend/.env.example`. Code defaults in `backend/config/database.py` are **90** seconds (passive MAC/ARP) and **1800** seconds (active sweep). You may add them to `.env` if you need to change or disable them (`0` disables those jobs).

`NMAP_SCAN_INTERVAL`: if **unset**, `database.py` defaults to **0** (scheduled Nmap **disabled**). `backend/.env.example` sets **3600**. Copying `.env.example` enables hourly Nmap.

### Environment variable table

**Required** means the process fails or refuses production bootstrap if missing/weak. **Seed** means first-boot Mongo `settings` only (later edits are in Settings UI / API). **Restart** means change `.env` then restart the backend (and scheduler process if split).

| Variable | Required | Default in code | Purpose | If omitted | Restart |
|----------|----------|-----------------|--------|------------|---------|
| `MONGO_URI` | Yes | none | Mongo connection | `ValueError` at import | Yes |
| `DATABASE_NAME` | Yes | none | Database name | `ValueError` at import | Yes |
| `JWT_SECRET` | Yes when `FLASK_DEBUG` is false | none | JWT signing; ≥ 32 chars, not a placeholder | Production boot fails | Yes |
| `SECRETS_ENCRYPTION_KEY` | Yes when `FLASK_DEBUG` is false | none | Fernet for SSH/SMTP/SNMP secrets | Production boot fails | Yes (keep stable) |
| `CORS_ALLOWED_ORIGINS` | Yes in production | localhost origins only in development | Allowed browser origins | Production `RuntimeError` | Yes |
| `FLASK_DEBUG` | No | `false` | Werkzeug debug; binds 127.0.0.1 if true | Production-like | Yes |
| `NETPULSE_ENV` | No | `production` if debug off | Environment label / CORS posture | Treated as production | Yes |
| `NETPULSE_ROLE` | No | `all` | `all` / `api` / `scheduler` | Combined API+scheduler | Yes |
| `NETPULSE_ENABLE_SCHEDULER` | No | `auto` | `true` / `false` / `auto` | Auto skips scheduler if Gunicorn workers > 1 | Yes |
| `FLASK_RUN_HOST` | No | `0.0.0.0` | Bind address for `python app.py` | All interfaces | Yes |
| `FLASK_RUN_PORT` | No | `5000` | HTTP port for `python app.py` | 5000 | Yes |
| `GUNICORN_BIND` | No | `127.0.0.1:5000` | Gunicorn listen | Localhost only | Yes |
| `GUNICORN_WORKERS` | No | `1` | Worker count; >1 forces API role in `gunicorn.conf.py` | 1 | Yes |
| `MAX_CONTENT_LENGTH` | No | `2097152` | Max request body bytes | 2 MiB | Yes |
| `MAX_CSV_UPLOAD_BYTES` | No | `1048576` | CSV import cap | 1 MiB | Yes |
| `DEFAULT_ADMIN_USER` | No | `admin` | First-boot admin username | `admin` | Only if `users` empty |
| `DEFAULT_ADMIN_PASSWORD` | Yes in production if `users` empty | none | First-boot admin password | Process raises | Only if `users` empty |
| `DEFAULT_USER_NAME` | No | `user` | First-boot user username | `user` | Only if `users` empty |
| `DEFAULT_USER_PASSWORD` | Yes in production if `users` empty | none | First-boot user password | Process raises | Only if `users` empty |
| `DEFAULT_VIEWER_PASSWORD` | No | alias | Legacy alias for user password | — | Only if `users` empty |
| `JWT_EXPIRE_HOURS` | No | `8` | Token lifetime | 8 hours | Yes |
| `SCAN_INTERVAL` | Seed | `60` | Default `pingInterval` seconds | 60 | Settings persist in Mongo |
| `PING_TIMEOUT_MS` | Seed | `1000` | ICMP attempt timeout | 1000 | Settings persist |
| `PING_RETRIES` | Seed | `3` | Total ICMP attempts per scan | 3 | Settings persist |
| `PING_FAILURE_CONFIRMATION_SCANS` | Seed | `2` | Failed scans before leaving Online | 2 | Settings persist |
| `MONITOR_PING_CONCURRENCY` | Seed | `40` | Parallel ping workers | 40 | Settings persist |
| `MONITOR_RUNTIME_MODE` | No | `dispatch` | `dispatch` or `legacy` | dispatch | Yes |
| `MONITOR_DISPATCHER_INTERVAL_SECONDS` | No | `5` | Dispatcher tick; clamped 1–15 | 5 | Yes (not retargeted by Settings pingInterval) |
| `SCHEDULER_LOCK_TTL_SECONDS` | No | `90` | Scheduler leader lease | 90 | Yes |
| `PING_CLAIM_TTL` | No | computed | Optional claim TTL floor | computed | Yes |
| `MONITOR_CONNECTIVITY_PROBE_HOST` | No | empty | Partition probe; empty disables | disabled | Yes |
| `MONITOR_CONNECTIVITY_PROBE_TIMEOUT_MS` | No | `800` | Probe timeout | 800 | Yes |
| `PING_HISTORY_RETENTION_DAYS` | Seed | `7` | pingHistory TTL | 7 | Settings + TTL job |
| `DATA_RETENTION_DAYS` | Seed | `90` | Stats / storm eval TTL | 90 | Settings + TTL job |
| `INCIDENT_RETENTION_DAYS` | Seed | `365` | Mitigation/recovery logs + closed incidents | 365 | Settings + daily purge |
| `ALERT_EMAIL_ENABLED` | Seed | `true` in example | SMTP on/off for critical offline | example true | Settings persist |
| `EMAIL_PROVIDER` | Seed | `gmail` | `gmail` or `outlook` | gmail unless Outlook host | Settings persist |
| `SMTP_HOST` | Seed | `smtp.gmail.com` | SMTP server | gmail host | Settings persist |
| `SMTP_PORT` | Seed | `587` | SMTP port | 587 | Settings persist |
| `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` / `ALERT_EMAIL_TO` | For email | empty | Credentials and recipient | no mail | Settings persist |
| `SMTP_FROM_NAME` | Seed | `NetPulse` | From display name | NetPulse | Settings persist |
| `SMTP_USE_TLS` | Seed | `true` | STARTTLS | true | Settings persist |
| `STORM_EMAIL_*` | Seed | enabled in example | Storm shutdown/recovery/failure mail | see example | Settings persist |
| `WHATSAPP_*` | No | disabled | Meta Cloud API | no WhatsApp | Yes |
| `NMAP_SCAN_INTERVAL` | No | **0** if unset | Scheduled Nmap seconds; 0 disables | disabled unless set in `.env` | Yes |
| `NMAP_ARGUMENTS` / `NMAP_QUICK_ARGUMENTS` | No | see `.env.example` | Nmap flag strings (validated) | code defaults | Yes |
| `MAX_SCAN_THREADS` | No | `5` | Concurrent Nmap | 5 | Yes |
| `NMAP_TIMEOUT` | No | `300` | Per-host timeout | 300 | Yes |
| `NMAP_CACHE_TTL` | No | `21600` | Reuse recent Nmap | 6 hours | Yes |
| `NMAP_PATH` | No | empty | Full path to nmap | PATH auto-detect | Yes |
| `INTERFACE_SCAN_INTERVAL` | No | `3600` | SSH discovery; **0 disables schedule** | 3600 | Yes |
| `MAX_INTERFACE_THREADS` | No | `5` | Concurrent SSH discovery | 5 | Yes |
| `INTERFACE_STATS_INTERVAL` | No | `60` | Stats + storm jobs; **0 disables** | 60 | Yes |
| `MAX_INTERFACE_STATS_THREADS` | No | `8` | Concurrent stats polls | 8 | Yes |
| `INTERFACE_STATS_BATCH_SIZE` | No | `500` | Bulk insert chunk | 500 | Yes |
| `MAC_ARP_POLL_INTERVAL` | No | `90` | Passive MAC/ARP; **0 disables** | 90 | Yes |
| `MAX_MAC_ARP_POLL_THREADS` | No | `5` | Concurrent MAC/ARP | 5 | Yes |
| `ARP_ACTIVE_SWEEP_INTERVAL` | No | `1800` | Active ARP sweep; **0 disables** | 1800 | Yes |
| `ARP_ACTIVE_SWEEP_MAX_HOSTS` | No | `512` | Cap hosts per subnet per sweep | 512 | Yes |
| `SSH_DEFAULT_*` | No | empty / port 22 / `cisco_ios` | Fallback if device has no creds | prefer per-device | Yes |
| `SSH_TIMEOUT` | No | `30` | SSH timeout | 30 | Yes |
| `SSH_KNOWN_HOSTS_FILE` | No | empty | known_hosts path | production RejectPolicy | Yes |
| `SSH_ALLOW_UNKNOWN_HOSTS` | No | false | Lab only with `FLASK_DEBUG=true` | false | Yes |
| `SNMP_DEFAULT_COMMUNITY` | No | `public` | Fallback community | `public` | Yes |
| `SNMP_DEFAULT_VERSION` | No | `2c` | SNMP version | 2c | Yes |
| `SNMP_DEFAULT_PORT` | No | `161` | SNMP port | 161 | Yes |
| `SNMP_TIMEOUT` / `SNMP_RETRIES` | No | `3` / `1` | SNMP timing | those defaults | Yes |
| `LOGIN_MAX_FAILURES` / `LOGIN_WINDOW_SECONDS` / `LOGIN_LOCKOUT_SECONDS` | No | 8 / 900 / 900 | Login lockout | those defaults | Yes |
| `MAX_GLOBAL_SSH_SESSIONS` | No | `10` | Global SSH slot cap | 10 | Yes |
| `SSH_SLOT_WAIT_SECONDS` | No | `30` | Wait for SSH slot | 30 | Yes |
| `STORM_*` | Mostly seed / flags | see `.env.example` | Storm eligibility, risk, safety | code/example defaults | Yes for env; Settings for some |

Storm threshold and weight variables are documented in `backend/.env.example`. They are optional overrides; first install can rely on defaults.

---

## 11. Database Initialization

**Automatic.** On import/startup, `backend/app.py` → `bootstrap()`:

- Connects to MongoDB and pings
- `ensure_settings()` — creates the global settings document if missing
- Creates indexes (devices, ISPs, users, monitoring, storm, TTL, login rate limit, reports, locks)
- `ensure_isp_connections()` — seeds ISP slots per site location
- `ensure_default_admin()` — seeds users **only if `users` is empty**
- TTL indexes via `ensure_retention_ttl_indexes()` (uses `collMod` to update existing TTL, does not drop indexes just to change TTL)

You do **not** create collections by hand. MongoDB creates them when documents or indexes are written.

There is no separate `init` / `migrate` command required for a new install. Optional operator scripts exist (`backend/migrate_encrypt_secrets.py`, `backend/clear_database.py`, `backend/clear_devices.py`, `backend/delete_ping_history.py`) — they are **not** part of first install. `clear_database.py` is destructive; do not run it on production.

---

## 12. Administrator Account

When `users` is empty, startup inserts two accounts (`ensure_default_admin`):

| Role | Username env | Password env |
|------|----------------|--------------|
| `admin` | `DEFAULT_ADMIN_USER` (default `admin`) | `DEFAULT_ADMIN_PASSWORD` |
| `user` | `DEFAULT_USER_NAME` (default `user`) | `DEFAULT_USER_PASSWORD` |

Both get `mustChangePassword=true`. The login page does **not** show passwords.

**Production** (`FLASK_DEBUG` off): passwords must be at least **12** characters and must not be well-known values (`admin123`, `password`, `changeme`, `netpulse`, etc.). Missing/weak passwords cause a `RuntimeError` and the process will not start.

**Local debug only** (`FLASK_DEBUG=true`): if env passwords are empty, code may seed lab defaults. Never use those on a shared host.

After login, change password on **Users** / **Account** (`/account`). Self-service new password minimum is **6** characters and must differ from the current password.

Admins can also create users later via **Users** (`POST /api/users`): username ≥ 3, password ≥ 6, role `admin` or `user`.

Existing users are **never** overwritten by bootstrap.

---

## 13. Frontend Installation

Package manager: **npm** (`frontend/package.json` + `package-lock.json`).

**Windows**

```powershell
cd C:\Path\To\NetPulse\frontend
npm install
```

**Linux**

```bash
cd /path/to/NetPulse/frontend
npm install
```

Success: `frontend/node_modules` exists and `npm install` exits 0.

Development server (optional, **not** for production):

```powershell
cd C:\Path\To\NetPulse\frontend
npm run dev
```

```bash
cd /path/to/NetPulse/frontend
npm run dev
```

UI: `http://127.0.0.1:5173` — proxies `/api` and `/health` to Flask on port 5000. Backend must already be running.

There is **no** required `frontend/.env` for the production SPA: the UI calls same-origin `/api`.

---

## 14. Build the Frontend

**Windows**

```powershell
cd C:\Path\To\NetPulse\frontend
npm run build
```

**Linux**

```bash
cd /path/to/NetPulse/frontend
npm run build
```

Script: `tsc -b && vite build`.

**Output:** `frontend/dist/` containing `index.html` and `assets/` (JS/CSS).

Flask serves that directory when it exists (`FRONTEND_DIST` in `app.py`). `start.bat` **refuses to start** if `frontend\dist\index.html` is missing.

After UI changes, rebuild and restart the backend so operators do not see a stale SPA.

---

## 15. Start NetPulse

Ensure MongoDB is running and `backend/.env` is complete.

### Combined process (typical first install)

This is the default: `NETPULSE_ROLE=all`, scheduler starts inside the same process.

#### Windows — recommended launcher

```text
Run from: C:\Path\To\NetPulse
```

Double-click `start.bat` or:

```powershell
cd C:\Path\To\NetPulse
.\start.bat
```

What it does:

1. Requires `backend\venv\Scripts\python.exe`
2. Requires `frontend\dist\index.html`
3. Starts Waitress: `waitress-serve --host=0.0.0.0 --port=5000 --threads=8 app:app`
4. Opens `http://127.0.0.1:5000`

Stop with `stop.bat` (kills the NetPulse Backend window and processes on ports 5000 and 5173).

#### Windows — manual (Flask development server)

```powershell
cd C:\Path\To\NetPulse\backend
.\venv\Scripts\Activate.ps1
python app.py
```

Default: host `0.0.0.0`, port `5000`. Open `http://127.0.0.1:5000`.

#### Windows — Waitress without the bat file

```powershell
cd C:\Path\To\NetPulse\backend
.\venv\Scripts\Activate.ps1
waitress-serve --host=0.0.0.0 --port=5000 --threads=8 app:app
```

#### Linux — Flask

```bash
cd /path/to/NetPulse/backend
source venv/bin/activate
python app.py
```

#### Linux — Gunicorn (single worker, API + scheduler)

```bash
cd /path/to/NetPulse/backend
source venv/bin/activate
export NETPULSE_ENV=production
export GUNICORN_WORKERS=1
export GUNICORN_BIND=0.0.0.0:5000
export CORS_ALLOWED_ORIGINS=http://127.0.0.1:5000,http://REPLACE_WITH_LAN_IP:5000
gunicorn -c gunicorn.conf.py "app:app"
```

**Never** set `GUNICORN_WORKERS>1` with the scheduler enabled in every worker. `gunicorn.conf.py` forces `NETPULSE_ROLE=api` when workers > 1; then you **must** run `python run_scheduler.py` separately.

### Split processes (scale / production Option A)

**API (no scheduler)**

```bash
cd /path/to/NetPulse/backend
source venv/bin/activate
export NETPULSE_ROLE=api
export NETPULSE_ENABLE_SCHEDULER=false
export NETPULSE_ENV=production
export CORS_ALLOWED_ORIGINS=https://netpulse.example.com
export GUNICORN_BIND=127.0.0.1:5000
gunicorn -c gunicorn.conf.py "app:app"
```

**Scheduler only** (exactly one instance):

```bash
cd /path/to/NetPulse/backend
source venv/bin/activate
export NETPULSE_ENV=production
python run_scheduler.py
```

`run_scheduler.py` sets `NETPULSE_ROLE=scheduler`, imports `app` (bootstrap + scheduler), and blocks. It does not serve HTTP.

### Health checks

| URL | Meaning |
|-----|---------|
| `GET /health/live` | Process liveness |
| `GET /health/ready` | Mongo (+ scheduler when expected) |
| `GET /health` | Legacy combined health |

**Windows**

```powershell
curl http://127.0.0.1:5000/health
```

**Linux**

```bash
curl http://127.0.0.1:5000/health
```

Success includes database connected (JSON fields from `ops_health`). Sidebar shows API online / DB connected when `/health` reports server running and database connected.

### Logs

Console plus `backend/logs/monitor.log`.

---

## 16. First Login

1. Open `http://127.0.0.1:5000` (or `http://<HOST-LAN-IP>:5000` from another PC).
2. You should see the **login** page (`/login`).
3. Sign in with `DEFAULT_ADMIN_USER` / `DEFAULT_ADMIN_PASSWORD` (placeholders: `ADMIN_USERNAME` / `ADMIN_PASSWORD`).
4. If `mustChangePassword` is true, the UI sends you to **Users** / account (`/account`) until you set a new password.
5. After that, **Enterprise Overview** (`/`) should load.

If you see JSON `{"message": "Network Monitor API is running"}` instead of the UI, `frontend/dist` is missing — run `npm run build` and restart.

---

## 17. Initial Configuration

Admin-only **Settings** (`/settings`):

1. **ISP connectivity** — `IspSettingsSection`: per-site slots, name, ping **target**, **monitor** checkbox. Sites in code: **Mills**, **Karachi**, **Lahore**.
2. **Ping monitoring** — Interval (seconds, min 5), Timeout (ms), Retry count.
3. **SMTP alerts** — Enable email, provider Gmail/Outlook, host/port/user/password/from/to, **Send test email**.
4. **Storm email notifications** — shutdown / recovery / failure emails.
5. **Storm confirmation** — Storm risk threshold (%), Required confirmation polls.
6. **Recovery protection** — Cooldown (minutes), Stabilization (seconds), Max attempts.
7. **Data retention** — Ping history days, telemetry days, incident days.

Mitigation mode **automatic vs manual** is controlled on **Storm Protection → Overview**, not on the Settings page (`USER_MANUAL.md`).

Optional: set `MONITOR_CONNECTIVITY_PROBE_HOST` in `.env` to a always-on host so a collector partition does not mass-mark devices offline.

---

## 18. Add Switches

1. Open **Devices** (`/devices`).
2. Use **Add device**.
3. Required: **Hostname**, **IP address** (IPv4 or IPv6 in the UI), **Device type**.
4. Set type to **Switch** or **Managed Switch** (Interfaces page only lists managed-switch types).
5. Enable **Monitor**. Set **Critical** if outages should create critical alerts/email.
6. Under **SSH credentials (optional)**: username, password, enable secret, vendor (stored as `credentials.sshVendor`; default fallback vendor is `cisco_ios`).
7. Save. Wait until status becomes **Online** (ICMP).
8. Open **Interfaces** (`/interfaces`) → **Discover all** or discover that device (`POST /api/interfaces/discover/<id>`). Admins can also wait for `interface_discovery_job` (default hourly).

Canonical types in the form include `Router`, `WiFi Router`, `Switch`, `Managed Switch`, `Firewall`, `Server`, `Linux Server`, `ESXi Server`, and others in `frontend/src/modules/ping/constants/devices.ts`.

---

## 19. Add Servers

Servers are **devices**, not a separate collection.

1. **Devices** → **Add device**.
2. Set **Device type** to **Server** (or `Linux Server` / `ESXi Server` as appropriate).
3. Fill **Hostname** and **IP address**.
4. Enable **Monitor**.
5. **Show on dashboard** applies when type is **Server** (`showOnDashboard`). Enterprise Overview site tiles list servers with `deviceType` server (case-insensitive).
6. Optional **Location** (Mills / Karachi / Lahore) groups site monitoring.
7. SSH is not required for ICMP-only servers. Nmap still needs reachability and the Nmap binary.

Server monitoring is the same ping pipeline as other devices. It is **not** the ISP monitor.

---

## 20. Configure ISPs

ISP monitoring uses collection `ispConnections`, **not** `devices`.

- First boot seeds slots (three per site: Mills `isp-1`…`isp-3`, Karachi/Lahore slug-based ids).
- Configure under **Settings** (ISP section): **name**, **target** (IPv4 or hostname), **monitor**, **location**.
- Scheduler job `isp_monitor_job` uses Settings **pingInterval** (same cadence as device interval for the ISP job registration).
- Dashboard **ISP Connectivity** shows status `Unknown` / `Online` / `Offline`.
- Manual ping: ISP scan from Settings UI (`POST /api/isps/<id>/scan`).
- Offline alerts use the same consecutive-failure threshold as critical devices (**3**). Email uses ISP-specific templates. Recovery can send ISP recovery email (unlike device recovery, which has no recovery **email** today — WhatsApp recovery is separate).

Leave target empty and monitor off for unused slots.

---

## 21. Device Monitoring Requirements

| Need | Who | Protocol | Notes |
|------|-----|----------|--------|
| ICMP echo | All monitored devices and ISP targets | ICMP | Windows: run NetPulse elevated for reliable `ping3` |
| SSH | Switches for discovery, SSH stats fallback, storm mitigation/recovery | TCP **22** default (`SSH_DEFAULT_PORT` / per-device `sshPort`) | Production SSH uses host-key RejectPolicy unless lab debug |
| SNMP | Switches for preferred stats | UDP **161** default | Community `SNMP_DEFAULT_COMMUNITY` or per-device |
| CDP and/or LLDP | Topology neighbors | On the switch | Parsed during SSH discovery; SNMP is **not** used for topology neighbors |
| Nmap | Optional profiling | TCP/UDP to targets | Online devices only for scheduled job |
| SMTP | Optional alerts | TCP **587** typical | Gmail app password or M365 SMTP AUTH |
| HTTPS to Meta | Optional WhatsApp | 443 outbound | Cloud API |

The NetPulse host must be able to **reach** those devices. Devices must **permit** ICMP/SSH/SNMP from the NetPulse host (switch ACLs / firewalls).

---

## 22. Interface Discovery

- **What:** SSH inventory of ports, VLANs, CDP/LLDP neighbors, monitoring intent. Written to `interfaces`.
- **Not the same as** ICMP ping or interface **statistics**.
- **Schedule:** `INTERFACE_SCAN_INTERVAL` default **3600** seconds. Set **0** to disable the job; manual `POST /api/interfaces/discover-all` still works.
- **Concurrency:** `MAX_INTERFACE_THREADS` default 5.
- **Requirements:** Device **Online**, SSH credentials (per-device or `SSH_DEFAULT_*`), Cisco-style CLI as implemented in `interface_collection`.
- **Where to look:** **Interfaces** (`/interfaces`), **Switches** (`/switches`).
- **Verify:** After discovery, ports appear for that switch. Console/logs mention interface discovery. Neighbors populate `interfaces.neighbor`.

---

## 23. Interface Statistics

- **What:** Counter/rate samples for storm risk. Stored in `interface_stats`.
- **Transport:** SNMP preferred, SSH fallback (`services/interface_collection`).
- **Schedule:** `INTERFACE_STATS_INTERVAL` default **60** seconds. **0** disables scheduled stats **and** the coupled storm analysis/confirmation/safety jobs.
- **Manual:** `POST /api/interfaces/stats/collect-all` or per-device collect.
- **UI:** Interface detail / history (`/interfaces/:deviceId/:interfaceName`). Frontend also polls other pages on their own intervals.
- **Verify:** Documents appear in `interface_stats`; Storm **Risk Analysis** starts scoring after samples exist.

---

## 24. MAC / ARP Monitoring

- **Enabled by default** at code defaults: `MAC_ARP_POLL_INTERVAL=90`. **0** disables.
- **Passive poll** (`mac_arp_poll_job`): MAC tables on switches; ARP on devices that route. Collections `port_mac_table`, `arp_cache`.
- **Active sweep** (`arp_active_sweep_job`): default **1800** seconds; pings unresolved hosts on **real** connected subnets (cap `ARP_ACTIVE_SWEEP_MAX_HOSTS=512`). More invasive; set interval **0** to disable.
- **Requirements:** SSH to switches/routers as implemented in `mac_arp_collector.py` (Cisco MAC/ARP parsing).
- **Use:** Endpoint IP enrichment for topology / port resolution — not a substitute for ping monitoring.
- **Verify:** After polls, `port_mac_table` / `arp_cache` have documents; topology may show richer endpoint IPs.

---

## 25. Topology Discovery

Topology is **computed on read** from `interfaces.neighbor`. There is **no** topology collection.

Protocols: **CDP and LLDP** via SSH (`show cdp/lldp neighbors detail` during discovery).

1. Add supported switches; wait until **Online**.
2. Enable CDP/LLDP on the switches.
3. Run interface discovery (manual or wait for hourly job).
4. Open **Topology** (`/topology`).
5. **Level 1 — Switch neighbors:** `GET /api/topology/switch/<device_id>` — one switch + neighbors. Current backend builds this with `live_only=False` (stale/offline/unresolved links kept, typically shown dashed).
6. **Level 2 — Full topology:** `GET /api/topology/full` — also `live_only=False` in the current `topology_service.py` routes (all discovered connections, including endpoints, matching Level 1). Older README text that described Level 2 as Online-only is not what the current handlers pass.

Picker: `GET /api/topology/switches`. UI polls on the order of **30 seconds**.

---

## 26. Alerting

### In-app alerts

- **Devices** with `critical: true`: after `pingFailureConfirmationScans` (default **2**) status becomes `Offline (Critical)`. An in-app alert is created when `consecutiveFailures >= 3` (`CRITICAL_OFFLINE_ALERT_THRESHOLD`).
- Non-critical devices become **Not Reachable** with **no** critical offline alert.
- At most one **active** critical-offline alert per device.
- When the device returns **Online**, active alerts are auto-resolved.
- **ISPs:** separate alert type `ISP Offline` at the same failure threshold of **3**.
- Operators: **Alerts** (`/alerts`) — acknowledge / dismiss (`user` or `admin`).

### Email (devices)

Configure **Settings → SMTP alerts** or `.env` `ALERT_EMAIL_*` / `SMTP_*`.

Gmail: use an **App Password**, not the account password.

Test: **Send test email** (`POST /api/settings/test-email`).

Fully configured SMTP (host, user, password, from, to) is required for send. Failures are logged; monitoring continues.

Device **recovery does not send email**. ISP recovery **can** send email (`send_isp_recovery_alert`).

### Storm email

**Settings → Storm email notifications**. Uses the same SMTP. Optional `STORM_EMAIL_TO` or falls back to alert recipient.

---

## 27. Email Notifications

See §26. Restart after `.env` SMTP changes if you are not using the Settings UI (Settings writes Mongo).

Troubleshooting: wrong app password, M365 SMTP AUTH disabled, `ALERT_EMAIL_ENABLED` / SMTP enabled false, recipient empty, device not **critical**, failures &lt; 3.

---

## 28. WhatsApp Notifications

Optional Meta **WhatsApp Cloud API**. Disabled by default (`WHATSAPP_ALERTS_ENABLED=false`).

Full steps: `backend/docs/WHATSAPP_ALERTS.md`.

You need: access token, phone number ID, recipient E.164 numbers **without** `+`, approved templates matching `WHATSAPP_CRITICAL_ALERT_TEMPLATE` and `WHATSAPP_RECOVERY_ALERT_TEMPLATE`.

Restart backend after `.env` changes.

**Test (admin):** `POST /api/settings/test-whatsapp` with Bearer token.

WhatsApp is sent on critical device offline (same moment as alert insert) and on device recovery (WhatsApp only for devices). Failures are logged (`[WHATSAPP]`); email and monitoring continue.

Never put real tokens in Git or this file.

---

## 29. Recovery Notifications

| Event | Email | WhatsApp | In-app |
|-------|-------|----------|--------|
| Critical device offline | Yes if SMTP configured | Yes if WhatsApp critical enabled | Alert created |
| Critical device back Online | **No** device recovery email | Yes if recovery templates enabled | Alert resolved |
| ISP offline / recovery | ISP email helpers | Not the device WhatsApp path | ISP alerts |
| Storm port shutdown / recover / failure | Storm email settings | Not via WhatsApp Cloud templates above | Storm UI / alerts as implemented |

Storm **auto-recovery** is the port recovery job (`storm_recovery_job`, **30s**), gated by Settings **Recovery protection** and Storm Overview **autoRecovery** / **mitigationMode**.

---

## 30. Monitoring Scheduler

Starts automatically from `app.py` when `should_start_scheduler()` is true (default combined process).

| Job ID | Default interval | Purpose |
|--------|------------------|---------|
| `device_monitor_job` | Dispatcher **5s**; per-device cadence **pingInterval 60s** | ICMP dispatch (default `MONITOR_RUNTIME_MODE=dispatch`) |
| `isp_monitor_job` | **pingInterval** (60s seed) | ISP targets |
| `nmap_scan_job` | `.env.example` **3600**; code default **0** if unset | Online Nmap |
| `interface_discovery_job` | **3600s** | SSH inventory |
| `interface_stats_job` | **60s** | Stats |
| `storm_analysis_job` | same as stats interval | Risk |
| `storm_confirmation_job` | same as stats | Confirmation |
| `storm_safety_prepare_job` | same as stats | Safety / prepare / auto-mitigation |
| `mac_arp_poll_job` | **90s** | Passive MAC/ARP |
| `arp_active_sweep_job` | **1800s** | Active ARP sweep |
| `storm_recovery_job` | **30s** | Auto-recovery |
| `data_retention_job` | cron **03:15 daily** | TTL ensure + purge RESOLVED incidents |

Jobs do **not** require a second process in the default `NETPULSE_ROLE=all` model.

---

## 31. Data Retention

Configured in **Settings → Data retention** (and seeded from env on first boot):

| Window | Default | Collections / behavior |
|--------|---------|------------------------|
| `pingHistoryRetentionDays` | 7 | TTL on `pingHistory.timestamp` (`idx_pingHistory_timestamp_ttl`) |
| `dataRetentionDays` | 90 | TTL on `interface_stats`, `eligibility_results`, `storm_risk_history`, `storm_confirmation_history`, `storm_safety_history` |
| `incidentRetentionDays` | 365 | TTL on mitigation/recovery **history**; daily job purges **RESOLVED** `storm_incidents` only |

TTL is applied with `create_index` / `collMod` (`expireAfterSeconds`). MongoDB may delay actual deletes (TTL monitor thread). Keep host time (NTP) accurate.

**Verify (mongosh),** using your database name:

```javascript
use NetworkMonitor
db.pingHistory.getIndexes()
db.interface_stats.getIndexes()
```

Look for `expireAfterSeconds` on the TTL index names above.

Do **not** drop collections to “fix” TTL.

---

## 32. Firewall Configuration

### Inbound (to the NetPulse host)

| Port | Traffic |
|------|---------|
| **5000/tcp** | Web UI + API (`FLASK_RUN_PORT` / Waitress / Gunicorn). Restrict to management LAN. |
| **5173/tcp** | Vite **dev only** — do not expose in production. |
| **27017/tcp** | MongoDB — **localhost or private only**, never Internet. |

### Outbound (from the NetPulse host)

| Traffic | Purpose |
|---------|---------|
| ICMP | Device and ISP ping |
| TCP 22 | SSH to switches |
| UDP 161 | SNMP stats |
| TCP/UDP to Nmap targets | Optional scans |
| TCP 587 (or your `SMTP_PORT`) | Email |
| TCP 443 | WhatsApp Cloud API / package downloads |

Inbound **5000** is operators hitting NetPulse. Outbound ICMP/SSH/SNMP is NetPulse hitting the network. Opening 5000 does **not** replace device ACLs allowing the NetPulse IP.

---

## 33. Quick Start — New Machine

Shortest path for an administrator (Windows shown; Linux: `python3`, `source venv/bin/activate`, `cp`).

```text
1. Install Python 3.10+, Node.js 18+, MongoDB, Git (optional), Nmap (optional)
2. Clone or extract NetPulse
3. Create backend venv and pip install -r requirements.txt
4. Copy backend/.env.example → backend/.env and set secrets + MONGO_URI + DATABASE_NAME + CORS
5. npm install and npm run build in frontend/
6. Start MongoDB
7. Start backend (start.bat or python app.py / waitress / gunicorn)
8. Open http://127.0.0.1:5000
9. Login, change password, add devices / ISPs
```

**Windows copy/paste** (replace paths and secrets):

```powershell
cd C:\Path\To\NetPulse\backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
notepad .env
cd C:\Path\To\NetPulse\frontend
npm install
npm run build
net start MongoDB
cd C:\Path\To\NetPulse
.\start.bat
```

**Linux copy/paste:**

```bash
cd /path/to/NetPulse/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env
cd /path/to/NetPulse/frontend
npm install
npm run build
sudo systemctl start mongod
cd /path/to/NetPulse/backend
python app.py
```

Open `http://127.0.0.1:5000`.

---

## 34. Verification Checklist

```text
[ ] Python installed (python --version / python3 --version)
[ ] Node.js installed (node --version)
[ ] npm installed (npm --version)
[ ] MongoDB installed and running
[ ] Repository cloned or ZIP extracted
[ ] backend/venv created
[ ] pip install -r backend/requirements.txt succeeded
[ ] frontend npm install succeeded
[ ] backend/.env created (not committed)
[ ] MONGO_URI and DATABASE_NAME set
[ ] JWT_SECRET and SECRETS_ENCRYPTION_KEY set (production)
[ ] DEFAULT_ADMIN_PASSWORD / DEFAULT_USER_PASSWORD set (empty users, production)
[ ] CORS_ALLOWED_ORIGINS set (production)
[ ] Frontend built (frontend/dist/index.html exists)
[ ] Backend started without traceback
[ ] GET /health shows database connected
[ ] Login page opens at http://127.0.0.1:5000
[ ] Admin login works; password changed if required
[ ] Test device added with Monitor on
[ ] Device becomes Online (ping)
[ ] pingHistory records appear (History page)
[ ] Switch SSH discovery lists interfaces
[ ] Interface stats samples appear
[ ] MAC/ARP collections populate if SSH MAC/ARP works
[ ] Topology shows CDP/LLDP links after discovery
[ ] Critical offline creates an Alert (optional test)
[ ] Send test email succeeds (if SMTP configured)
[ ] POST /api/settings/test-whatsapp succeeds (if WhatsApp configured)
[ ] ISP slot with target+monitor shows Online/Offline
[ ] Scheduler log lines show jobs registered
```

---

## 35. Troubleshooting

### MongoDB connection failed

**Symptoms:** Startup traceback `MongoDB Connection Failed` or `MONGO_URI not found`.  
**Cause:** Service down, wrong URI, missing `DATABASE_NAME`, firewall.  
**Check:** Service status; ping with `mongosh`; inspect `backend/.env` (no secrets in logs — `safe_mongo_log_summary` prints host/port only).  
**Fix:** Start MongoDB; correct URI; set both required variables; restart backend.

### MongoDB service not running

**Symptoms:** Connection refused on 27017.  
**Check:** `Get-Service *mongo*` / `systemctl status mongod`.  
**Fix:** Start the service. Do not guess the service name if `Get-Service` shows a different one.

### Python / pip / venv

**Symptoms:** `python` not found; `Activate.ps1` blocked; pip errors.  
**Fix:** Install Python with PATH; use `python -m pip`; Process-scope ExecutionPolicy Bypass; recreate `venv` if the interpreter was upgraded.

### Node / npm

**Symptoms:** `npm` not found; install/build failures.  
**Fix:** Install Node 18+; delete `node_modules` and rerun `npm install` (does not delete MongoDB). Disk full or network proxy can fail installs.

### Port already in use

**Symptoms:** Flask/Waitress cannot bind 5000.  
**Check:** Windows `stop.bat` or identify PID on 5000.  
**Fix:** Stop the other process or set `FLASK_RUN_PORT` / Waitress `--port`.

### Missing JWT / Fernet / CORS / bootstrap password

**Symptoms:** Immediate `RuntimeError` on start.  
**Fix:** Set production secrets and `CORS_ALLOWED_ORIGINS`; strong 12+ char bootstrap passwords if `users` is empty.

### Scheduler not starting

**Symptoms:** No ping jobs; logs `Scheduler not started`.  
**Cause:** `NETPULSE_ROLE=api`, `NETPULSE_ENABLE_SCHEDULER=false`, Gunicorn workers > 1, or Flask debug reloader parent.  
**Fix:** Use `all` + one worker, or run `python run_scheduler.py`.

### Blank page / API JSON on `/`

**Cause:** Missing or stale `frontend/dist`.  
**Fix:** `npm run build`; restart backend (Flask reads dist existence at import — restart after first build).

### Device always offline

**Cause:** ICMP blocked; Windows without Administrator; wrong IP; `monitor` false.  
**Fix:** Elevate process; permit ICMP; enable Monitor; check History.

### SSH / SNMP / discovery / topology failures

**Cause:** Bad credentials, unknown SSH host key, device not Online, CDP/LLDP off, non-Cisco CLI.  
**Fix:** Per-device SSH on **Edit device**; lab-only `SSH_ALLOW_UNKNOWN_HOSTS` with debug; enable CDP/LLDP; run Discover all.

### Statistics missing

**Cause:** Stats interval 0; SNMP community wrong; no discovery yet.  
**Fix:** Discover interfaces first; fix SNMP; confirm `INTERFACE_STATS_INTERVAL`.

### Email / WhatsApp / recovery notification

**Cause:** SMTP incomplete; WhatsApp disabled/missing env; expecting device recovery **email** (not implemented); failures &lt; 3; device not critical.  
**Fix:** Test email button; WhatsApp test endpoint; read §26–29.

---

## 36. Production Deployment

Recommended layout on the host:

```text
C:\NetPulse\          or  /opt/netpulse/
  backend\            venv, .env, app.py
  frontend\dist\      production SPA only needed at runtime
```

1. `FLASK_DEBUG=false`, `NETPULSE_ENV=production`.
2. Strong `JWT_SECRET`, `SECRETS_ENCRYPTION_KEY`, bootstrap passwords (first boot only).
3. MongoDB authenticated, bound to localhost, backups scheduled.
4. `npm run build` on deploy.
5. Windows: Waitress as in `start.bat` (`0.0.0.0:5000`, 8 threads) **or** bind `127.0.0.1` behind IIS/Caddy/nginx TLS (optional; not in-repo).
6. Linux: Gunicorn `GUNICORN_WORKERS=1` **or** API workers + one `run_scheduler.py`.
7. Set `CORS_ALLOWED_ORIGINS` to the exact URLs browsers use (scheme + host + port).
8. Firewall: §32.
9. Logs: `backend/logs/monitor.log`.
10. One scheduler per environment.

See also `backend/DEPLOYMENT.md`.

---

## 37. Automatic Startup

The repository does **not** ship a Windows service wrapper or a systemd unit file. The following is **optional**.

### Windows (optional)

- **Task Scheduler:** At startup, run `C:\Path\To\NetPulse\start.bat` (highest privileges if ICMP/Nmap require it).
- **NSSM / WinSW:** Wrap `backend\venv\Scripts\waitress-serve.exe` with arguments `--host=0.0.0.0 --port=5000 --threads=8 app:app` and **Start in** `backend`. This is operator tooling, not part of the app.

### Linux (optional systemd)

API + scheduler in one Gunicorn worker — adjust User, paths, and CORS:

```ini
[Unit]
Description=NetPulse
After=network.target mongod.service

[Service]
Type=simple
WorkingDirectory=/path/to/NetPulse/backend
Environment=NETPULSE_ENV=production
Environment=GUNICORN_WORKERS=1
Environment=GUNICORN_BIND=127.0.0.1:5000
EnvironmentFile=/path/to/NetPulse/backend/.env
ExecStart=/path/to/NetPulse/backend/venv/bin/gunicorn -c gunicorn.conf.py app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

If you use multiple Gunicorn workers, add a **second** unit for:

```text
ExecStart=/path/to/NetPulse/backend/venv/bin/python run_scheduler.py
```

with `NETPULSE_ROLE=scheduler` (already defaulted in that script).

---

## 38. Backup & Restore

Database name is whatever you set in `DATABASE_NAME` (example **NetworkMonitor**).

Backup (no credentials shown):

**Windows / Linux**

```bash
mongodump --uri="mongodb://127.0.0.1:27017" --db=NetworkMonitor --out=C:\Path\To\Backups\netpulse-YYYYMMDD
```

```bash
mongodump --uri="mongodb://127.0.0.1:27017" --db=NetworkMonitor --out=/path/to/backups/netpulse-YYYYMMDD
```

If MongoDB uses auth, put the user in the URI locally; do not commit it.

**What is backed up:** collections (devices, users, settings, history, interfaces, storm data, etc.). **Not** backed up: `backend/.env`, `frontend/dist`, Python venv.

Store backups off-host. Restore **overwrites** data in that database:

```bash
mongorestore --uri="mongodb://127.0.0.1:27017" --db=NetworkMonitor /path/to/backups/netpulse-YYYYMMDD/NetworkMonitor
```

**WARNING:** Restore can replace live documents. Stop the NetPulse process first. Do not run restore against the wrong `--db`.

---

## 39. Updating NetPulse

**Do not drop MongoDB** as part of an update.

**Git**

```powershell
cd C:\Path\To\NetPulse
git pull
```

```bash
cd /path/to/NetPulse
git pull
```

Then:

1. Activate `backend/venv`
2. `pip install -r requirements.txt`
3. Merge new keys from `.env.example` into `.env` **without** deleting secrets
4. `cd frontend` → `npm install` → `npm run build`
5. Restart backend (and `run_scheduler.py` if used)

ZIP updates: extract over the tree but **keep** `backend/.env` and do not overwrite MongoDB data files.

---

## 40. Security Recommendations

- Keep `.env` off Git, USB copies, and tickets.
- Rotate SMTP app passwords and WhatsApp tokens if leaked; restart after `.env` change.
- Restrict MongoDB and port 5000.
- Prefer per-device SSH over `SSH_DEFAULT_PASSWORD`.
- Change seeded passwords immediately.
- Do not enable `FLASK_DEBUG` on a shared network.
- Do not set `SSH_ALLOW_UNKNOWN_HOSTS` except local debug.
- Keep `SECRETS_ENCRYPTION_KEY` backed up with the database; losing it makes stored secrets unreadable (`migrate_encrypt_secrets.py` is for encryption migration, not casual use).

---

## 41. Final Deployment Checklist

```text
[ ] Prerequisites installed and verified
[ ] MongoDB running, not Internet-facing
[ ] .env complete; secrets unique
[ ] Frontend production build present
[ ] One scheduler in the environment
[ ] Health endpoint OK
[ ] Admin password changed
[ ] Firewall rules applied
[ ] Backup procedure tested once
[ ] ICMP/SSH/SNMP paths validated to a lab switch
```

---

## Known Project Facts

| Item | Current configuration (from this repository) |
|------|-----------------------------------------------|
| Backend | Flask 3.1.3 (`backend/requirements.txt`), entry `backend/app.py` |
| WSGI helpers | Waitress 3.0.2 (`start.bat`); Gunicorn 23.0.0 (`gunicorn.conf.py`) |
| Frontend | React 19 + Vite 8 + TypeScript (`frontend/package.json`) |
| Frontend build | `npm run build` → `frontend/dist/` |
| Dev UI | `npm run dev` → port **5173**, proxy to `127.0.0.1:5000` |
| Database | MongoDB via PyMongo 4.17.0 |
| Database name | `DATABASE_NAME` env (**no default**; example `NetworkMonitor`) |
| HTTP bind (`python app.py`) | `FLASK_RUN_HOST` default `0.0.0.0`, port **5000** |
| Gunicorn default bind | `127.0.0.1:5000` |
| Auth | JWT + bcrypt; roles `admin` / `user` |
| Ping interval | Settings `pingInterval`, seed `SCAN_INTERVAL=60` |
| Dispatcher tick | `MONITOR_DISPATCHER_INTERVAL_SECONDS` default **5** (clamp 1–15) |
| Ping runtime | `MONITOR_RUNTIME_MODE=dispatch` default |
| Interface discovery | `INTERFACE_SCAN_INTERVAL` default **3600** s |
| Interface statistics | `INTERFACE_STATS_INTERVAL` default **60** s |
| MAC/ARP poll | `MAC_ARP_POLL_INTERVAL` default **90** s |
| ARP active sweep | `ARP_ACTIVE_SWEEP_INTERVAL` default **1800** s |
| Nmap schedule | Unset → **0** (off); `.env.example` **3600** |
| Storm recovery job | **30** s |
| Retention job | Daily **03:15** |
| pingHistory TTL seed | **7** days |
| Telemetry TTL seed | **90** days |
| Incident retention seed | **365** days |
| Critical alert threshold | **3** consecutive failures |
| ISP | Separate `ispConnections`; up to 3 slots per site location |
| Topology | CDP/LLDP from SSH discovery; on-read graph |
| Git origin (this clone) | `https://github.com/mrzohaibahmed/NetPulse.git` |

---

## Related documents

| File | Role |
|------|------|
| `README.md` | Product overview and development setup |
| `USER_MANUAL.md` | Operator / feature reference |
| `backend/DEPLOYMENT.md` | Gunicorn / process roles |
| `backend/.env.example` | Full commented environment template |
| `backend/docs/WHATSAPP_ALERTS.md` | WhatsApp Cloud API setup |
| `frontend/README.md` | Frontend build vs Vite |

If a step in this guide disagrees with obsolete printouts, trust the source files listed above and the code paths they name.