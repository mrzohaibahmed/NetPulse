import { useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  Bell,
  Check,
  Gauge,
  Server,
  Timer,
  Wifi,
  WifiOff,
  X,
  AlertTriangle,
  ChevronRight,
} from 'lucide-react'
import { useAuth } from '@/auth/AuthContext'
import { useTheme } from '@/lib/theme'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useAlertMutations, useDashboardQuery } from '@/hooks/queries'
import { computeNetworkHealth } from '@/lib/health'
import { isStormAlert, STATUS_COLORS, TYPE_COLORS } from '@/lib/status'
import { formatDateTime, formatMs, formatPercent, formatRelative } from '@/utils/format'
import { useClientPagination } from '@/hooks/useClientPagination'
import type { AlertItem, DeviceStatusRow } from '@/types'
import { KpiCard } from '@/components/shared/KpiCard'
import { HealthGauge } from '@/components/shared/HealthGauge'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { EmptyState } from '@/components/shared/EmptyState'
import { ErrorState } from '@/components/shared/ErrorState'
import { DashboardSkeleton } from '@/components/shared/LoadingState'
import { PageHeader } from '@/components/shared/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { PaginationControls } from '@/components/shared/PaginationControls'

const TYPE_ORDER = [
  'router',
  'switch',
  'firewall',
  'server',
  'access-point',
  'workstation',
  'printer',
  'other',
  'unknown',
]

const TYPE_LABELS: Record<string, string> = {
  router: 'Routers',
  switch: 'Switches',
  firewall: 'Firewalls',
  server: 'Servers',
  'access-point': 'Access points',
  workstation: 'Workstations',
  printer: 'Printers',
  other: 'Other',
  unknown: 'Unknown',
}

function normalizeDeviceType(type: string | null | undefined): string {
  const raw = (type || 'Unknown').trim().toLowerCase()
  if (!raw) return 'unknown'
  return raw.replace(/[\s_]+/g, '-')
}

function formatTypeLabel(key: string): string {
  if (TYPE_LABELS[key]) return TYPE_LABELS[key]
  return key
    .split('-')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function groupDevicesByType(devices: DeviceStatusRow[]) {
  const groups = new Map<string, DeviceStatusRow[]>()
  for (const device of devices) {
    const key = normalizeDeviceType(device.deviceType)
    const list = groups.get(key)
    if (list) list.push(device)
    else groups.set(key, [device])
  }
  return [...groups.entries()]
    .sort(([a], [b]) => {
      const ai = TYPE_ORDER.indexOf(a)
      const bi = TYPE_ORDER.indexOf(b)
      const aRank = ai === -1 ? TYPE_ORDER.length : ai
      const bRank = bi === -1 ? TYPE_ORDER.length : bi
      if (aRank !== bRank) return aRank - bRank
      return formatTypeLabel(a).localeCompare(formatTypeLabel(b))
    })
    .map(([key, items]) => ({
      key,
      label: formatTypeLabel(key),
      devices: items,
    }))
}

function useTrend(current: number | null | undefined) {
  const prev = useRef<number | null>(null)
  const trend =
    current == null || prev.current == null ? null : Math.round(current - prev.current)
  if (current != null) prev.current = current
  return trend
}

function ChartTooltip({ active, payload, label }: {
  active?: boolean
  payload?: Array<{ value: number; name: string; color?: string }>
  label?: string
}) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-border bg-popover px-3 py-2 text-xs shadow-xl">
      {label ? <p className="mb-1 font-medium text-foreground">{label}</p> : null}
      {payload.map((entry) => (
        <p key={entry.name} className="text-muted-foreground">
          <span style={{ color: entry.color }}>{entry.name}</span>: {entry.value}
        </p>
      ))}
    </div>
  )
}

