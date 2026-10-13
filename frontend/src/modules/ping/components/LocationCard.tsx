/**
 * LocationCard — per-site card with donut charts for ISP Connectivity
 * and Server Monitoring, matching the enterprise dashboard design.
 */
import { useMemo } from 'react'
import { Building2 } from 'lucide-react'
import { PieChart, Pie, Cell, ResponsiveContainer } from 'recharts'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip'
import { formatMs, formatRelative } from '@/utils/format'
import type { IspConnection, SiteMonitoringServer, SiteMonitoringSite } from '@/types'
import { normalizeIspSlotsForLocation } from '@/modules/ping/components/IspConnectivitySection'
import { cn } from '@/lib/utils'

// ─── Colour tokens ───────────────────────────────────────────────────────────

const COLOR_ONLINE = '#22c55e'      // green-500
const COLOR_OFFLINE = '#ef4444'     // red-500
const COLOR_WARNING = '#f59e0b'     // amber-500
const COLOR_UNKNOWN = '#475569'     // slate-600  (Unknown / no data)
const COLOR_UNCONFIGURED = '#334155' // slate-700 (Not configured)

// ─── ISP helpers ─────────────────────────────────────────────────────────────

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

// ─── Donut chart ─────────────────────────────────────────────────────────────

interface DonutChartProps {
  slices: { value: number; color: string }[]
  centerLabel: string
  centerSub?: string
  size?: number
}

function DonutChart({ slices, centerLabel, centerSub, size = 88 }: DonutChartProps) {
  const total = slices.reduce((s, d) => s + d.value, 0)
  const data = total === 0 ? [{ value: 1, color: COLOR_UNKNOWN }] : slices

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius="62%"
            outerRadius="88%"
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
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-base font-bold leading-none tracking-tight text-foreground">
          {centerLabel}
        </span>
        {centerSub ? (
          <span className="mt-0.5 text-[10px] leading-none text-muted-foreground">{centerSub}</span>
        ) : null}
      </div>
    </div>
  )
}

// ─── Legend row ───────────────────────────────────────────────────────────────

