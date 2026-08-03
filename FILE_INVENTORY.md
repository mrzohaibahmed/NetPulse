# NetPulse — File-by-File Inventory

Full catalog of every source, config, test, and doc file in the repository (217 files, excluding `.git/` internals). NetPulse is a full-stack LAN monitoring and switch broadcast-storm protection platform: a Flask + MongoDB backend (~30k lines of Python) with an APScheduler job engine, and a React 19 + TypeScript + Vite frontend (~13k lines).

Counts in parentheses are line counts. Descriptions are derived from module docstrings, class/function signatures, and the project READMEs.

---

## Root

| File | Description |
|------|-------------|
| `README.md` (568) | Master project doc: what it does, end-to-end flow, storm-protection pipeline, architecture, tech stack, MongoDB collections, env config, API overview, dev setup, default credentials, troubleshooting. |
| `PRESENTATION_REPORT.md` (422) | Slide-by-slide presentation deck (dated July 31, 2026) framing the problem, solution, architecture, and demo talking points for a technical/academic audience. |
| `.gitignore` | Git ignore rules for the repo root. |
| `pyrightconfig.json` | Pyright type-checker config (Python 3.12, Windows, points at `backend/`, treats missing imports as warnings). |
| `start.bat` | Windows launcher: validates backend venv/`.env` and Node/npm, starts Flask (`:5000`) and Vite (`:5173`) in separate windows, opens the UI. |
| `stop.bat` | Windows stopper: kills the launcher windows and frees ports 5000/5173 via PowerShell. |

---

## Backend — top level (`backend/`)

| File | Description |
|------|-------------|
| `app.py` (219) | Flask entry point. Registers all blueprints, sets CORS, ensures every collection's indexes, runs `bootstrap` (seed admin, migrations, encryption check, remove legacy pipeline-generation artifacts), starts the scheduler, and serves the built SPA with an SPA fallback route + `/health`. |
| `scheduler.py` (405) | APScheduler orchestration. Starts/reschedules the ping, Nmap, interface-discovery, interface-stats→storm-chain, recovery (30s), and daily retention jobs. Chains stats → eligibility → risk → confirmation → safety → prepare → optional auto-mitigation. |
| `requirements.txt` | Pinned Python deps: Flask 3, PyMongo, APScheduler, bcrypt, PyJWT, cryptography, paramiko, ping3, python-nmap, pysnmp, openpyxl, python-dotenv. |
| `.env.example` | Template environment file documenting all config variables (Mongo, JWT, secrets key, ping, SMTP, Nmap, interface/SNMP/SSH, retention, storm thresholds). |
| `README.md` (160) | Backend-focused doc: features, tech stack, structure, setup, auth/roles, scheduler job table, storm pipeline summary, API groups, collections. |
| `clear_database.py` (34) | Utility script: drops the entire MongoDB database (all collections). |
| `clear_devices.py` (22) | Utility script: deletes all documents from the `devices` collection. |
| `migrate_encrypt_secrets.py` (127) | One-time manual migration to encrypt plaintext SSH/SMTP secrets already in Mongo (dry-run by default, `--apply` to write). |
| `test_ping.py` | Standalone scratch script for testing ICMP ping behavior. |

### `backend/config/`

| File | Description |
|------|-------------|
| `__init__.py` (0) | Package marker. |
| `database.py` (75) | Loads `.env`, defines all env-derived constants (scan intervals, Nmap, interface/SNMP thresholds), connects to MongoDB, exposes the shared `db` handle; raises if `MONGO_URI`/`DATABASE_NAME` missing. |
| `email.py` (27) | SMTP/alert-email env constants and `email_alerts_configured()` guard. |

### `backend/models/` — document factories

| File | Description |
|------|-------------|
| `__init__.py` (0) | Package marker. |
| `device.py` (102) | `create_device` factory and `normalize_device_credentials` for the `devices` collection. |
| `interface.py` (105) | `create_interface` — vendor-independent interface document factory for `db.interfaces`, shaped for storm-protection consumers. |
| `interface_stats.py` (81) | `create_interface_stat` — append-only interface-statistics document factory. |
| `ping_history.py` (20) | `create_ping_history` — ping-result document factory. |

