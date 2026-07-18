# NetPulse Frontend

Enterprise NOC dashboard for NetPulse Network Monitor.

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

## Structure

- `src/components/ui` — design system primitives
- `src/components/layout` — sidebar + top navbar shell
- `src/components/shared` — KPI cards, health gauge, status badges, empty/error states
- `src/components/devices` — device table drawer + form dialog
- `src/hooks/queries.ts` — API hooks (polling preserved)
- `src/pages` — route screens (lazy-loaded)
