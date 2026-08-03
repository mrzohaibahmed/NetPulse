# NetPulse — Project Presentation Report

**LAN Monitoring & Switch Storm-Protection Platform**  
**Date:** July 31, 2026  
**Audience:** Technical / operations / academic presentation

---

## Slide 1 — Title

**NetPulse**  
Full-stack network monitoring and broadcast-storm protection for enterprise LANs

*Continuously answers: Is it up? What is it? What ports exist? Is a storm forming? Did something critical fail?*

---

## Slide 2 — The Problem

| Challenge | Why it hurts |
|-----------|----------------|
| Device outages go unnoticed | Critical hosts fail without timely alerts |
| Manual inventory is stale | Switches change; operators lack live port context |
| Broadcast storms escalate fast | L2 storms can collapse segments before humans react |
| Auto-shutdown is dangerous | Blind automation can take down uplinks / trunks |
| Tools are fragmented | Ping, Nmap, SSH, SNMP, and alerting rarely live in one NOC UI |

**Core insight:** Monitoring alone is not enough. Storm mitigation needs **eligibility, confirmation, and safety** before any port is shut.

---

## Slide 3 — The Solution

**NetPulse** unifies five capabilities in one platform:

1. **Reachability** — Scheduled ICMP ping with critical/non-critical status
2. **Profiling** — Nmap OS, ports, services, MAC, vendor
3. **Discovery** — Subnet sweep + SSH switch interface inventory
4. **Storm protection** — Multi-stage gated pipeline ending in safe shutdown / recovery
5. **Alerting & reports** — In-app alerts, optional SMTP, uptime exports

**Human-in-the-loop by default:** Mitigation mode is `manual` unless an admin enables `automatic`.

---

## Slide 4 — Who Uses It

| Role | What they can do |
|------|------------------|
| **Super-admin** | Full control + manage other super-admins |
| **Admin** | Devices, discovery, settings, users, storm mitigate/recover |
| **Operator** | View + on-demand Nmap, alert ack, selected storm actions |
| **Viewer** | Read-only dashboards, devices, interfaces, history, reports |

Default seeded accounts (empty DB): `admin` / `admin123`, `viewer` / `viewer123`.

---

## Slide 5 — System Architecture

```
┌──────────────┐     JWT REST      ┌─────────────────┐     PyMongo     ┌──────────┐
│ React UI     │ ◄───────────────► │ Flask (app.py)  │ ◄─────────────► │ MongoDB  │
│ (Vite/TS)    │   poll 10–20s     │ + APScheduler   │                 │          │
└──────────────┘                   └────────┬────────┘                 └──────────┘
                                            │
          ┌─────────────┬───────────────────┼───────────────────┬──────────────┐
          ▼             ▼                   ▼                   ▼              ▼
     ICMP ping     Nmap profiling    SSH iface discovery   SNMP/SSH stats   SMTP email
     (~30s)        (~1 hour)         (~1 hour)             (~60s → storm)   critical
```

**Design choices**
- No WebSockets — live UI via TanStack Query polling
- Background work via APScheduler inside Flask
- Optional single-process deploy: Flask serves `frontend/dist`

---

## Slide 6 — Tech Stack

| Layer | Stack |
|-------|--------|
| **Frontend** | React 19, TypeScript, Vite 8, Tailwind CSS 4, TanStack Query/Table, Recharts, Radix UI, Framer Motion, Zod |
| **Backend** | Flask 3.1, APScheduler, PyMongo, PyJWT, bcrypt |
| **Monitoring** | ping3 (ICMP), python-nmap, Paramiko (SSH), pysnmp (IF-MIB) |
| **Data** | MongoDB (local or Atlas) |
| **Security** | JWT auth, bcrypt passwords, Fernet-encrypted secrets at rest |
| **Export** | CSV / Excel (openpyxl) |

---

## Slide 7 — Project Structure