### `backend/routes/` — Flask blueprints (REST API)

| File | Description |
|------|-------------|
| `__init__.py` (0) | Package marker. |
| `auth_routes.py` (375) | Login (JWT), current user, own-account update, and admin user management (list/update); user serialization and role checks. |
| `device_routes.py` (488) | Device CRUD, filtering/pagination, CSV import, cascade delete. |
| `scan_routes.py` (50) | On-demand ICMP ping for a single device. |
| `nmap_routes.py` (157) | Trigger Nmap scan for one Online device or all online devices. |
| `discovery_routes.py` (83) | LAN `/24` network hint + subnet range sweep with optional auto-register. |
| `history_routes.py` (206) | Ping-history queries (global and per-device) with date/filter parsing. |
| `dashboard_routes.py` (321) | Dashboard summary, KPIs, and chart datasets (status, device type, response time, scan activity). |
| `interface_routes.py` (796) | Interface list/detail, bulk & per-device SSH discovery, stats collection, monitoring-mode changes, manual shutdown/recover, stats history. |
| `storm_routes.py` (1410) | The full Storm Protection REST surface: eligibility/risk/confirmation/safety evaluate + history, incidents, orchestrator prepare, mitigation execute/rollback + history, recovery execute/retry + history. |
| `alert_routes.py` (155) | List, acknowledge, and dismiss critical-outage alerts. |
| `settings_routes.py` (72) | Get/update global settings. |
| `report_routes.py` (261) | Uptime reports and device/history export to CSV and XLSX. |

### `backend/services/` — core services

| File | Description |
|------|-------------|
| `__init__.py` (0) | Package marker. |
| `monitor_service.py` (134) | Ping-loop orchestration: decides which devices to check, applies results, updates device status. |
| `ping_service.py` (60) | Low-level ICMP ping via `ping3` and failure-status classification. |
| `nmap_service.py` (531) | Nmap scanning: runs scans, extracts OS/ports/services/MAC/hostname, writes `networkInfo`, scans all online devices. |
| `discovery_service.py` (158) | Subnet sweep, reverse-DNS hostname resolution, local network-range hint. |
| `history_service.py` (16) | Persists ping-history documents. |
| `alert_service.py` (304) | Creates critical-offline alerts and storm shutdown/recovery/failure alerts; tracks email-sent state. |
| `email_service.py` (567) | Single SMTP path for critical-offline emails and storm notifications (shutdown/recovery/failure); builds and dispatches HTML emails, audits sends. |
| `audit_service.py` (28) | `log_audit` — writes admin/storm action-trail entries. |
| `device_cleanup.py` (154) | Cascade delete: purges all documents referencing a device, transactionally when Mongo supports it, else sequentially. |
| `settings_service.py` (243) | Settings lifecycle: ensure/get/update, public vs. internal views, ping-config resolution. |
| `retention_service.py` (254) | Data retention: TTL indexes for telemetry/eval history plus daily purge of closed storm incidents. |
| `user_service.py` (79) | Seeds default admin and super-admin accounts on first boot. |

#### `backend/services/interface_collection/` — SSH/SNMP switch inventory & stats

| File | Description |
|------|-------------|
| `__init__.py` (35) | Package overview; public entry points (collector, stats_collector). |
| `README.md` (692) | Deep-dive doc on interface discovery + statistics pipeline. |
| `collector.py` (457) | Discovery orchestrator: validate device → SSH raw outputs → parse → normalize → classify → upsert into `interfaces`; index management and bulk discovery. |
| `ssh_collector.py` (563) | SSH transport (Paramiko): resolve creds, connect, disable paging, run vendor `show` commands, return raw outputs; enable-mode and prompt handling. |
| `ssh_stats.py` (462) | SSH fallback counter collector (Cisco/Juniper) matching the SNMP output shape; counter/error/speed parsing. |
| `snmp.py` (498) | SNMP IF-MIB/IF-X-MIB collector for inventory and stats, preferring 64-bit HC counters; credential resolution and table merging. |
| `stats_collector.py` (549) | Periodic stats orchestrator: prefer SNMP, fall back to SSH, compute utilization from counter deltas, append to `interface_stats`; history/index helpers. |
| `parser.py` (833) | Vendor CLI parsers (Cisco status/description/switchport/VLAN/CDP/LLDP, Juniper terse) → loosely-structured interface dicts. |
| `normalizer.py` (409) | Maps vendor-specific fields to the single vendor-independent interface schema (status, mode, VLAN, speed, duplex, neighbors). |
| `naming.py` (119) | Vendor-agnostic interface-name canonicalization so `Gi1/0/1` == `GigabitEthernet1/0/1`. |
| `classifier.py` (248) | Pure classification (no SSH/SNMP): flags interfaces access/trunk/uplink/infrastructure and infers neighbor device type. |
| `monitoring_state.py` (355) | Separates admin monitoring intent (`AUTO`/`DISABLED_BY_USER`/…) from transient operational state; resolve/apply/migrate monitoring preferences. |
| `utilization.py` (200) | Computes RX/TX/overall link utilization from consecutive counter samples and negotiated bandwidth. |