function DeviceTypePanel({ label, devices }: { label: string; devices: DeviceStatusRow[] }) {
  const [isOpen, setIsOpen] = useState(false)
  const pagination = useClientPagination(devices, 10)
  const online = devices.filter((d) => d.status === 'Online').length
  const offline = devices.length - online

  // Show up to 3 device cards
  const displayDevices = devices.slice(0, 3)
  const hasMore = devices.length > 3

  return (
    <>
      <Card 
        className="glass overflow-hidden cursor-pointer transition-all hover:shadow-lg hover:border-primary/50"
        onClick={() => setIsOpen(true)}
      >
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div className="flex-1">
              <CardTitle className="flex items-center gap-2">
                {label}
                <span className="rounded-full bg-primary/15 px-2 py-0.5 text-xs font-semibold text-primary">
                  {devices.length}
                </span>
              </CardTitle>
              <p className="mt-2 text-xs text-muted-foreground">
                <span className="text-success font-semibold">{online}</span> online · <span className="text-orange-700 font-semibold dark:text-orange-500">{offline}</span> offline
              </p>
            </div>
            <ChevronRight className="h-5 w-5 text-muted-foreground shrink-0" />
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          {displayDevices.map((device) => (
            <div
              key={device._id}
              className="flex items-center justify-between rounded-lg border border-border/50 bg-secondary/30 px-3 py-2 hover:bg-secondary/50 transition-colors"
              onClick={(e) => {
                e.stopPropagation()
              }}
            >
              <div className="flex-1 min-w-0">
                <Link
                  to={`/devices/${device._id}`}
                  className="font-medium text-primary hover:underline block truncate"
                  onClick={(e) => e.stopPropagation()}
                >
                  {device.hostname}
                </Link>
                <p className="mono text-xs text-muted-foreground truncate">{device.ipAddress}</p>
              </div>
              <div className="flex items-center gap-2 shrink-0 ml-2">
                <StatusBadge status={device.status} />
                <span className="mono text-xs text-muted-foreground">{formatMs(device.responseTime)}</span>
              </div>
            </div>
          ))}
          {hasMore && (
            <div className="pt-2 text-center">
              <p className="text-xs text-muted-foreground font-medium">
                +{devices.length - 3} more device{devices.length - 3 !== 1 ? 's' : ''}
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={isOpen} onOpenChange={setIsOpen}>
        <DialogContent className="max-w-3xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{label} Devices</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="flex items-center justify-between text-sm">
              <div>
                <span className="text-success font-semibold">{online}</span> online · <span className="text-orange-700 font-semibold dark:text-orange-500">{offline}</span> offline
              </div>
              <span className="text-muted-foreground">Total: {devices.length}</span>
            </div>
            
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Hostname</TableHead>
                  <TableHead>IP</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>RTT</TableHead>
                  <TableHead>Last seen</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pagination.pageItems.map((device) => (
                  <TableRow key={device._id} className="cursor-pointer hover:bg-secondary/50">
                    <TableCell>
                      <Link
                        to={`/devices/${device._id}`}
                        className="font-semibold text-primary hover:underline"
                      >
                        {device.hostname}
                      </Link>
                    </TableCell>
                    <TableCell className="mono text-muted-foreground">{device.ipAddress}</TableCell>
                    <TableCell>
                      <StatusBadge status={device.status} />
                    </TableCell>
                    <TableCell className="mono">{formatMs(device.responseTime)}</TableCell>
                    <TableCell className="text-muted-foreground">{formatRelative(device.lastSeen)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <PaginationControls
              page={pagination.page}
              totalPages={pagination.totalPages}
              total={pagination.total}
              limit={pagination.limit}
              onPageChange={pagination.setPage}
              onLimitChange={pagination.setLimit}
              limitOptions={[10, 25, 50]}
            />
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}

export function DashboardPage() {
  const { isOperator } = useAuth()
  const { theme } = useTheme()
  const dash = useDashboardQuery()
  const { acknowledge, dismiss } = useAlertMutations()
  const health = computeNetworkHealth(dash.summary)

  const onlineTrend = useTrend(dash.summary?.onlineDevices)
  const criticalTrend = useTrend(dash.summary?.criticalOfflineDevices)
  const totalTrend = useTrend(dash.summary?.totalDevices)
  const rttTrend = useTrend(dash.statistics?.averageResponseTime ?? null)

  const deviceGroups = useMemo(() => groupDevicesByType(dash.devices), [dash.devices])
  const chartTotal = dash.statusChart.reduce((sum, entry) => sum + entry.value, 0)

  // This dashboard is device-reachability only — storm alerts belong on the
  // Storm Dashboard / Alerts page's Storm Alerts tab instead.
  const deviceAlerts = useMemo(() => dash.alerts.filter((a) => !isStormAlert(a)), [dash.alerts])

  const alertTrendData = useMemo(() => {
    const buckets = new Map<string, number>()
    for (const alert of deviceAlerts) {
      const day = (alert.createdAt || '').slice(0, 10) || 'unknown'
      buckets.set(day, (buckets.get(day) ?? 0) + 1)
    }
    if (buckets.size === 0 && dash.scanActivity.length) {
      return dash.scanActivity.map((p) => ({ date: p.date, value: p.scans }))
    }
    return [...buckets.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, value]) => ({ date, value }))
  }, [deviceAlerts, dash.scanActivity])

  if (dash.isLoading && !dash.summary) {
    return (
      <div className="space-y-6">
        <PageHeader title="Dashboard" description="Live overview of monitored network devices" />
        <DashboardSkeleton />
      </div>
    )
  }

  if (dash.error && !dash.summary) {
    return (
      <div className="space-y-6">
        <PageHeader title="Dashboard" />
        <ErrorState message={dash.error} onRetry={() => void dash.refetchAll()} />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        description="Live overview of monitored network devices"
        actions={
          <Button type="button" variant="secondary" onClick={() => void dash.refetchAll()}>
            Refresh
          </Button>
        }
      />

      {dash.error ? (
        <ErrorState message={dash.error} onRetry={() => void dash.refetchAll()} className="py-4" />
      ) : null}

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-7">
        <KpiCard
          label="Online Devices"
          value={dash.summary?.onlineDevices ?? 0}
          hint={formatPercent(dash.summary?.onlinePercentage)}
          icon={Wifi}
          tone="success"
          trend={onlineTrend}
        />
        <KpiCard
          label="Offline / Unreachable"
          value={(dash.summary?.notReachableDevices ?? 0) + (dash.summary?.criticalOfflineDevices ?? 0)}
          hint={formatPercent(dash.summary?.notReachablePercentage)}
          icon={WifiOff}
          tone="warning"
        />
        <KpiCard
          label="Critical Devices"
          value={dash.summary?.criticalOfflineDevices ?? 0}
          hint={formatPercent(dash.summary?.criticalOfflinePercentage)}
          icon={AlertTriangle}
          tone="danger"
          trend={criticalTrend}
        />
        <KpiCard
          label="Avg Response"
          value={formatMs(dash.statistics?.averageResponseTime)}
          icon={Timer}
          tone="accent"
          trend={rttTrend}
        />
        <KpiCard
          label="Total Devices"
          value={dash.summary?.totalDevices ?? 0}
          icon={Server}
          tone="accent"
          trend={totalTrend}
        />
        <KpiCard
          label="Active Alerts"
          value={deviceAlerts.length}
          icon={Bell}
          tone={deviceAlerts.length ? 'danger' : 'success'}
        />
        <KpiCard
          label="Health Score"
          value={`${health.score}%`}
          hint={health.label}
          icon={Gauge}
          tone={
            health.label === 'Critical'
              ? 'danger'
              : health.label === 'Warning'
                ? 'warning'
                : health.label === 'Good'
                  ? 'accent'
                  : 'success'
          }
        />
      </section>

      <section className="grid gap-4 lg:grid-cols-3">
        <HealthGauge score={health.score} label={health.label} />
        <Card className="glass lg:col-span-2">
          <CardHeader>
            <CardTitle>Response Time</CardTitle>
          </CardHeader>
          <CardContent>
            {dash.responseTime.length === 0 ? (
              <EmptyState title="No response time data" />
            ) : (
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={dash.responseTime.slice(0, 12)}>
                    <CartesianGrid strokeDasharray="3 3" stroke={theme === 'light' ? '#e2e8f0' : '#334155'} vertical={false} />
                    <XAxis dataKey="hostname" tick={{ fill: theme === 'light' ? '#64748b' : '#94a3b8', fontSize: 11 }} interval={0} angle={-20} textAnchor="end" height={60} />
                    <YAxis tick={{ fill: theme === 'light' ? '#64748b' : '#94a3b8', fontSize: 11 }} unit=" ms" width={56} />
                    <Tooltip content={<ChartTooltip />} />
                    <Bar dataKey="responseTime" name="RTT" fill="#3B82F6" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Card className="glass">
          <CardHeader>
            <CardTitle>Status Distribution</CardTitle>
          </CardHeader>
          <CardContent>
            {chartTotal === 0 ? (
              <EmptyState title="No status data yet" />
            ) : (
              <div className="flex flex-col items-center gap-4 sm:flex-row">
                <div className="h-52 w-full sm:w-1/2">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={dash.statusChart} dataKey="value" nameKey="name" innerRadius={52} outerRadius={78} paddingAngle={3}>
                        {dash.statusChart.map((entry) => (
                          <Cell key={entry.name} fill={STATUS_COLORS[entry.name] ?? '#64748B'} />
                        ))}
                      </Pie>
                      <Tooltip content={<ChartTooltip />} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <ul className="w-full space-y-2 text-sm sm:w-1/2">
                  {dash.statusChart.map((entry) => (
                    <li key={entry.name} className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-2">
                        <span className="h-2.5 w-2.5 rounded-full" style={{ background: STATUS_COLORS[entry.name] ?? '#64748B' }} />
                        {entry.name}
                      </span>
                      <span className="font-semibold">{entry.value}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="glass">
          <CardHeader>
            <CardTitle>Device Types</CardTitle>
          </CardHeader>
          <CardContent>
            {dash.typeChart.length === 0 ? (
              <EmptyState title="No devices yet" />
            ) : (
              <div className="flex flex-col items-center gap-4 sm:flex-row">
                <div className="h-52 w-full sm:w-1/2">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={dash.typeChart} dataKey="value" nameKey="name" outerRadius={78} paddingAngle={2}>
                        {dash.typeChart.map((entry, index) => (
                          <Cell key={entry.name} fill={TYPE_COLORS[index % TYPE_COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip content={<ChartTooltip />} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <ul className="w-full space-y-2 text-sm sm:w-1/2">
                  {dash.typeChart.map((entry, index) => (
                    <li key={entry.name} className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-2">
                        <span className="h-2.5 w-2.5 rounded-full" style={{ background: TYPE_COLORS[index % TYPE_COLORS.length] }} />
                        {entry.name}
                      </span>
                      <span className="font-semibold">{entry.value}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="glass md:col-span-2 xl:col-span-1">
          <CardHeader>
            <CardTitle>Uptime / Scan Activity</CardTitle>
          </CardHeader>
          <CardContent>
            {dash.scanActivity.length === 0 ? (
              <EmptyState title="No scan activity yet" />
            ) : (
              <div className="h-52">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={dash.scanActivity}>
                    <defs>
                      <linearGradient id="scanFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#3B82F6" stopOpacity={0.35} />
                        <stop offset="100%" stopColor="#3B82F6" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke={theme === 'light' ? '#e2e8f0' : '#334155'} vertical={false} />
                    <XAxis dataKey="date" tick={{ fill: theme === 'light' ? '#64748b' : '#94a3b8', fontSize: 11 }} />
                    <YAxis tick={{ fill: theme === 'light' ? '#64748b' : '#94a3b8', fontSize: 11 }} width={36} />
                    <Tooltip content={<ChartTooltip />} />
                    <Area type="monotone" dataKey="scans" name="Scans" stroke="#3B82F6" fill="url(#scanFill)" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 xl:grid-cols-2">
        <Card className="glass">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Active Alerts</CardTitle>
            <Button type="button" variant="ghost" size="sm" asChild>
              <Link to="/alerts">View all</Link>
            </Button>
          </CardHeader>
          <CardContent>
            {deviceAlerts.length === 0 ? (
              <EmptyState title="No active alerts" description="All clear — no outstanding alerts." icon={Activity} />
            ) : (
              <div className="space-y-3">
                {deviceAlerts.map((alert) => (
                  <AlertRow
                    key={alert._id}
                    alert={alert}
                    canAct={isOperator}
                    onAck={() => acknowledge.mutate(alert._id)}
                    onDismiss={() => dismiss.mutate(alert._id)}
                    busy={acknowledge.isPending || dismiss.isPending}
                  />
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="glass">
          <CardHeader>
            <CardTitle>Alerts / Activity Trend</CardTitle>
          </CardHeader>
          <CardContent>
            {alertTrendData.length === 0 ? (
              <EmptyState title="No trend data" />
            ) : (
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={alertTrendData}>
                    <CartesianGrid strokeDasharray="3 3" stroke={theme === 'light' ? '#e2e8f0' : '#334155'} vertical={false} />
                    <XAxis dataKey="date" tick={{ fill: theme === 'light' ? '#64748b' : '#94a3b8', fontSize: 11 }} />
                    <YAxis tick={{ fill: theme === 'light' ? '#64748b' : '#94a3b8', fontSize: 11 }} width={36} />
                    <Tooltip content={<ChartTooltip />} />
                    <Area type="monotone" dataKey="value" name="Count" stroke="#EF4444" fill="#EF444433" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>
      </section>

      <section className="space-y-3">
        <div>
          <h2 className="text-lg font-semibold">Devices</h2>
          <p className="text-sm text-muted-foreground">Grouped by type — click a card to view all devices</p>
        </div>
        {dash.devices.length === 0 ? (
          <Card className="glass">
            <EmptyState title="No devices" description="Add devices or run discovery." />
          </Card>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
            {deviceGroups.map((group) => (
              <DeviceTypePanel key={group.key} label={group.label} devices={group.devices} />
            ))}
          </div>
        )}
      </section>

      <Card className="glass">
        <CardHeader>
          <CardTitle>Recent Scans</CardTitle>
        </CardHeader>
        <CardContent>
          {dash.history.length === 0 ? (
            <EmptyState title="No history yet" />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Host</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>When</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {dash.history.map((row) => (
                  <TableRow key={row._id}>
                    <TableCell>
                      <div>
                        <p className="font-semibold">{row.hostname}</p>
                        <p className="mono text-xs text-muted-foreground">{row.ipAddress}</p>
                      </div>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={row.status} />
                    </TableCell>
                    <TableCell>{row.scanType}</TableCell>
                    <TableCell className="text-muted-foreground">{formatDateTime(row.timestamp)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function AlertRow({
  alert,
  canAct,
  onAck,
  onDismiss,
  busy,
}: {
  alert: AlertItem
  canAct: boolean
  onAck: () => void
  onDismiss: () => void
  busy: boolean
}) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border/70 bg-secondary/30 p-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-semibold">{alert.hostname}</p>
          <StatusBadge status={alert.status} pulse={false} />
        </div>
        <p className="mt-1 text-sm text-muted-foreground">{alert.message}</p>
        <p className="mono mt-1 text-xs text-muted-foreground">
          {alert.ipAddress} · {formatDateTime(alert.createdAt)}
        </p>
      </div>
      {canAct ? (
        <div className="flex shrink-0 gap-2">
          <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={onAck}>
            <Check className="h-3.5 w-3.5" />
            Ack
          </Button>
          <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onDismiss}>
            <X className="h-3.5 w-3.5" />
            Dismiss
          </Button>
        </div>
      ) : null}
    </div>
  )
}