function LegendRow({ color, label, count }: { color: string; label: string; count: number }) {
  return (
    <div className="flex items-center gap-1.5">
      <span
        className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
        style={{ backgroundColor: color }}
        aria-hidden="true"
      />
      <span className="text-[11px] font-medium tabular-nums text-foreground">{count}</span>
      <span className="text-[11px] text-muted-foreground">{label}</span>
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
    const online = classified.filter((c) => c.cls === 'Online').length
    const offline = classified.filter((c) => c.cls === 'Offline').length
    const notConfigured = classified.filter((c) => c.cls === 'Not configured').length
    const unknown = classified.filter((c) => c.cls === 'Unknown').length
    return { online, offline, notConfigured, unknown, total: classified.length }
  }, [classified])

  const slices = [
    { value: counts.online, color: COLOR_ONLINE },
    { value: counts.offline, color: COLOR_OFFLINE },
    { value: counts.notConfigured, color: COLOR_UNCONFIGURED },
    { value: counts.unknown, color: COLOR_UNKNOWN },
  ].filter((s) => s.value > 0)

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-xs font-bold uppercase tracking-wider text-foreground">
          ISP Connectivity
        </h4>
        <span className="text-[10px] text-muted-foreground">{counts.total} ISP links</span>
      </div>

      {classified.length === 0 ? (
        <NeutralRingState label="No ISPs configured" />
      ) : (
        <>
          <div className="flex items-center gap-4">
            <DonutChart
              slices={slices}
              centerLabel={`${counts.online}/${counts.total}`}
              centerSub="online"
            />
            <div className="flex flex-col gap-1">
              <LegendRow color={COLOR_ONLINE} label="Online" count={counts.online} />
              <LegendRow color={COLOR_OFFLINE} label="Offline" count={counts.offline} />
              {counts.notConfigured > 0 && (
                <LegendRow
                  color={COLOR_UNCONFIGURED}
                  label="Not configured"
                  count={counts.notConfigured}
                />
              )}
              {counts.unknown > 0 && (
                <LegendRow color={COLOR_UNKNOWN} label="Unknown" count={counts.unknown} />
              )}
            </div>
          </div>

          <div className="overflow-hidden rounded-lg border border-border/50">
            <table className="w-full text-[11px]" aria-label={`ISP links for ${locationName}`}>
              <thead>
                <tr className="border-b border-border/50 bg-secondary/20">
                  <th className="px-2 py-1.5 text-left font-semibold uppercase tracking-wider text-muted-foreground">
                    ISP
                  </th>
                  <th className="px-2 py-1.5 text-left font-semibold uppercase tracking-wider text-muted-foreground">
                    Target IP
                  </th>
                  <th className="px-2 py-1.5 text-left font-semibold uppercase tracking-wider text-muted-foreground">
                    Status
                  </th>
                  <th className="px-2 py-1.5 text-right font-semibold uppercase tracking-wider text-muted-foreground">
                    Latency
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {classified.map(({ isp, cls }) => (
                  <tr key={isp.id} className="hover:bg-secondary/10">
                    <td className="px-2 py-1.5 font-medium text-foreground">{isp.name}</td>
                    <td className="px-2 py-1.5 font-mono text-muted-foreground">
                      {isp.target ? (
                        isp.target
                      ) : (
                        <span className="italic text-slate-500">No target configured</span>
                      )}
                    </td>
                    <td className="px-2 py-1.5">
                      <span
                        className={cn(
                          'inline-flex items-center gap-1.5 font-medium',
                          ispStatusTextClass(cls),
                        )}
                      >
                        <StatusDot dotClass={ispStatusDotClass(cls)} />
                        {cls}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono text-muted-foreground">
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
        </>
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
    const online = classified.filter((c) => c.cls === 'Online').length
    const offline = classified.filter((c) => c.cls === 'Offline').length
    const warning = classified.filter((c) => c.cls === 'Warning').length
    const unknown = classified.filter((c) => c.cls === 'Unknown').length
    return { online, offline, warning, unknown, total: classified.length }
  }, [classified])

  const slices = [
    { value: counts.online, color: COLOR_ONLINE },
    { value: counts.offline, color: COLOR_OFFLINE },
    { value: counts.warning, color: COLOR_WARNING },
    { value: counts.unknown, color: COLOR_UNKNOWN },
  ].filter((s) => s.value > 0)

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-xs font-bold uppercase tracking-wider text-foreground">
          Server Monitoring
        </h4>
        <span className="text-[10px] text-muted-foreground">{counts.total} servers</span>
      </div>

      {classified.length === 0 ? (
        <NeutralRingState label="No servers configured" />
      ) : (
        <>
          <div className="flex items-center gap-4">
            <DonutChart
              slices={slices}
              centerLabel={`${counts.online}/${counts.total}`}
              centerSub="online"
            />
            <div className="flex flex-col gap-1">
              <LegendRow color={COLOR_ONLINE} label="Online" count={counts.online} />
              <LegendRow color={COLOR_OFFLINE} label="Offline" count={counts.offline} />
              <LegendRow color={COLOR_WARNING} label="Warning" count={counts.warning} />
              <LegendRow color={COLOR_UNKNOWN} label="Unknown" count={counts.unknown} />
            </div>
          </div>

          <div className="overflow-hidden rounded-lg border border-border/50">
            <table
              className="w-full text-[11px]"
              aria-label={`Server monitoring for ${locationName}`}
            >
              <thead>
                <tr className="border-b border-border/50 bg-secondary/20">
                  <th className="px-2 py-1.5 text-left font-semibold uppercase tracking-wider text-muted-foreground">
                    Device
                  </th>
                  <th className="px-2 py-1.5 text-left font-semibold uppercase tracking-wider text-muted-foreground">
                    IP Address
                  </th>
                  <th className="px-2 py-1.5 text-left font-semibold uppercase tracking-wider text-muted-foreground">
                    Status
                  </th>
                  <th className="px-2 py-1.5 text-right font-semibold uppercase tracking-wider text-muted-foreground">
                    Latency
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {classified.map(({ server, cls }) => (
                  <Tooltip key={server.id}>
                    <TooltipTrigger asChild>
                      <tr className="cursor-default hover:bg-secondary/10">
                        <td className="px-2 py-1.5 font-medium text-foreground">
                          {server.hostname}
                        </td>
                        <td className="px-2 py-1.5 font-mono text-muted-foreground">
                          {server.ipAddress ?? server.iloAddress ?? '—'}
                        </td>
                        <td className="px-2 py-1.5">
                          <span
                            className={cn(
                              'inline-flex items-center gap-1.5 font-medium',
                              serverStatusTextClass(cls),
                            )}
                          >
                            <StatusDot dotClass={serverStatusDotClass(cls)} />
                            {cls}
                          </span>
                        </td>
                        <td className="px-2 py-1.5 text-right font-mono text-muted-foreground">
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
                          <span className="font-medium">
                            {formatRelative(server.lastCheckedAt)}
                          </span>
                        </p>
                      ) : null}
                    </TooltipContent>
                  </Tooltip>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

// ─── Neutral ring (empty state) ───────────────────────────────────────────────

function NeutralRingState({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-4 py-1">
      <div
        className="relative shrink-0"
        style={{ width: 88, height: 88 }}
        aria-label={label}
        role="img"
      >
        <svg viewBox="0 0 88 88" className="h-full w-full">
          <circle
            cx={44}
            cy={44}
            r={34}
            fill="none"
            stroke={COLOR_UNKNOWN}
            strokeWidth={10}
            opacity={0.35}
          />
        </svg>
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <span className="text-[10px] text-muted-foreground">—</span>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">{label}</p>
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
      className="glass flex flex-col gap-5 rounded-xl border border-border/70 p-4 shadow-sm"
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