#### `backend/services/storm/` — Storm Protection engine

| File | Description |
|------|-------------|
| `__init__.py` (84) | Package overview of the storm engines. |
| `config.py` (82) | `StormConfig` — env-driven storm feature flags/config with reload support. |
| `thresholds.py` (201) | Configurable risk thresholds, metric weights, score/severity mapping. |
| `eligibility.py` (519) | Port Eligibility Engine (first decision layer): is an interface eligible for storm analysis? Rule-based on interface metadata; store/query results. |
| `exceptions.py` (17) | Domain exceptions for the eligibility engine. |
| `models.py` (540) | Strongly-typed result models (Eligibility, Risk, Confirmation, Safety, Prepare) and their Mongo/API document factories. |
| `history.py` (185) | Rate-calculation helpers (counter deltas, rollover handling) from consecutive `interface_stats` samples. |
| `risk_engine.py` (557) | Advanced Risk Score Engine: estimates storm probability for eligible interfaces; store/query risk history. |
| `aggregator.py` (88) | Weighted aggregation of independent analyzer outputs, redistributing weight over supported analyzers. |
| `confirmation.py` (641) | Confirmation Engine: decides whether high-risk conditions persisted long enough to be a real storm (consecutive samples → CONFIRMED). |
| `confirmation_history.py` (188) | Mongo-only loaders for confirmation (risk history, prior state, eligibility, poll-failure detection, window stats). |
| `confirmation_rules.py` (98) | Env-configurable confirmation thresholds and consecutive-sample → state mapping. |
| `safety.py` (594) | Safety Engine: final go/no-go before automatic mitigation (device online, SSH OK, cooldown, not already shut, etc.). |
| `safety_checks.py` (174) | The individual ordered safety checks (RULE_1…RULE_14), each returning pass/fail + reason. |
| `safety_history.py` (276) | Safety context loaders (Mongo + optional read-only SSH probes); never mutates config. |
| `safety_rules.py` (99) | Env-configurable Safety Engine policy. |
| `source_classification.py` (232) | Classifies whether elevated metrics originate on, forward through, or are received by a port (RX/TX convention). |
| `source_arbitration_config.py` (110) | Env-configurable source-arbitration policy (arbitration + receiver filtering). |
| `storm_source_selector.py` (633) | Storm source arbitration: picks the single most probable originating interface per broadcast domain among elevated candidates. |
| `risk_engine.py` | *(listed above)* |
| `orchestrator.py` (497) | Mitigation Orchestrator: prepares mitigation (diagnostics + incident) only, gated on live-CONFIRMED + current risk + fresh SAFE. Never executes config. |
| `mitigation_context.py` (61) | Builds the immutable context the Mitigation Engine consumes after prepare. |
| `incident.py` (422) | Storm incident lifecycle: create from diagnostics or manual action, append timeline events, list/get; append-only evidence. |
| `lock_service.py` (295) | Shared Mongo-backed TTL lease locks for mitigation and recovery (acquire/release/renew/reclaim). |
| `risk_engine.py` | |

##### `backend/services/storm/analyzers/` — independent risk analyzers

