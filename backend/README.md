# Network Monitor — Backend API

Flask REST API for monitoring network devices. Stores devices in MongoDB, pings them manually or on a schedule, keeps ping history, discovers devices on an IP range, sends alerts, and exposes dashboard/report data for the React frontend.

When `frontend/dist` exists, Flask also serves the built React UI at `/`.

## Features

- **Authentication** — JWT login, admin/viewer roles, account management
- **Device CRUD** — create, list, update, delete monitored devices
- **CSV import** — bulk import devices from a spreadsheet
- **Manual scan** — ping a single device and update its status
- **Automatic monitoring** — background scheduler pings devices with `monitor: true`
- **Ping history** — every scan (manual or automatic) stored in MongoDB
- **Network discovery** — scan an IP range, detect online hosts, auto-save new devices
- **Dashboard APIs** — summaries, statistics, and chart data
- **Reports** — uptime reports and CSV/Excel export
- **Alerts** — in-app alert list with acknowledge/dismiss; email for critical offline events
- **Settings** — global ping interval, timeout, retries, and SMTP config (persisted in MongoDB)
- **Audit logging** — administrative actions recorded
- **Frontend hosting** — serves `frontend/dist` when present

## Tech Stack

| Layer | Technology |
|-------|------------|
| API | Flask, Flask-CORS |
| Database | MongoDB (PyMongo) |
| Auth | PyJWT, bcrypt |
| Scheduling | APScheduler |
| Ping | ping3 |
| Export | openpyxl |
| Config | python-dotenv |

## Project Structure

```
backend/
├── app.py                  # Flask entry point (+ frontend static serving)
├── scheduler.py            # Background monitoring scheduler
├── requirements.txt
├── .env                    # Environment variables (not committed)
├── config/
│   ├── database.py         # MongoDB connection
│   └── email.py            # Email env defaults
├── models/
│   ├── device.py
│   └── ping_history.py
├── routes/
│   ├── auth_routes.py      # Login, users, account
│   ├── device_routes.py    # Device CRUD + CSV import
│   ├── scan_routes.py      # Manual device scan
│   ├── history_routes.py   # Ping history
│   ├── dashboard_routes.py # Dashboard & charts
│   ├── discovery_routes.py # IP range discovery
│   ├── alert_routes.py     # Alert list / acknowledge / dismiss
│   ├── settings_routes.py  # Global settings
│   └── report_routes.py    # Uptime reports & export
├── services/
│   ├── ping_service.py
│   ├── monitor_service.py
│   ├── history_service.py
│   ├── discovery_service.py
│   ├── alert_service.py
│   ├── email_service.py
│   ├── settings_service.py
│   ├── user_service.py
│   └── audit_service.py
├── utils/
│   ├── auth.py             # JWT + password helpers
│   ├── serializers.py
│   ├── pagination.py
│   └── monitor_logger.py
└── logs/
    └── monitor.log
```

## Setup

### 1. Prerequisites

- Python 3.10+
- MongoDB (local or Atlas)
- Network access for ICMP ping (may require admin privileges on Windows)

### 2. Virtual environment

```powershell
cd backend
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Environment variables

Create `backend/.env`:

```env
MONGO_URI=mongodb://localhost:27017
DATABASE_NAME=NetworkMonitor
SCAN_INTERVAL=30
FLASK_DEBUG=true

# Auth
JWT_SECRET=change-me-in-production
JWT_EXPIRE_HOURS=8
DEFAULT_ADMIN_USER=admin
DEFAULT_ADMIN_PASSWORD=admin123

# Email alerts (optional — can also be configured in Settings UI)
ALERT_EMAIL_TO=your-email@example.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@example.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=your-email@example.com
SMTP_USE_TLS=true
ALERT_EMAIL_ENABLED=true
```

| Variable | Description | Default |
|----------|-------------|---------|
| `MONGO_URI` | MongoDB connection string | required |
| `DATABASE_NAME` | Database name | required |
| `SCAN_INTERVAL` | Default auto-monitor interval (seconds) | `30` |
| `FLASK_DEBUG` | Flask debug/reloader | `true` |
| `JWT_SECRET` | Token signing secret | dev default |
| `JWT_EXPIRE_HOURS` | Token lifetime | `8` |
| `DEFAULT_ADMIN_USER` | First admin username | `admin` |
| `DEFAULT_ADMIN_PASSWORD` | First admin password | `admin123` |
| `ALERT_EMAIL_TO` | Alert recipient | — |
| `SMTP_*` | SMTP settings | see `.env.example` |

### 4. Run the server

```powershell
cd backend
.\venv\Scripts\activate
python app.py
```

- API: `http://127.0.0.1:5000`
- With built frontend: UI also at `http://127.0.0.1:5000`

On startup the scheduler pings all devices where `monitor` is `true`. The interval comes from Settings (default: `SCAN_INTERVAL`).

## Authentication

All `/api/*` routes except login require a Bearer token.

```http
POST /api/auth/login
Content-Type: application/json

{
  "username": "admin",
  "password": "admin123"
}
```

Use the returned token:

```http
Authorization: Bearer <token>
```

| Role | Access |
|------|--------|
| `admin` | Full access — devices, discovery, settings, user management |
| `viewer` | Read-only access to dashboard, devices, history, reports |