```
NetPulse/
├── backend/
│   ├── app.py                 # Flask entry, indexes, SPA hosting
│   ├── scheduler.py           # Background jobs + storm chain
│   ├── routes/                # REST API blueprints
│   ├── services/
│   │   ├── interface_collection/   # SSH discovery + SNMP/SSH stats
│   │   └── storm/                  # Eligibility → risk → mitigate → recover
│   ├── models/, config/, utils/, tests/
└── frontend/
    └── src/
        ├── pages/             # Dashboard, Devices, Storm, …
        ├── api/, auth/, components/, hooks/
```

---

## Slide 8 — Continuous Monitoring Jobs

| Job | Interval (typical) | Purpose |
|-----|--------------------|---------|
| Device monitor | ~30s | ICMP ping → status + history + alerts |
| Nmap scan | ~1 hour | OS / ports / services on Online devices |
| Interface discovery | ~1 hour | SSH inventory, VLANs, neighbors, port flags |
| Interface stats | ~60s | Counters → **full storm pipeline** |
| Storm recovery | ~30s | Auto-recovery, re-mitigation, stabilization |
| Data retention | Daily 03:15 | TTL indexes + purge closed incidents |

---

## Slide 9 — Feature Map (UI)

| Page | Value for operators |
|------|---------------------|
| **Dashboard** | KPIs, health gauge, status charts, response-time trends, recent alerts |
| **Devices** | Inventory CRUD, CSV import, ping/Nmap, per-device overrides |
| **Interfaces** | Switch ports, discovery/stats, monitoring modes, manual shutdown/recover |
| **Storm Protection** | Full pipeline panels: eligibility → recovery |
| **Discovery** | Suggest `/24`, sweep range, auto-register hosts |
| **History** | Ping history & uptime |
| **Alerts** | Acknowledge / dismiss critical outages |
| **Reports** | Uptime reports; CSV/XLSX export |
| **Settings** | Ping, SMTP, mitigation mode, recovery, retention |
| **Account** | Profile & user administration |

---

## Slide 10 — Storm Protection Pipeline (Core Differentiator)

```
Interface Stats
      ↓
Eligibility          Access only; deny uplink / trunk / mgmt / protected
      ↓
Risk Score           Weighted multi-signal score
      ↓
Confirmation         N consecutive high-risk samples (e.g. 4 @ ≥75)
      ↓
Safety Engine        14 rules (online, SSH, cooldown, locks, health, …)
      ↓
Orchestrator Prepare Live CONFIRMED + fresh SAFE → incident
      ↓
Mitigation           SSH SHUTDOWN (manual or automatic)
      ↓
Recovery             Policy + Recovery Safety (R0–R8)
      ↓
Post-recovery reset  Confirmation reset, safety invalidate, orphan cancel
      ↓
MONITORING → RESOLVED  or  re-mitigate on *fresh* post-recovery evidence
```

---

## Slide 11 — Risk Scoring Signals

Weighted analyzers feed a single risk score (weights tunable via env):

| Signal | Typical weight | Meaning |
|--------|----------------|---------|
| Broadcast rate | ~35% | Classic storm indicator |
| Multicast | ~15% | Abnormal multicast flood |
| Unknown unicast | ~15% | Suspicious UUC |
| Utilization | configurable | Link saturation |
| Errors / discards / CRC | configurable | Physical / protocol distress |

**Confirmation gate:** High risk must persist across consecutive samples before mitigation is even prepared — reduces false positives.

---

## Slide 12 — Safety-First Automation

**Mitigation Safety (examples of 14 rules)**
- Storm must be CONFIRMED
- Device must be Online
- SSH must be available
- Port not already shut / locked
- Cooldown and max-attempt limits
- Host / switch health checks

**Recovery Safety (R0–R8)**  
Separate engine validates that bringing a port back is safe.

**Locks with TTL leases**  
Prevents concurrent conflicting mitigate/recover actions.