| File | Description |
|------|-------------|
| `__init__.py` (22) | Base `Analyzer` type. |
| `broadcast.py` (46) | Broadcast packets/sec analyzer. |
| `multicast.py` (46) | Multicast packets/sec analyzer. |
| `unknown_unicast.py` (44) | Unknown-unicast packets/sec analyzer. |
| `crc.py` (42) | CRC errors/sec analyzer. |
| `errors.py` (42) | Input/output errors-per-second analyzer. |
| `discards.py` (46) | Discard packets/sec analyzer. |
| `utilization.py` (39) | Interface utilization-% analyzer. |
| `directional.py` (130) | RX/TX directional rate helpers with fallback to legacy combined counters. |

##### `backend/services/storm/diagnostics/` — read-only evidence capture

| File | Description |
|------|-------------|
| `__init__.py` (15) | Package exports. |
| `collector.py` (275) | Diagnostics orchestrator: collects immutable pre-mitigation evidence from Mongo + read-only SSH. |
| `ssh_capture.py` (139) | Read-only SSH capture that hard-blocks any config/shutdown/write command. |
| `snapshots.py` (205) | Parses read-only `show` output into structured interface/switchport/MAC/device-health snapshots. |
| `serializer.py` (115) | Serializes diagnostics/incident/prepare payloads to JSON-safe HTTP responses. |

##### `backend/services/storm/mitigation/` — shutdown execution

| File | Description |
|------|-------------|
| `__init__.py` (19) | Subpackage marker + index helper export. |
| `engine.py` (519) | Mitigation execution engine: lock acquisition, SSH execution, verification, rollback, auditing. |
| `strategy.py` (145) | Mitigation strategies (Cisco/Juniper): shutdown and no-shutdown-recovery command/verification/rollback sets. |
| `ssh_executor.py` (124) | Safe SSH executor: validates commands against templates and scans output for error signatures. |
| `verifier.py` (49) | Runs verification commands and evaluates whether mitigation succeeded. |
| `rollback.py` (62) | Executes a strategy's rollback commands, reconnecting if needed. |
| `audit.py` (193) | Immutable `storm_mitigation_history` records + audit logs; serialization. |

##### `backend/services/storm/recovery/` — port restoration

| File | Description |
|------|-------------|
| `__init__.py` (20) | Subpackage marker + index helper export. |
| `engine.py` (503) | Recovery coordination: locking, validation, execution, retries, re-mitigation triggers. |
| `state_machine.py` (83) | Recovery lifecycle state machine (cooldown → validate → execute → recurrence). |
| `policy.py` (45) | Thin facade over the Recovery Safety Engine (mitigation safety must never be called here). |
| `safety.py` (450) | Recovery Safety Engine (rules R0–R8): "is it safe to bring the port back up?" — independent of mitigation safety. |
| `verifier.py` (102) | Verifies interface came back up and collects post-recovery stats. |
| `post_recovery.py` (155) | Post-recovery pipeline invalidation: reset confirmation, invalidate safety, cancel orphan READY incidents. |
| `scheduler.py` (325) | Periodic recovery/stabilization cycle run inside APScheduler; picks newest MITIGATED per interface. |
| `audit.py` (142) | Immutable `storm_recovery_history` records + audit logs; serialization. |

### `backend/utils/`

| File | Description |
|------|-------------|
| `__init__.py` (0) | Package marker. |
| `auth.py` (167) | JWT create/decode, bcrypt hash/verify, role normalization/hierarchy, `require_auth` decorator. |
| `secret_crypto.py` (102) | Fernet field-level encryption for secrets at rest (`npenc:` prefix, legacy plaintext tolerant). |
| `serializers.py` (386) | Central JSON serializers for devices, interfaces, stats, and all storm result types. |
| `pagination.py` (37) | Pagination param parsing, page clamping, and response payload helper. |
| `monitor_logger.py` (34) | Configured logger factory writing to `logs/monitor.log` + console. |

### `backend/tests/` — unit & integration tests

