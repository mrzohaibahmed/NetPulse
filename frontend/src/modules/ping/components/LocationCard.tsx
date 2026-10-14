/**
 * LocationCard — per-site card with donut charts for ISP Connectivity
 * and Server Monitoring, matching the enterprise dashboard design.
 *
 * Layout per section (desktop):
 *   LEFT  col (~40%): heading + total count + status legend, vertically centred
 *   RIGHT col (~60%): square chart wrapper that fills the column; Recharts
 *                     ResponsiveContainer fills the square absolutely so the
 *                     visible ring grows with the available width.
 *   BELOW: full-width ISP / device table
 *
 * On narrow viewports the grid stacks to a single column.
 */
import { useMemo } from 'react'
import { Building2 } from 'lucide-react'
import { PieChart, Pie, Cell, ResponsiveContainer } from 'recharts'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip'
import { formatMs, formatRelative } from '@/utils/format'
import type { IspConnection, SiteMonitoringServer, SiteMonitoringSite } from '@/types'
import { normalizeIspSlotsForLocation } from '@/modules/ping/components/IspConnectivitySection'
import { cn } from '@/lib/utils'

// ─── Colour tokens ────────────────────────────────────────────────────────────

const COLOR_ONLINE       = '#22c55e'   // green-500
const COLOR_OFFLINE      = '#ef4444'   // red-500
const COLOR_WARNING      = '#f59e0b'   // amber-500
const COLOR_UNKNOWN      = '#475569'   // slate-600
const COLOR_UNCONFIGURED = '#334155'   // slate-700

// ─── ISP helpers ──────────────────────────────────────────────────────────────

type IspCls = 'Online' | 'Offline' | 'Not configured' | 'Unknown'

function classifyIsp(isp: IspConnection): IspCls {
  if (!isp.target || isp.target.trim() === '') return 'Not configured'
  if (isp.status === 'Online') return 'Online'
  if (isp.status === 'Offline') return 'Offline'
  return 'Unknown'
}

function ispStatusDotClass(cls: IspCls): string {
  if (cls === 'Online') return 'bg-green-500'
  if (cls === 'Offline') return 'bg-red-500'
  if (cls === 'Not configured') return 'bg-slate-700'
  return 'bg-slate-500'
}

function ispStatusTextClass(cls: IspCls): string {
  if (cls === 'Online') return 'text-green-400'
  if (cls === 'Offline') return 'text-red-400'
  return 'text-slate-400'
}

// ─── Server helpers ───────────────────────────────────────────────────────────

type ServerCls = 'Online' | 'Offline' | 'Warning' | 'Unknown'

function classifyServer(server: SiteMonitoringServer): ServerCls {
  if (server.status === 'Online') return 'Online'
  if (server.status === 'Offline (Critical)' || server.status === 'Offline') return 'Offline'
  if (server.status === 'Not Reachable') return 'Warning'
  return 'Unknown'
}

function serverStatusDotClass(cls: ServerCls): string {
  if (cls === 'Online') return 'bg-green-500'
  if (cls === 'Offline') return 'bg-red-500'
  if (cls === 'Warning') return 'bg-amber-500'
  return 'bg-slate-500'
}

function serverStatusTextClass(cls: ServerCls): string {
  if (cls === 'Online') return 'text-green-400'
  if (cls === 'Offline') return 'text-red-400'
  if (cls === 'Warning') return 'text-amber-400'
  return 'text-slate-400'
}

// ─── DonutChart ───────────────────────────────────────────────────────────────
//
// The chart wrapper is a square that fills 100% of whatever column it sits in.
// The key technique that makes Recharts fill the space:
//   1. Outer div: w-full + aspect-square  → defines a pixel size at runtime
//   2. Inner div: position:absolute inset-0 → gives ResponsiveContainer a
//      measured parent with an explicit pixel height so it can respond
//   3. PieChart margin={0} → no internal whitespace stealing ring area
//   4. Overlay div: absolute inset-0 → centre label stays centred over ring

interface DonutChartProps {
  slices: { value: number; color: string }[]
  centerLabel: string
  centerSub?: string
}