**Default mode = manual**  
Pipeline stops at `READY_FOR_MITIGATION` until an admin acts — automatic shutdown is an explicit opt-in.

---

## Slide 13 — Post-Recovery Anti-Loop Design

After successful recovery verification:

1. Set `recoveredAt`
2. Reset confirmation state
3. Invalidate stale safety results
4. Cancel orphan READY incidents
5. Enter MONITORING with a stabilization window
6. Re-mitigate **only** if a *new* storm appears after `recoveredAt`

**Why it matters:** Without this, a recovering port can flap forever between shut and restore.

---

## Slide 14 — Interface Intelligence

Layered pipeline in `backend/services/interface_collection/`:

**SSH collector → parser → normalizer → classifier → MongoDB**

Port classification flags used by storm logic:
- Access / trunk / uplink
- Infrastructure / management / protected

Enrichment:
- CDP / LLDP neighbors
- Device-type detection
- Stats via SNMP (preferred) or SSH fallback
- Utilization from counter deltas

Storm modules consume **classified inventory**, not raw CLI — cleaner and safer decisions.

---

## Slide 15 — Data Model (MongoDB)

| Domain | Collections (examples) |
|--------|-------------------------|
| Monitoring | `devices`, `pingHistory`, `alerts` |
| Access | `users`, `auditLogs`, `settings` |
| Interfaces | `interfaces`, `interface_stats` |
| Storm | `eligibility_results`, `storm_risk_history`, `storm_confirmation_history`, `storm_safety_history`, `storm_incidents`, `storm_mitigation_history`, `storm_recovery_history` |
| Concurrency | Mitigation / recovery lock collections (TTL) |

Storm history is **append-only** — forensic-friendly without pipeline versioning counters.

---

## Slide 16 — API Surface

All business APIs under `/api` (plus `/health`):

| Blueprint | Responsibility |
|-----------|----------------|
| Auth | Login, JWT, account |
| Devices | Inventory CRUD, ping overrides |
| Nmap / Scan | Profiling jobs |
| Discovery | Subnet sweep |
| Interfaces | Discovery, stats, manual shut/recover |
| Storm | Eligibility, risk, confirmation, safety, incidents, mitigate, recover |
| Dashboard | Aggregated KPIs |
| History / Reports | Uptime & exports |
| Alerts / Settings | Notifications & system config |

---

## Slide 17 — Security & Operations

| Concern | Approach |
|---------|----------|
| Authentication | JWT with configurable expiry |
| Passwords | bcrypt |
| Secrets at rest | Fernet (`SECRETS_ENCRYPTION_KEY`) |
| Authorization | Role hierarchy enforced on routes |
| Auditability | `auditLogs` + storm history trails |
| Retention | Daily job + TTL indexes |
| Logging | `backend/logs/monitor.log` |

---

## Slide 18 — Deployment & Prerequisites

**Prerequisites**
- Python 3.10+
- Node.js 18+
- MongoDB (local or Atlas)
- Nmap on PATH
- SSH reachability to switches
- Windows: Admin often needed for ICMP / aggressive Nmap

**Development**
```text
Backend:  python app.py          → :5000
Frontend: npm run dev            → :5173  (proxies /api → backend)
```

**Production-style**
```text
npm run build  →  Flask serves frontend/dist
```

Config via `backend/.env` (Mongo URI, JWT, Fernet key, SMTP, storm thresholds, retention).

---

## Slide 19 — Demo Walkthrough (Suggested)

1. **Login** as admin → open **Dashboard** (live KPIs)
2. **Devices** → show Online / Critical statuses and ping history
3. **Discovery** → sweep a `/24`, auto-register a host
4. **Interfaces** → run discovery/stats on a switch; show access vs uplink flags
5. **Storm Protection** → walk panels left-to-right:
   - Eligibility → Risk → Confirmation → Safety → Incidents → Mitigation → Recovery
6. **Settings** → show `mitigationMode`: manual vs automatic
7. **Alerts / Reports** → critical offline + CSV/XLSX export