| File | Description |
|------|-------------|
| `__init__.py` (0) | Package marker. |
| `test_eligibility.py` (187) | Port Eligibility Engine rules (access/trunk/uplink/management/admin-down, config toggles). |
| `test_risk_engine.py` (229) | Risk scoring across storm types, rollover, severity bands. |
| `test_confirmation.py` (220) | Confirmation Engine (consecutive-high, poll failure, threshold changes). |
| `test_safety.py` (217) | Safety Engine checks (offline, SSH, cooldown, maintenance, locks, thresholds). |
| `test_recovery_safety.py` (357) | Recovery Safety Engine R1–R8 blocking conditions. |
| `test_recovery_engine.py` (767) | Recovery engine transitions, policy, retries, stabilization re-mitigation. |
| `test_mitigation_engine.py` (276) | Mitigation engine: command whitelisting, verification/rollback, lock conflicts. |
| `test_diagnostics_orchestrator.py` (379) | Read-only SSH guards, snapshot parsing, incident creation, prepare gating. |
| `test_post_recovery_invalidation.py` (161) | Post-recovery reset + re-mitigation freshness. |
| `test_monitoring_state.py` (471) | Interface monitoring-state intent vs. operational state, rediscovery/migration. |
| `test_directional_stats.py` (285) | RX/TX directional stats parsing/storage and source analysis. |
| `test_utilization.py` (165) | Utilization calc and Cisco speed parsing (rollover, reset). |
| `test_storm_source_attribution.py` (483) | Source classification and arbitration selection. |
| `test_storm_alerts.py` (405) | Storm alert creation integrated with the Alerts module. |
| `test_storm_email_notifications.py` (315) | Storm email notification content and flag gating. |
| `test_incident_serializer.py` (127) | Incident HTTP serialization is JSON-safe. |
| `test_manual_interface_control_routes.py` (282) | Manual shutdown/recover route auth + success/failure paths. |
| `test_device_cascade_delete.py` (160) | Integration: device delete removes all referencing docs (needs live Mongo). |
| `test_reliability_indexes_and_lock_ttl.py` (216) | Index idempotency, unique constraints, lock TTL/reclaim (needs live Mongo). |
| `test_role_hierarchy.py` (33) | Role-satisfaction hierarchy (super-admin ⊃ admin ⊃ operator ⊃ viewer). |

---

## Frontend (`frontend/`)

| File | Description |
|------|-------------|
| `index.html` | Vite HTML entry point. |
| `package.json` | React 19, TanStack Query/Table, Radix UI, Recharts, Framer Motion, react-hook-form+zod, Tailwind 4; scripts for dev/build/lint/preview. |
| `package-lock.json` | Locked dependency tree. |
| `vite.config.ts` | Vite config; dev server proxies `/api` and `/health` to Flask at `127.0.0.1:5000`. |
| `tsconfig.json` / `tsconfig.app.json` / `tsconfig.node.json` | TypeScript project references and compiler options. |
| `.oxlintrc.json` | oxlint linter config. |
| `.gitignore` | Frontend ignore rules. |
| `README.md` | Frontend setup/usage doc. |
| `public/favicon.svg` | App favicon. |

### `frontend/src/` — app shell & entry

| File | Description |
|------|-------------|
| `main.tsx` (10) | React/Vite bootstrap: mounts `<App>`. |
| `App.tsx` (111) | Router, providers (auth, theme, query client), route table with lazy pages and a page fallback. |
| `index.css` | Global Tailwind + theme styles. |

### `frontend/src/api/`

| File | Description |
|------|-------------|
| `client.ts` (119) | Fetch wrapper: JWT storage, `ApiRequestError`, auth-expired handler. |
| `index.ts` (692) | Typed API client — every endpoint helper (auth, devices, scan/nmap, history, dashboard, discovery, interfaces, storm, alerts, settings, reports). |

### `frontend/src/auth/`

| File | Description |
|------|-------------|
| `AuthContext.tsx` (94) | Auth context/provider and `useAuth` hook (login state, token, current user). |

### `frontend/src/hooks/`

| File | Description |
|------|-------------|
| `queries.ts` (923) | All TanStack Query hooks (polling reads + mutations) for dashboard, devices, history, interfaces, storm, alerts, etc. |
| `queryKeys.ts` (43) | Centralized query-key factory. |
| `useClientPagination.ts` (39) | Generic client-side pagination hook. |

### `frontend/src/lib/` & `utils/` & `constants/`