function DonutChart({ slices, centerLabel, centerSub }: DonutChartProps) {
  const total = slices.reduce((s, d) => s + d.value, 0)
  const data = total === 0 ? [{ value: 1, color: COLOR_UNKNOWN }] : slices

  return (
    // Outer square: grows with the grid column
    <div className="relative aspect-square w-full max-w-[190px] [container-type:inline-size]">
      {/* Recharts needs an absolutely-positioned parent to measure height */}
      <div className="absolute inset-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
            <Pie
              data={data}
              cx="50%"
              cy="50%"
              innerRadius="68%"
              outerRadius="96%"
              startAngle={90}
              endAngle={-270}
              dataKey="value"
              stroke="none"
              paddingAngle={total === 0 ? 0 : 2}
            >
              {data.map((entry, i) => (
                <Cell key={i} fill={entry.color} />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
      </div>

      {/* Centre label overlay */}
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        <span
          className="font-semibold leading-none tracking-tight text-foreground"
          style={{ fontSize: 'clamp(1.5rem, 15cqi, 1.75rem)' }}
        >
          {centerLabel}
        </span>
        {centerSub ? (
          <span
            className="mt-[4cqi] leading-none text-muted-foreground"
            style={{ fontSize: 'clamp(0.75rem, 7cqi, 0.875rem)' }}
          >
            {centerSub}
          </span>
        ) : null}
      </div>
    </div>
  )
}

// ─── Empty-state ring ─────────────────────────────────────────────────────────
//
// Uses the same square wrapper so it matches the populated donut in size.

function EmptyDonut({ label }: { label: string }) {
  return (
    <div
      className="relative aspect-square w-full max-w-[190px] [container-type:inline-size]"
      aria-label={label}
      role="img"
    >
      <span className="sr-only">{label}</span>
      {/* SVG ring scales with the container */}
      <svg viewBox="0 0 100 100" className="h-full w-full">
        {/* Matches the populated chart's 68% inner and 96% outer radii. */}
        <circle
          cx={50}
          cy={50}
          r={41}
          fill="none"
          stroke={COLOR_UNKNOWN}
          strokeWidth={14}
          opacity={0.3}
        />
      </svg>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        <span
          className="font-semibold leading-none text-muted-foreground"
          style={{ fontSize: 'clamp(1.5rem, 15cqi, 1.75rem)' }}
        >
          —
        </span>
        <span
          className="mt-[4cqi] leading-none text-muted-foreground"
          style={{ fontSize: 'clamp(0.75rem, 7cqi, 0.875rem)' }}
        >
          empty
        </span>
      </div>
    </div>
  )
}

// ─── Legend row ───────────────────────────────────────────────────────────────

function LegendRow({ color, label, count }: { color: string; label: string; count: number }) {
  return (
    <div className="flex items-center gap-2">
      <span
        className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
        style={{ backgroundColor: color }}
        aria-hidden="true"
      />
      <span className="text-[13px] font-medium tabular-nums text-foreground">{count}</span>
      <span className="text-[13px] text-muted-foreground">{label}</span>
    </div>
  )
}

// ─── Status dot ───────────────────────────────────────────────────────────────

function StatusDot({ dotClass }: { dotClass: string }) {
  return (
    <span
      className={cn('inline-block h-2 w-2 shrink-0 rounded-full', dotClass)}
      aria-hidden="true"
    />
  )
}

// ─── SectionLayout ───────────────────────────────────────────────────────────
//
// Flexible legend column plus a bounded chart column.
// On narrow screens it collapses to a single column so text stays readable.

function SectionLayout({
  left,
  right,
}: {
  left: React.ReactNode
  right: React.ReactNode
}) {
  return (
    <div className="grid min-h-[190px] grid-cols-[minmax(0,1fr)_clamp(10.5rem,42%,11.875rem)] items-center gap-5 max-[480px]:min-h-0 max-[480px]:grid-cols-1">
      {/* LEFT: heading + count + legend */}
      <div className="flex min-w-0 flex-col justify-center gap-3">{left}</div>
      {/* RIGHT: square chart that fills its column */}
      <div className="flex min-w-0 items-center justify-end max-[480px]:justify-center">{right}</div>
    </div>
  )
}

// ─── ISP section ─────────────────────────────────────────────────────────────

function IspSection({
  isps,
  locationName,
  isLoading,
}: {
  isps: IspConnection[]
  locationName: string
  isLoading?: boolean
}) {
  const classified = useMemo(
    () =>
      normalizeIspSlotsForLocation(isps, locationName).map((isp) => ({
        isp,
        cls: classifyIsp(isp),
      })),
    [isps, locationName],
  )

  const counts = useMemo(() => {
    const online        = classified.filter((c) => c.cls === 'Online').length
    const offline       = classified.filter((c) => c.cls === 'Offline').length
    const notConfigured = classified.filter((c) => c.cls === 'Not configured').length
    const unknown       = classified.filter((c) => c.cls === 'Unknown').length
    return { online, offline, notConfigured, unknown, total: classified.length }
  }, [classified])

  const slices = [
    { value: counts.online,        color: COLOR_ONLINE },
    { value: counts.offline,       color: COLOR_OFFLINE },
    { value: counts.notConfigured, color: COLOR_UNCONFIGURED },
    { value: counts.unknown,       color: COLOR_UNKNOWN },
  ].filter((s) => s.value > 0)

  return (
    <div className="min-w-0 space-y-3">
      <SectionLayout
        left={
          <>
            <div>
              <h4 className="text-sm font-semibold uppercase tracking-wider text-foreground">
                ISP Connectivity
              </h4>
              <p className="mt-1 text-[13px] text-muted-foreground">
                {counts.total} ISP link{counts.total !== 1 ? 's' : ''}
              </p>
            </div>
            <div className="flex flex-col gap-1.5">
              <LegendRow color={COLOR_ONLINE}       label="Online"         count={counts.online} />
              <LegendRow color={COLOR_OFFLINE}      label="Offline"        count={counts.offline} />
              {counts.notConfigured > 0 && (
                <LegendRow color={COLOR_UNCONFIGURED} label="Not configured" count={counts.notConfigured} />
              )}
              {counts.unknown > 0 && (
                <LegendRow color={COLOR_UNKNOWN}    label="Unknown"        count={counts.unknown} />
              )}
            </div>
          </>
        }
        right={
          classified.length === 0 ? (
            <EmptyDonut label="No ISPs configured" />
          ) : (
            <DonutChart
              slices={slices}
              centerLabel={`${counts.online}/${counts.total}`}
              centerSub="online"
            />
          )
        }
      />

      {/* Full-width table */}
      {classified.length > 0 && (
        <div className="w-full max-w-full overflow-x-auto rounded-lg border border-border/50 sm:overflow-x-hidden">
          <table className="w-full min-w-[410px] table-fixed text-[13px] sm:min-w-0" aria-label={`ISP links for ${locationName}`}>
            <colgroup>
              <col className="w-[22%]" />
              <col className="w-[37%]" />
              <col className="w-[27%]" />
              <col className="w-[14%]" />
            </colgroup>
            <thead>
              <tr className="border-b border-border/50 bg-secondary/20">
                <th className="px-2 py-2 text-left text-xs font-semibold uppercase tracking-wider text-muted-foreground">ISP</th>
                <th className="px-2 py-2 text-left text-xs font-semibold uppercase tracking-wider text-muted-foreground">Target IP</th>
                <th className="px-2 py-2 text-left text-xs font-semibold uppercase tracking-wider text-muted-foreground">Status</th>
                <th className="px-2 py-2 text-right text-xs font-semibold uppercase tracking-wider text-muted-foreground">Latency</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {classified.map(({ isp, cls }) => (
                <tr key={isp.id} className="hover:bg-secondary/10">
                  <td className="truncate px-2 py-2 font-medium text-foreground" title={isp.name}>
                    {isp.name}
                  </td>
                  <td
                    className="truncate px-2 py-2 font-mono text-muted-foreground"
                    title={isp.target || 'No target configured'}
                  >
                    {isp.target ? (
                      isp.target
                    ) : (
                      <span className="italic text-slate-500">No target configured</span>
                    )}
                  </td>
                  <td className="overflow-hidden px-2 py-2" title={cls}>
                    <span className={cn('inline-flex max-w-full items-center gap-1.5 font-medium', ispStatusTextClass(cls))}>
                      <StatusDot dotClass={ispStatusDotClass(cls)} />
                      <span className="truncate">{cls}</span>
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-2 py-2 text-right font-mono text-muted-foreground">
                    {isLoading && !isp.lastCheckedAt
                      ? '…'
                      : cls === 'Online'
                        ? formatMs(isp.responseTime)
                        : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ─── Server section ───────────────────────────────────────────────────────────

function ServerSection({
  servers,
  locationName,
  isLoading,
}: {
  servers: SiteMonitoringServer[]
  locationName: string
  isLoading?: boolean
}) {
  const classified = useMemo(
    () => servers.map((server) => ({ server, cls: classifyServer(server) })),
    [servers],
  )

  const counts = useMemo(() => {
    const online  = classified.filter((c) => c.cls === 'Online').length
    const offline = classified.filter((c) => c.cls === 'Offline').length
    const warning = classified.filter((c) => c.cls === 'Warning').length
    const unknown = classified.filter((c) => c.cls === 'Unknown').length
    return { online, offline, warning, unknown, total: classified.length }
  }, [classified])

  const slices = [
    { value: counts.online,  color: COLOR_ONLINE },
    { value: counts.offline, color: COLOR_OFFLINE },
    { value: counts.warning, color: COLOR_WARNING },
    { value: counts.unknown, color: COLOR_UNKNOWN },
  ].filter((s) => s.value > 0)

  return (
    <div className="min-w-0 space-y-3">
      <SectionLayout
        left={
          <>
            <div>
              <h4 className="text-sm font-semibold uppercase tracking-wider text-foreground">
                Server Monitoring
              </h4>
              <p className="mt-1 text-[13px] text-muted-foreground">
                {counts.total} server{counts.total !== 1 ? 's' : ''}
              </p>
            </div>
            <div className="flex flex-col gap-1.5">
              <LegendRow color={COLOR_ONLINE}  label="Online"  count={counts.online} />
              <LegendRow color={COLOR_OFFLINE} label="Offline" count={counts.offline} />
              <LegendRow color={COLOR_WARNING} label="Warning" count={counts.warning} />
              <LegendRow color={COLOR_UNKNOWN} label="Unknown" count={counts.unknown} />
            </div>
          </>
        }
        right={
          classified.length === 0 ? (
            <EmptyDonut label="No servers configured" />
          ) : (
            <DonutChart
              slices={slices}
              centerLabel={`${counts.online}/${counts.total}`}
              centerSub="online"
            />
          )
        }
      />

      {/* Full-width table */}
      {classified.length > 0 && (
        <div className="w-full max-w-full overflow-x-auto rounded-lg border border-border/50 sm:overflow-x-hidden">
          <table className="w-full min-w-[410px] table-fixed text-[13px] sm:min-w-0" aria-label={`Server monitoring for ${locationName}`}>
            <colgroup>
              <col className="w-[36%]" />
              <col className="w-[27%]" />
              <col className="w-[22%]" />
              <col className="w-[15%]" />
            </colgroup>
            <thead>
              <tr className="border-b border-border/50 bg-secondary/20">
                <th className="px-2 py-2 text-left text-xs font-semibold uppercase tracking-wider text-muted-foreground">Device</th>
                <th className="px-2 py-2 text-left text-xs font-semibold uppercase tracking-wider text-muted-foreground">IP Address</th>
                <th className="px-2 py-2 text-left text-xs font-semibold uppercase tracking-wider text-muted-foreground">Status</th>
                <th className="px-2 py-2 text-right text-xs font-semibold uppercase tracking-wider text-muted-foreground">Latency</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {classified.map(({ server, cls }) => (
                <Tooltip key={server.id}>
                  <TooltipTrigger asChild>
                    <tr className="cursor-default hover:bg-secondary/10">
                      <td className="truncate px-2 py-2 font-medium text-foreground" title={server.hostname}>
                        {server.hostname}
                      </td>
                      <td
                        className="truncate px-2 py-2 font-mono text-muted-foreground"
                        title={server.ipAddress ?? server.iloAddress ?? '—'}
                      >
                        {server.ipAddress ?? server.iloAddress ?? '—'}
                      </td>
                      <td className="px-2 py-2">
                        <span className={cn('inline-flex whitespace-nowrap items-center gap-1.5 font-medium', serverStatusTextClass(cls))}>
                          <StatusDot dotClass={serverStatusDotClass(cls)} />
                          {cls}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-2 py-2 text-right font-mono text-muted-foreground">
                        {isLoading && !server.lastCheckedAt
                          ? '…'
                          : cls === 'Online'
                            ? formatMs(server.responseTime)
                            : '—'}
                      </td>
                    </tr>
                  </TooltipTrigger>
                  <TooltipContent side="left" className="text-xs">
                    <p>
                      Last seen:{' '}
                      <span className="font-medium">
                        {server.lastSeen ? formatRelative(server.lastSeen) : '—'}
                      </span>
                    </p>
                    {server.lastCheckedAt ? (
                      <p>
                        Last checked:{' '}
                        <span className="font-medium">{formatRelative(server.lastCheckedAt)}</span>
                      </p>
                    ) : null}
                  </TooltipContent>
                </Tooltip>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ─── Public: LocationCard ────────────────────────────────────────────────────

export interface LocationCardProps {
  site: SiteMonitoringSite
  isLoading?: boolean
}

export function LocationCard({ site, isLoading }: LocationCardProps) {
  return (
    <article
      className="glass min-w-0 max-w-full flex flex-col gap-4 rounded-xl border border-border/70 p-4 shadow-sm"
      aria-label={`${site.name} monitoring`}
    >
      {/* Card header */}
      <div className="flex items-center gap-2 border-b border-border/60 pb-3">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Building2 className="h-4 w-4" />
        </div>
        <h3 className="text-sm font-bold uppercase tracking-widest text-foreground">
          {site.name}
        </h3>
      </div>

      {/* ISP Connectivity */}
      <IspSection isps={site.isps} locationName={site.name} isLoading={isLoading} />

      {/* Divider */}
      <div className="border-t border-border/40" />

      {/* Server Monitoring */}
      <ServerSection servers={site.servers} locationName={site.name} isLoading={isLoading} />
    </article>
  )
}
