# NetPulse Production Deployment

NetPulse uses **Flask + APScheduler + MongoDB** in-process. Only **one process per environment** should run APScheduler; Mongo scheduler leadership is the final authority when multiple processes exist, but duplicate schedulers waste resources and create noisy leader contention.

## Recommended production topology

```
Internet / LAN clients
        ↓
HTTPS reverse proxy (IIS / nginx / Caddy)
        ↓
127.0.0.1:5000  (Gunicorn / WSGI)
        ↓
MongoDB 127.0.0.1:27017 (authenticated, not Internet-facing)
```

Default Gunicorn bind is **`127.0.0.1:5000`**. Do not expose Flask/Gunicorn directly on `0.0.0.0` without TLS termination in front.

## Process models

### Option A — Recommended (API + dedicated scheduler)

1. **API workers** (no scheduler):

```bash
cd backend
export NETPULSE_ROLE=api
export NETPULSE_ENABLE_SCHEDULER=false
export NETPULSE_ENV=production
export CORS_ALLOWED_ORIGINS=https://netpulse.example.com
export GUNICORN_BIND=127.0.0.1:5000
gunicorn -c gunicorn.conf.py "app:app"
```

2. **Scheduler process** (single instance):

```bash
cd backend
export NETPULSE_ENV=production
python run_scheduler.py
```

Use systemd/supervisor to keep exactly **one** `run_scheduler.py` alive.

### Option B — Single combined process

One Gunicorn worker owns API + scheduler:

```bash
cd backend
export GUNICORN_WORKERS=1
export GUNICORN_BIND=127.0.0.1:5000
export NETPULSE_ENABLE_SCHEDULER=true
export NETPULSE_ENV=production
export CORS_ALLOWED_ORIGINS=https://netpulse.example.com
gunicorn -c gunicorn.conf.py "app:app"
```

**Never** run `GUNICORN_WORKERS>1` with schedulers enabled in every worker. The provided `gunicorn.conf.py` forces `NETPULSE_ROLE=api` when workers > 1.

## Environment variables (production)

| Variable | Purpose |
|----------|---------|
| `NETPULSE_ENV` | Must be `production` (unset + `FLASK_DEBUG=false` also defaults to production) |
| `NETPULSE_ROLE` | `all` \| `api` \| `scheduler` |
| `NETPULSE_ENABLE_SCHEDULER` | `true` \| `false` \| `auto` |
| `CORS_ALLOWED_ORIGINS` | Comma-separated frontend origins (**required** in production) |
| `JWT_SECRET` | Strong unique secret (≥32 chars, not a placeholder) |
| `SECRETS_ENCRYPTION_KEY` | Fernet key for SSH/SMTP/SNMP secrets |
| `DEFAULT_*_PASSWORD` | Strong bootstrap passwords only for empty DB; never well-known defaults |
| `SSH_KNOWN_HOSTS_FILE` | Path to known_hosts for SSH host-key verification |
| `GUNICORN_BIND` | Default `127.0.0.1:5000` |
| `MAX_CONTENT_LENGTH` | Request body cap (default 2 MiB) |
| `MONGO_MAX_POOL_SIZE` | Default 50 |
| `STORM_MITIGATION_BATCH_SIZE` | Auto-mitigation batch (default 5) |
| `MAX_GLOBAL_SSH_SESSIONS` | Collector SSH cap (default 10) |
| `STORM_SAFETY_FAIL_OPEN_MISSING_HEALTH` | Default **false** (fail-closed) |

## Health checks

| Endpoint | Use |
|----------|-----|
| `GET /health/live` | Liveness — minimal (`status`, `timestamp`) |
| `GET /health/ready` | Readiness — Mongo (+ scheduler check when expected); no hostname/pid/owner |
| `GET /health` | Legacy alias |
| `GET /api/dashboard/ops-metrics` | Admin operational snapshot |

## MongoDB (operator checklist)

- Enable authentication; use a least-privilege `NetworkMonitor` user (not root).
- Bind to `127.0.0.1` (or private network + TLS if remote).
- Block port `27017` from the Internet via Windows Firewall.
- Keep NTP correct for TTL indexes.

## Startup logs

On boot each process logs: hostname, PID, role, environment, scheduler enabled/disabled (no secrets).

## Development

```bash
export FLASK_DEBUG=true
export CORS_ALLOWED_ORIGINS=http://localhost:5173
python app.py
```

Scheduler starts in the reloader child only (not the watch parent). Local debug may use `SSH_ALLOW_UNKNOWN_HOSTS=true` only with `FLASK_DEBUG=true`.