| File | Description |
|------|-------------|
| `lib/utils.ts` (6) | `cn` Tailwind class-merge helper. |
| `lib/status.ts` (22) | Status/type color maps and status-tone helpers. |
| `lib/health.ts` (39) | Network-health label computation and colors from dashboard summary. |
| `lib/device-icons.ts` (30) | Maps device type → Lucide icon. |
| `lib/theme.tsx` (41) | Theme (dark/light) provider and `useTheme` hook. |
| `utils/format.ts` (88) | Formatters: ms, datetime, percent, relative time, bytes, packets, utilization, bps. |
| `utils/interfaceNames.ts` (42) | Frontend interface-name canonicalization / match (mirrors backend `naming.py`). |
| `constants/devices.ts` (15) | Device type list and default type. |
| `types/index.ts` (698) | All shared TypeScript types/interfaces (Device, PingHistory, Dashboard, interfaces, storm, users, etc.). |

### `frontend/src/components/`

| File | Description |
|------|-------------|
| `ProtectedRoute.tsx` (26) | Route guard redirecting unauthenticated users to login. |
| `layout/Layout.tsx` (29) | App shell layout (sidebar + top nav + page outlet). |
| `layout/Sidebar.tsx` (288) | Navigation sidebar with pin/mobile state and a health row. |
| `layout/TopNavbar.tsx` (195) | Top bar showing last-updated time and monitoring status. |
| `devices/DeviceDrawer.tsx` (491) | Device detail drawer (tabs, Nmap info, meta/stat rows). |
| `devices/DeviceFormDialog.tsx` (231) | Add/edit device dialog (react-hook-form + zod). |
| `interfaces/InterfaceStatusBadge.tsx` (206) | Interface status/port-mode/classification badges, utilization bar, VLAN/neighbor formatting. |
| `shared/EmptyState.tsx` (38) | Empty-state placeholder. |
| `shared/ErrorState.tsx` (39) | Error-state placeholder. |
| `shared/HealthGauge.tsx` (70) | Network-health gauge + badge. |
| `shared/KpiCard.tsx` (83) | Dashboard KPI card. |
| `shared/LoadingState.tsx` (48) | Loading spinner + dashboard/table skeletons. |
| `shared/PageHeader.tsx` (21) | Page title/description/actions header. |
| `shared/PageTransition.tsx` (19) | Framer Motion page-transition wrapper. |
| `shared/PaginationControls.tsx` (77) | Pagination UI controls. |
| `shared/StatusBadge.tsx` (38) | Device-status badge with pulse. |
| `ui/*.tsx` (19 files) | shadcn/Radix UI primitives: `alert-dialog`, `avatar`, `badge`, `button`, `card`, `checkbox`, `dialog`, `dropdown-menu`, `input`, `label`, `progress`, `scroll-area`, `select`, `separator`, `sheet`, `skeleton`, `table`, `tooltip`. Reusable styled building blocks. |

### `frontend/src/pages/` — routed pages

| File | Description |
|------|-------------|
| `LoginPage.tsx` (263) | Login screen with feature highlights. |
| `DashboardPage.tsx` (670) | Live KPIs, status/type/response-time charts, recent activity. |
| `DevicesPage.tsx` (492) | Device inventory table, CRUD, manual ping/Nmap, latency bar. |
| `DiscoveryPage.tsx` (274) | Subnet discovery: network hint + range sweep + auto-register. |
| `HistoryPage.tsx` (179) | Filterable ping history and per-device uptime. |
| `InterfacesPage.tsx` (381) | Switch interface inventory, discovery, monitoring mode. |
| `InterfaceDetailPage.tsx` (1209) | Deep per-interface view: stats charts, history, manual shutdown/recover, detail rows. |
| `StormProtectionPage.tsx` (2708) | The storm-protection console: eligibility/risk/confirmation/safety panels, incidents, mitigation & recovery history, many status/badge/progress subcomponents. |
| `AlertsPage.tsx` (278) | Critical-outage alerts with severity badges and acknowledge/dismiss + timeline. |
| `ReportsPage.tsx` (249) | Uptime reports and CSV/XLSX export. |
| `SettingsPage.tsx` (414) | Global settings: ping interval, SMTP, mitigation mode, auto-recovery, retention. |
| `AccountPage.tsx` (344) | Change own username/password; admin user management. |

---

*Not itemized: `.git/` internals and `frontend/node_modules/` (dependency code, not part of the project source).*