Default users are created on first run if the `users` collection is empty.

## Email Alerts

When a **critical** device transitions to **Offline (Critical)**, the monitor:

1. Creates an in-app alert
2. Sends an email (if SMTP is configured)

Alerts fire once per outage transition, not on every failed ping while the device stays offline.

SMTP can be configured in `backend/.env` or updated at runtime via **Settings** in the UI.

## API Reference

All API routes are prefixed with `/api` unless noted.

### Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Frontend UI (if built) or API status JSON |
| GET | `/health` | Server and database health |

### Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/login` | Login |
| GET | `/api/auth/me` | Current user |
| PUT | `/api/auth/account` | Update own username/password |
| GET | `/api/users` | List users (admin) |
| PUT | `/api/users/<id>` | Update user (admin) |

### Devices

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/devices` | Create device (admin) |
| GET | `/api/devices` | List devices (paginated, filterable) |
| GET | `/api/devices/<id>` | Get one device |
| PUT | `/api/devices/<id>` | Update device (admin) |
| DELETE | `/api/devices/<id>` | Delete device (admin) |
| POST | `/api/devices/import` | CSV import (admin) |

**Device fields:**

| Field | Type | Description |
|-------|------|-------------|
| `hostname` | string | Device name |
| `ipAddress` | string | IPv4 address (unique) |
| `deviceType` | string | Device category |
| `critical` | boolean | Mark as critical |
| `monitor` | boolean | Include in automatic monitoring |
| `status` | string | `Online`, `Not Reachable`, `Offline (Critical)`, `Unknown` |
| `responseTime` | number \| null | Last ping time in ms |
| `lastSeen` | datetime \| null | Last successful response |
| `pingInterval` | number \| null | Per-device override (seconds) |
| `pingTimeoutMs` | number \| null | Per-device timeout override |
| `pingRetries` | number \| null | Per-device retry override |

### Scanning

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/devices/<id>/scan` | Manually ping a device |

### History

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/history` | Ping history (paginated, filterable) |
| GET | `/api/devices/<id>/history` | Device history + uptime + trend |

Filters: `status`, `scanType`, `deviceId`, `deviceType`, `startDate`, `endDate`, `q`.

### Discovery

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/discovery/network-hint` | Suggest local scan range |
| POST | `/api/discovery/scan-range` | Scan IP range (admin) |

### Dashboard

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/dashboard/summary` | Device counts and percentages |
| GET | `/api/dashboard/recent-history` | Last 20 ping records |
| GET | `/api/dashboard/device-status` | All devices with status |
| GET | `/api/dashboard/statistics` | Scan stats and averages |
| GET | `/api/dashboard/charts/device-status` | Status chart data |
| GET | `/api/dashboard/charts/device-type` | Device type chart |
| GET | `/api/dashboard/charts/response-time` | Avg response time chart |
| GET | `/api/dashboard/charts/scan-activity` | Daily scan activity |

### Alerts

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/alerts` | List alerts |
| POST | `/api/alerts/<id>/acknowledge` | Acknowledge alert |
| POST | `/api/alerts/<id>/dismiss` | Dismiss alert |

### Settings

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/settings` | Get settings |
| PUT | `/api/settings` | Update settings (admin) |

Updating `pingInterval` reschedules the monitor job without restarting the server.

### Reports

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/reports/uptime` | Uptime report per device |
| GET | `/api/reports/export/devices` | Export devices (CSV/XLSX) |
| GET | `/api/reports/export/history` | Export history (CSV/XLSX) |

## MongoDB Collections

| Collection | Purpose |
|------------|---------|
| `devices` | Monitored network devices |
| `pingHistory` | Every ping result |
| `alerts` | Critical offline alerts |
| `settings` | Global app settings (ping + SMTP) |
| `users` | Login accounts |
| `auditLogs` | Admin action audit trail |

## Automatic Monitoring

`scheduler.py` uses APScheduler to call `monitor_all_devices()` on an interval from Settings.

For each device with `monitor: true`:

1. Check if per-device interval allows a scan
2. Ping the device
3. Update status, response time, last seen, consecutive failures
4. Save ping history with `scanType: "Automatic"`
5. Send alert if a critical device goes offline

Each device scan is wrapped in its own `try/except` so one failure does not stop the cycle.

## Logging

Logs are written to `logs/monitor.log` and the console.

```powershell
Get-Content logs\monitor.log -Wait
```

## Development Notes

- Run commands from `backend/` so Python imports resolve correctly.
- Flask debug mode uses a reloader; the scheduler only starts in the correct process to avoid duplicate jobs.
- Set `FLASK_DEBUG=false` in production.
- ICMP ping may require elevated permissions on Windows.
- Build the frontend (`npm run build` in `frontend/`) to serve the UI from Flask at `/`.

## Example Workflow

1. Start the API: `python app.py`
2. Log in: `POST /api/auth/login`
3. Create a device: `POST /api/devices`
4. Scan it: `POST /api/devices/<id>/scan`
5. Check history: `GET /api/history`
6. View dashboard: `GET /api/dashboard/summary`
7. Discover devices: `POST /api/discovery/scan-range`
8. Export report: `GET /api/reports/export/devices?format=csv`
