# NetPulse Frontend

Enterprise NOC dashboard for NetPulse Network Monitor — device reachability, switch interfaces, and storm protection.

## Stack

- React 19 + Vite + TypeScript
- Tailwind CSS 4 + shadcn-style Radix primitives
- TanStack Query + TanStack Table
- Framer Motion + Recharts
- React Hook Form + Zod + Sonner

## Develop (localhost only)

```bash
npm install
npm run dev
```

UI: `http://127.0.0.1:5173` — proxies `/api` and `/health` to `http://127.0.0.1:5000`.

**Do not use port 5173 for LAN access.** Other computers load hundreds of unbundled dev modules over the network and the UI feels slow.

## Build (LAN / production)

```bash
npm run build
```

Output: `frontend/dist/` (`index.html` + `assets/*`). Flask serves this at the backend port (default **5000**). API calls use same-origin `/api/...` (no Vite proxy required).

```bash
cd ../backend
python app.py
```

Access from other machines: `http://<HOST-LAN-IP>:5000` (Flask binds `0.0.0.0:5000` by default).

`npm run preview` is optional for checking the build locally without Flask.

## Pages

| Route | Page |
|-------|------|
| `/` | Dashboard |
| `/devices` | Device inventory |
| `/interfaces` | Switch interfaces |
| `/interfaces/:deviceId/:interfaceName` | Interface detail + history |
| `/storm` | Storm Protection (eligibility → recovery) |
| `/discovery` | Subnet discovery |
| `/history` | Ping history |
| `/alerts` | Outage alerts |
| `/reports` | Uptime / exports |
| `/settings` | Ping, SMTP, mitigation, recovery, retention |
| `/account` | Profile / user admin |
| `/login` | Sign-in |

## Structure

- `src/api` — HTTP client + endpoint helpers
- `src/auth` — Auth context + protected routes
- `src/components/ui` — design system primitives
- `src/components/layout` — sidebar + top navbar shell
- `src/components/shared` — KPI cards, gauges, badges, empty/error states
- `src/components/devices` — device table drawer + form dialog
- `src/components/interfaces` — interface status badges / helpers
- `src/hooks` — React Query hooks (polling preserved)
- `src/pages` — route screens (lazy-loaded)
- `src/types` — shared TypeScript models

See the root [`README.md`](../README.md) for architecture, storm pipeline, and API overview.
