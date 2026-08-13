# NetPulse Production Deployment

NetPulse uses **Flask + APScheduler + MongoDB** in-process. Only **one process per environment** should run APScheduler; Mongo scheduler leadership is the final authority when multiple processes exist, but duplicate schedulers waste resources and create noisy leader contention.

## Process models

### Option A — Recommended (API + dedicated scheduler)

1. **API workers** (no scheduler):

```bash
cd backend
export NETPULSE_ROLE=api
export NETPULSE_ENABLE_SCHEDULER=false
export CORS_ALLOWED_ORIGINS=https://netpulse.example.com
gunicorn -c gunicorn.conf.py "app:app"
```

2. **Scheduler process** (single instance):

```bash
cd backend
python run_scheduler.py
```

Use systemd/supervisor to keep exactly **one** `run_scheduler.py` alive.

### Option B — Single combined process

One Gunicorn worker owns API + scheduler:

```bash
cd backend
export GUNICORN_WORKERS=1
export NETPULSE_ENABLE_SCHEDULER=true
gunicorn -c gunicorn.conf.py "app:app"
```

**Never** run `GUNICORN_WORKERS>1` with schedulers enabled in every worker. The provided `gunicorn.conf.py` forces `NETPULSE_ROLE=api` when workers > 1.

## Environment variables (production)

| Variable | Purpose |
|----------|---------|
| `NETPULSE_ROLE` | `all` \| `api` \| `scheduler` |
| `NETPULSE_ENABLE_SCHEDULER` | `true` \| `false` \| `auto` |
| `CORS_ALLOWED_ORIGINS` | Comma-separated frontend origins (required in production) |
| `JWT_SECRET` | Strong random signing key |
| `SECRETS_ENCRYPTION_KEY` | Fernet key for SSH/SMTP secrets |
| `MONGO_MAX_POOL_SIZE` | Default 50 |
| `STORM_MITIGATION_BATCH_SIZE` | Auto-mitigation batch (default 5) |
| `MAX_GLOBAL_SSH_SESSIONS` | Collector SSH cap (default 10) |

## Health checks

| Endpoint | Use |
|----------|-----|
| `GET /health/live` | Liveness — process up |
| `GET /health/ready` | Readiness — Mongo + scheduler (if expected) |
| `GET /health` | Legacy alias (no raw errors) |
| `GET /api/dashboard/ops-metrics` | Admin operational snapshot |

## Startup logs

On boot each process logs: hostname, PID, role, environment, scheduler enabled/disabled.

## Development

```bash
export FLASK_DEBUG=true
export CORS_ALLOWED_ORIGINS=http://localhost:5173
python app.py
```

Scheduler starts in the reloader child only (not the watch parent).
