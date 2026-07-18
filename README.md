# 🖥️ Network Monitor (NetPulse)

A full-stack, enterprise-grade network monitoring system that continuously monitors LAN devices, captures deep hardware/service signatures using Nmap, fires email alerts on outages, and visualizes live latency telemetry in a React dashboard.

---

## 📖 Table of Contents

- [Features](#-features)
- [System Architecture](#-system-architecture)
- [How It Works (System Mechanics)](#-how-it-works-system-mechanics)
  - [1. Automatic Ping Monitoring](#1-automatic-ping-monitoring)
  - [2. Nmap Deep Scanning](#2-nmap-deep-scanning)
  - [3. Subnet sweeping (Discovery)](#3-subnet-sweeping-discovery)
  - [4. Alerting & Notifications](#4-alerting--notifications)
  - [5. Role-Based Access Control](#5-role-based-access-control)
- [Tech Stack](#-tech-stack)
- [Project Directory Structure](#-project-directory-structure)
- [MongoDB Collections Schema](#-mongodb-collections-schema)
- [Configuration (.env)](#-configuration-env)
- [API Route Reference](#-api-route-reference)
- [Development Setup](#-development-setup)
- [Running under Windows (Single EXE Mode)](#-running-under-windows-single-exe-mode)
- [Default Login Credentials](#-default-login-credentials)
- [Troubleshooting](#-troubleshooting)

---

## ✨ Features

- **📊 Live Telemetry Dashboard** — Monitor latency trends, status distribution, and active alerts dynamically with automatic frontend page-polling.
- **🛠️ Device Registry Management** — CRUD functionality, bulk CSV spreadsheet imports, and device-specific ping configurations (custom timeout, retries, interval).
- **⏱️ Bounded Background Scheduler** — Concurrent background jobs managing ICMP checks and Nmap profiles.
- **🔍 Subnet Sweeping & Discovery** — Auto-detect local networks, scan IP ranges, and dynamically register newly found online nodes.
- **🔒 Deep OS & Port Profiling** — Read operating systems, open ports, software products, version details, MAC addresses, and vendor labels using integrated Nmap.
- **✉️ Automated Outage Routing** — Generate persistent internal alert lists and forward email notifications to administrators when critical infrastructure fails.
- **👤 Multi-Role Authentication** — JWT validation supporting admin and read-only viewer accounts.
- **⚙️ Dynamic Reconfiguration** — Re-schedule ping loops and tweak SMTP setups on the fly directly from the UI without restarting python processes.

---

## 📐 System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       React Frontend                        │
│             Vite + TypeScript + Tailwind CSS                │
│       React Query Polling (10s/15s) ◄──► REST API          │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP with JWT Authorization
┌──────────────────────────────▼──────────────────────────────┐
│                   Flask Backend (app.py)                    │
│                                                             │
│  ┌───────────────────────┐       ┌───────────────────────┐  │
│  │     APScheduler       │       │    Flask Blueprints   │  │
│  │                       │       │  (Routes/Controllers) │  │
│  │  Job 1 (Ping loop):   │       │                       │  │
│  │  monitor_all_devices  │       │  • /api/auth          │  │
│  │  Interval: 30s        │       │  • /api/devices       │  │
│  │                       │       │  • /api/history       │  │
│  │  Job 2 (Nmap scan):   │       │  • /api/discovery     │  │
│  │  scan_all_online_devs │       │  • /api/alerts        │  │
│  │  Interval: 3600s      │       │  • /api/reports       │  │
│  └───────────┬───────────┘       └───────────┬───────────┘  │
└──────────────┼───────────────────────────────┼──────────────┘
               │                               │
               └───────────────┬───────────────┘
                               │ PyMongo Driver
┌──────────────────────────────▼──────────────────────────────┐
│                           MongoDB                           │
│                                                             │
│  Collections:                                               │
│  • devices (inventory & Nmap results)                       │
│  • pingHistory (raw time-series ping records)               │
│  • alerts (active/dismissed outages)                        │
│  • settings (global variables)                              │
│  • users (hashed authentication records)                     │
│  • auditLogs (administrative actions)                       │
└─────────────────────────────────────────────────────────────┘
```

---

## ⚙️ How It Works (System Mechanics)

### 1. Automatic Ping Monitoring
- **Runner**: Powered by `APScheduler` inside `backend/scheduler.py`.
- **Methodology**:
  1. The background scheduler initiates the `device_monitor_job` trigger.
  2. The service queries all records in MongoDB where `monitor = true`.
  3. If a device has an overridden `pingInterval` in database, the system verifies whether the duration since its `lastCheckedAt` exceeds the custom threshold.
  4. The system executes a raw ICMP echo request using the `ping3` library.
  5. The result state is evaluated and written:
     - **Success**: Status becomes `Online`. `consecutiveFailures` is reset to `0`. `lastSeen` is set to the current timestamp.
     - **Outage (Critical)**: If the host fails ICMP checks and `critical = true`, the status switches to `Offline (Critical)`.
     - **Outage (Non-Critical)**: If the host fails ICMP checks and `critical = false`, the status switches to `Not Reachable`.
  6. Every execution stores an item in the `pingHistory` collection.

---

### 2. Nmap Deep Scanning
- **Runner**: Concurrent execution managed in `backend/services/nmap_service.py` via python-nmap.
- **Methodology**:
  1. An independent scheduling job executes at `NMAP_SCAN_INTERVAL` (default: 3600 seconds / 1 hour).
  2. It queries MongoDB for devices currently marked `Online`. Offline targets are skipped to prevent execution timeouts.
  3. The service instantiates a bounded thread pool with a worker thread limit controlled by `MAX_SCAN_THREADS` (default: 5).
  4. It calls the Nmap binary via subprocess with the configured variables (default: `-A -T4` for OS mapping, default script scans, service version detection, and timing acceleration).
  5. The raw results are mapped to the device's `networkInfo` sub-document:
     - `os`: Name, family, generation, and detection confidence level.
     - `ports`: Active ports, matching protocols (`tcp`/`udp`), states (`open`/`filtered`), service identifiers, software products, and version strings.
     - `services`: Deduplicated string arrays representing running services (e.g. `["http", "ssh", "rdp"]`).
     - `macAddress` & `vendor`: Populated when scanning local Layer-2 segments.
  6. The backend updates the device document in MongoDB, which is rendered dynamically in the **Network Info** tab inside the **Device Details** drawer.

---

### 3. Subnet sweeping (Discovery)
- **Runner**: Multithreaded address scanning in `backend/services/discovery_service.py`.
- **Methodology**:
  1. The backend automatically determines the local network configuration by executing a UDP socket check to `8.8.8.8` and calculating the `/24` subnet boundaries.
  2. The user initiates a range sweep (limited to 1024 hosts per execution to safeguard resources).
  3. A thread pool of 20 workers simultaneously pings every host in the range.
  4. For each responsive target, the discovery module attempts a reverse-DNS hostname lookup (`socket.gethostbyaddr`).
  5. **Auto-Save Functionality**: If the host is not already present in the database, NetPulse inserts it with `deviceType = "Unknown"`, `monitor = true`, and status `Online`, making it instantly part of the active polling routine.

---

### 4. Alerting & Notifications
- **Runner**: Spawned background alerting in `backend/services/alert_service.py`.
- **Methodology**:
  1. After every automatic check, `monitor_service.py` calls the alert verification hook.
  2. If the target's status transitions from `Online` to `Offline (Critical)`, an outage alert record is generated.
  3. If SMTP alerts are enabled, the service spawns a separate background thread to configure an email message.
  4. It connects to the defined server configuration (`SMTP_HOST`, `SMTP_PORT`, with optional TLS/SSL protection) and routes an warning email to the target destination.

---

### 5. Role-Based Access Control
- **Viewer Role**: Read-only permission limits access to dashboard statistics, uptime tables, filterable logs, and network details.
- **Admin Role**: Read-write permission grants full access to add, edit, or delete monitored devices, scan configurations, range discovery sweeps, global SMTP parameters, and custom user account generation.

---

## 🛠️ Tech Stack

- **Frontend**: React 19, TypeScript 6, Vite 8, Recharts, Tailwind CSS 4, TanStack Query, Radix UI.
- **Backend**: Flask 3.1, APScheduler 3.11, PyMongo 4.17.
- **Libraries**: `ping3` (ICMP execution), `python-nmap` (nmap automation), `PyJWT` (authorization tokens), `bcrypt` (password hashing), `openpyxl` (spreadsheet reporting).

---

## 📁 Project Directory Structure

```
Network Monitor/
├── README.md               # Main architecture & mechanics documentation
├── backend/
│   ├── app.py              # Flask server and blueprint routes registration
│   ├── scheduler.py        # Background task scheduler (Ping & Nmap loops)
│   ├── requirements.txt    # Python requirements
│   ├── config/
│   │   ├── database.py     # MongoDB connection config & env parser
│   │   └── email.py        # Mail server environment maps
│   ├── models/             # PyMongo template structures (devices, history)
│   ├── routes/             # REST blueprints (auth, device CRUD, scans, alerts)
│   ├── services/           # Underlying logic (ICMP ping, Nmap scans, discovery)
│   └── utils/              # Security helpers, serialization, paging, logging
└── frontend/
    ├── src/
    │   ├── api/            # API queries layer (client, endpoints)
    │   ├── components/     # UI widgets, layout tables, DeviceDrawer
    │   ├── hooks/          # React Query hook abstractions
    │   ├── pages/          # Layout views (Dashboard, Devices, Discovery)
    │   └── types/          # Shared TypeScript contracts (Device, NetworkInfo)
    ├── package.json        # Frontend manifest & scripts
    └── vite.config.ts      # Vite server configuration
```

---

## 🗄️ MongoDB Collections Schema

- **`devices`**: Stores active host inventories, status metrics, overrides, and Nmap metadata arrays.
- **`pingHistory`**: Time-series log containing result parameters of every scheduled and manually triggered check.
- **`alerts`**: Tracked system outages showing target identifiers, timestamps, acknowledge, and dismiss statuses.
- **`settings`**: Key-value pair configuration for mail servers, global timeout thresholds, and scan speed parameters.
- **`users`**: Secure account store storing password details encrypted using `bcrypt`.
- **`auditLogs`**: Immutable log recording administrative actions (device additions, settings updates, imports).

---

## 🔌 Configuration (.env)

Modify configuration by updating `backend/.env`:

```env
# Database Settings
MONGO_URI=mongodb://localhost:27017
DATABASE_NAME=NetworkMonitor

# Polling Controls
SCAN_INTERVAL=30
PING_TIMEOUT_MS=1000
PING_RETRIES=3

# JWT Configuration
JWT_SECRET=network-monitor-production-secret-key
JWT_EXPIRE_HOURS=8

# SMTP Email Parameters
ALERT_EMAIL_ENABLED=true
ALERT_EMAIL_TO=recipient@example.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=sender@gmail.com
SMTP_PASSWORD=your-secure-app-password
SMTP_FROM=sender@gmail.com
SMTP_USE_TLS=true

# Nmap Profiling Parameters
NMAP_SCAN_INTERVAL=3600
NMAP_ARGUMENTS=-A -T4
MAX_SCAN_THREADS=5
NMAP_TIMEOUT=300
NMAP_PATH=
```

---

## 🗺️ API Route Reference

| Method | Route | Description | Auth Role |
|---|---|---|---|
| **POST** | `/api/auth/login` | Yields JWT authorization token | Public |
| **GET** | `/api/auth/me` | Fetches active identity profile | Any |
| **PUT** | `/api/auth/account` | Alters password or username variables | Any |
| **POST** | `/api/devices` | Inserts a monitored host record | Admin |
| **GET** | `/api/devices` | Fetches paginated, filterable host inventories | Any |
| **PUT** | `/api/devices/<id>` | Alters host parameters and overrides | Admin |
| **DELETE** | `/api/devices/<id>` | Drops device and purges database history | Admin |
| **POST** | `/api/devices/import` | Processes device registry CSV uploads | Admin |
| **POST** | `/api/devices/<id>/scan` | Triggers immediate ICMP check | Any |
| **POST** | `/api/devices/<id>/scan-details`| Runs Nmap OS, version, and service scan | Any |
| **POST** | `/api/devices/scan-all-details` | Scans all online devices with Nmap concurrently | Any |
| **GET** | `/api/history` | Fetches system-wide ping history records | Any |
| **GET** | `/api/devices/<id>/history` | Fetches device history, uptime report & Nmap scans | Any |
| **GET** | `/api/discovery/network-hint`| Inspects socket context to suggest CIDR sweep | Any |
| **POST** | `/api/discovery/scan-range`| Launches thread pool subnet discovery sweep | Admin |
| **GET** | `/api/alerts` | Fetches alert inventory log | Any |
| **POST** | `/api/alerts/<id>/acknowledge`| Marks target outage recognized | Any |
| **POST** | `/api/alerts/<id>/dismiss` | Dismisses outage records | Any |
| **GET** | `/api/settings` | Fetches mail settings and thresholds | Any |
| **PUT** | `/api/settings` | Updates global configuration dynamically | Admin |
| **GET** | `/api/reports/uptime` | Compiles device uptime percentages | Any |
| **GET** | `/api/reports/export/devices` | Yields Excel/CSV device manifest download | Any |
| **GET** | `/api/reports/export/history` | Yields Excel/CSV ping logs download | Any |

---

## 🚀 Development Setup

### 1. Initialize MongoDB
Ensure you have a MongoDB instance running locally on port `27017` or have a valid MongoDB Atlas connection string.

### 2. Configure Backend
```powershell
cd backend
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
# Create and edit your .env file in the backend/ directory
python app.py
```
*API server runs at `http://127.0.0.1:5000`*

### 3. Configure Frontend
Open a new shell:
```powershell
cd frontend
npm install
npm run dev
```
*Vite UI server runs at `http://127.0.0.1:5173` (proxies `/api` routes to backend)*

---

## 🔑 Default Login Credentials

Upon the database creation, NetPulse pre-seeds the following system roles:

| Username | Password | Role | Description |
|---|---|---|---|
| **admin** | `admin123` | `admin` | Full read/write capability, settings access |
| **viewer** | `viewer123` | `viewer` | Read-only access to dashboard statistics |

*Note: Update password credentials immediately inside **Account Settings** or the environment parameters when deploying.*

---

## 🔍 Troubleshooting

| Issue | Cause | Resolution |
|---|---|---|
| **ICMP Ping Failure** | Missing socket privileges | Run your terminal instance (or VS Code) **as Administrator** on Windows. |
| **Nmap Scan Failures** | Admin privilege missing | Nmap aggressive scanning (`-A` or `-O`) requires elevated system permissions. Run your terminal as Administrator, or downgrade `NMAP_ARGUMENTS` in `.env` to `-sV -T4` to omit raw OS detection tests. |
| **UI changes not showing** | Running on Flask port | Run `npm run build` in the `frontend` folder to update the static build served by Flask. |
| **MongoDB Errors** | Authentication/Connection failure | Validate the `MONGO_URI` connection string and credentials inside `backend/.env`. |
