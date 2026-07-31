# NetPulse Frontend

Enterprise NOC dashboard for NetPulse Network Monitor — device reachability, switch interfaces, and storm protection.

## Stack

- React 19 + Vite + TypeScript
- Tailwind CSS 4 + shadcn-style Radix primitives
- TanStack Query + TanStack Table
- Framer Motion + Recharts
- React Hook Form + Zod + Sonner

## Develop

```bash
npm install
npm run dev
```

Proxies `/api` and `/health` to `http://127.0.0.1:5000`.

## Build

```bash
npm run build
npm run preview
```

Flask serves `dist/` automatically when you run the backend after a build.

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