---

## Slide 20 — Unique Selling Points

1. **Storm protection as a first-class product**, not a bolt-on script
2. **Safety-gated automation** — eligibility + confirmation + 14 safety rules + recovery safety
3. **Manual or automatic mitigation** — operators choose risk tolerance
4. **Anti-flap post-recovery design** — prevents mitigate/recover loops
5. **Multi-signal risk scoring** with tunable weights
6. **Interface classification** (access/uplink/trunk) feeds storm decisions
7. **Enterprise NOC UX** — RBAC, encrypted secrets, audit trail, retention, email
8. **One stack** — ping + Nmap + discovery + interfaces + storm + reports

---

## Slide 21 — Competitive Positioning

| Typical tools | NetPulse |
|---------------|----------|
| Ping monitors only | Ping **plus** switch-aware storm pipeline |
| SNMP dashboards only | SNMP/SSH stats **driving automated safety gates** |
| Scripts that shut ports | Eligibility + confirmation + safety + recovery engines |
| Separate Nmap / discovery tools | Unified inventory and operator workflow |
| Always-auto remediation | Default **manual**; auto is opt-in |

---

## Slide 22 — Risks & Mitigations (Project Honesty)

| Risk | How NetPulse addresses it |
|------|---------------------------|
| False positive storm | Confirmation window + multi-signal score |
| Shutting an uplink | Eligibility exclusions (uplink/trunk/mgmt/protected) |
| Concurrent actions | TTL lease locks |
| Recovery storms | Stabilization + remmitigate only on fresh evidence |
| Secret leakage | Fernet encryption; never commit `.env` |
| Windows ICMP limits | Document Admin requirement for production-like ping |

---

## Slide 23 — Future Extensions (Discussion)

Possible roadmap items for Q&A:
- Docker / Compose packaging
- Real-time push (WebSocket / SSE) instead of polling
- Broader vendor CLI profiles beyond current SSH parsers
- Correlation of storms across multiple switches
- SIEM / webhook integrations
- Multi-tenant / multi-site scopes

---

## Slide 24 — Summary / Closing

**NetPulse** is a full-stack LAN operations platform that:

- Monitors reachability and profiles hosts continuously  
- Inventories and classifies switch interfaces  
- Detects forming storms with multi-signal scoring  
- Mitigates **only** when eligibility, confirmation, and safety agree  
- Recovers with a dedicated safety engine and anti-loop protections  

**One line:** *Monitor the LAN — and shut the storm, not the network.*

---

## Appendix A — Talking Points (2-minute pitch)

> “NetPulse is a network operations platform for LANs. It continuously pings devices, profiles them with Nmap, discovers hosts and switch ports, and — uniquely — runs a safety-first broadcast-storm pipeline. Before any port is shut down, the system checks that the port is eligible, that risk is confirmed across multiple samples, and that a full safety engine says mitigation is allowed. Operators can keep human approval or enable automatic shutdown. After recovery, the system resets confirmation state so ports don’t flap. Everything sits behind JWT roles, encrypted secrets, and a React NOC UI.”

---

## Appendix B — Key Metrics to Mention

| Metric | Typical default |
|--------|-----------------|
| Ping interval | ~30 seconds |
| Interface stats / storm cycle | ~60 seconds |
| Recovery evaluation | ~30 seconds |
| Nmap / interface discovery | ~1 hour |
| Confirmation | ~4 consecutive high-risk samples |
| Risk confirm threshold | ~75 |
| Broadcast weight in score | ~35% |
| Default mitigation mode | `manual` |

---

## Appendix C — Document Map

| Document | Path |
|----------|------|
| Master product guide | `README.md` |
| Backend / API | `backend/README.md` |
| Frontend routes & stack | `frontend/README.md` |
| Interface collection deep dive | `backend/services/interface_collection/README.md` |
| This presentation report | `PRESENTATION_REPORT.md` |

---

*End of presentation report.*
