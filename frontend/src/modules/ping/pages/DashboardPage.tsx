import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  Activity,
  ArrowRight,
  Bell,
  Database,
  FileBarChart,
  Gauge,
  Network,
  Radar,
  RefreshCw,
  Server,
  Settings,
  Shield,
  ShieldAlert,
  Timer,
  Wifi,
  WifiOff,
} from 'lucide-react'
import { useAuth } from '@/shared/auth/AuthContext'
import { useDashboardQuery, useHealthQuery } from '@/hooks/queries'
import { computeNetworkHealth, healthColor } from '@/lib/health'
import { formatDateTime, formatMs, formatPercent, formatRelative } from '@/utils/format'
import type { AlertItem, PingHistory } from '@/types'
import { HealthGauge } from '@/shared/components/HealthGauge'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { DashboardSkeleton } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { SectionHeading } from '@/shared/components/SectionHeading'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/ui/card'
import { cn } from '@/lib/utils'

function isStormAlert(alert: AlertItem): boolean {
  const category = (alert.category || '').toLowerCase()
  const alertType = (alert.alertType || '').toLowerCase()
  const message = (alert.message || '').toLowerCase()
  return (
    Boolean(alert.incidentId) ||
    category.includes('storm') ||
    alertType.includes('storm') ||
    alertType.includes('mitigation') ||
    alertType.includes('recovery') ||
    message.includes('storm') ||
    message.includes('mitigation') ||
    message.includes('recovery')
  )
}

function alertSeverityRank(alert: AlertItem): number {
  const severity = (alert.severity || '').toUpperCase()
  if (severity === 'CRITICAL' || severity === 'EMERGENCY') return 4
  if (severity === 'HIGH') return 3
  if (severity === 'MEDIUM' || severity === 'WARNING') return 2
  if (alert.status === 'Offline (Critical)' || alert.status === 'Offline') return 3
  if (alert.status === 'Not Reachable') return 2
  return 1
}

function isHighPriorityAlert(alert: AlertItem): boolean {
  return alertSeverityRank(alert) >= 3
}

type ActivityItem = {
  id: string
  kind: 'status' | 'storm' | 'alert' | 'scan'
  title: string
  detail: string
  timestamp: string
  href?: string
}

function buildRecentActivity(
  history: PingHistory[],
  alerts: AlertItem[],
): ActivityItem[] {
  const items: ActivityItem[] = []

  for (const row of history) {
    items.push({
      id: `hist-${row._id}`,
      kind: row.status === 'Online' ? 'scan' : 'status',
      title: `${row.hostname} · ${row.status}`,
      detail: `${row.ipAddress} · ${row.scanType}${row.responseTime != null ? ` · ${formatMs(row.responseTime)}` : ''}`,
      timestamp: row.timestamp,
      href: row.deviceId ? `/devices/${row.deviceId}` : '/history',
    })
  }

  for (const alert of alerts) {
    const storm = isStormAlert(alert)
    items.push({
      id: `alert-${alert._id}`,
      kind: storm ? 'storm' : 'alert',
      title: alert.title || alert.message || `${alert.hostname} alert`,
      detail: [
        alert.hostname,
        alert.interface,
        alert.incidentId ? `Incident ${alert.incidentId}` : null,
        alert.action,
      ]
        .filter(Boolean)
        .join(' · '),
      timestamp: alert.createdAt,
      href: storm && alert.incidentId
        ? `/storm?incident=${encodeURIComponent(alert.incidentId)}`
        : storm
          ? '/storm'
          : '/alerts',
    })
  }

  return items
    .filter((item) => Boolean(item.timestamp))
    .sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp)))
    .slice(0, 8)
}

const fadeUp = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0 },
}

export function DashboardPage() {
  const { isAdmin } = useAuth()
  const dash = useDashboardQuery()
  const healthQuery = useHealthQuery()
  const health = computeNetworkHealth(dash.summary)

  const offlineCount =
    (dash.summary?.notReachableDevices ?? 0) + (dash.summary?.criticalOfflineDevices ?? 0)

  const stormMetrics = useMemo(() => {
    const stormAlerts = dash.alerts.filter(isStormAlert)
    const incidentIds = new Set(
      stormAlerts.map((a) => a.incidentId).filter((id): id is string => Boolean(id)),
    )
    const riskScores = stormAlerts
      .map((a) => a.riskScore)
      .filter((n): n is number => typeof n === 'number' && !Number.isNaN(n))
    const peakRisk = riskScores.length ? Math.max(...riskScores) : null
    const managedSwitches = dash.devices.filter(
      (d) => (d.deviceType || '').trim().toLowerCase().includes('switch'),
    ).length
    const monitoredSwitches = dash.devices.filter(
      (d) =>
        (d.deviceType || '').trim().toLowerCase().includes('switch') && d.monitor,
    ).length

    return {
      activeIncidents: incidentIds.size || stormAlerts.filter((a) => Boolean(a.incidentId)).length,
      stormAlerts: stormAlerts.length,
      peakRisk,
      managedSwitches,
      monitoredSwitches,
    }
  }, [dash.alerts, dash.devices])

  const recentActivity = useMemo(
    () => buildRecentActivity(dash.history, dash.alerts),
    [dash.history, dash.alerts],
  )

  const criticalAlerts = useMemo(() => {
    return [...dash.alerts]
      .filter(isHighPriorityAlert)
      .sort((a, b) => alertSeverityRank(b) - alertSeverityRank(a))
      .slice(0, 5)
  }, [dash.alerts])

  const apiOk = healthQuery.isError ? false : healthQuery.data ? true : null
  const dbOk = healthQuery.data
    ? healthQuery.data.database === 'Connected'
    : healthQuery.isError
      ? false
      : null
  const lastRefresh = dash.dataUpdatedAt
    ? formatRelative(new Date(dash.dataUpdatedAt).toISOString())
    : null

  if (dash.isLoading && !dash.summary) {
    return (
      <div className="np-page">
        <PageHeader
          title="Enterprise Dashboard"
          description="Choose a workspace or monitor the overall health of your network."
        />
        <DashboardSkeleton />
      </div>
    )
  }

  if (dash.error && !dash.summary) {
    return (
      <div className="np-page">
        <PageHeader title="Enterprise Dashboard" />
        <ErrorState message={dash.error} onRetry={() => void dash.refetchAll()} />
      </div>
    )
  }

  return (
    <motion.div
      className="np-page"
      initial="hidden"
      animate="show"
      variants={{ show: { transition: { staggerChildren: 0.06 } } }}
    >
      <motion.div variants={fadeUp}>
        <PageHeader
          title="Enterprise Dashboard"
          description="Choose a workspace or monitor the overall health of your network."
          actions={
            <Button type="button" variant="secondary" onClick={() => void dash.refetchAll()}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
          }
        />
      </motion.div>

      {dash.error ? (
        <ErrorState message={dash.error} onRetry={() => void dash.refetchAll()} className="py-4" />
      ) : null}

      {/* Row 1 — Workspace launchers */}
      <motion.section
        variants={fadeUp}
        className="grid gap-6 lg:grid-cols-2"
        aria-label="Workspaces"
      >
        <WorkspaceCard
          title="Ping Monitoring"
          description="Device reachability, discovery, history, and uptime reporting."
          icon={Activity}
          accent="from-sky-500/20 via-primary/10 to-transparent"
          iconClass="bg-sky-500/15 text-sky-400"
          metrics={[
            {
              label: 'Total Devices',
              value: String(dash.summary?.totalDevices ?? 0),
              icon: Server,
            },
            {
              label: 'Online',
              value: String(dash.summary?.onlineDevices ?? 0),
              icon: Wifi,
              tone: 'success',
            },
            {
              label: 'Offline',
              value: String(offlineCount),
              icon: WifiOff,
              tone: offlineCount > 0 ? 'warning' : 'default',
            },
            {
              label: 'Avg Response',
              value: formatMs(dash.statistics?.averageResponseTime),
              icon: Timer,
            },
            {
              label: 'Availability',
              value: formatPercent(dash.summary?.onlinePercentage ?? dash.statistics?.onlinePercentage),
              icon: Gauge,
              tone: 'accent',
            },
          ]}
          ctaLabel="Open Ping Monitoring"
          ctaTo="/devices"
        />

        <WorkspaceCard
          title="Storm Protection"
          description="Switch interface protection, risk scoring, incidents, and recovery."
          icon={Shield}
          accent="from-violet-500/20 via-fuchsia-500/5 to-transparent"
          iconClass="bg-violet-500/15 text-violet-300"
          metrics={[
            {
              label: 'Active Incidents',
              value: String(stormMetrics.activeIncidents),
              icon: ShieldAlert,
              tone: stormMetrics.activeIncidents > 0 ? 'danger' : 'success',
            },
            {
              label: 'Storm Alerts',
              value: String(stormMetrics.stormAlerts),
              icon: Bell,
              tone: stormMetrics.stormAlerts > 0 ? 'warning' : 'default',
            },
            {
              label: 'Peak Risk Score',
              value:
                stormMetrics.peakRisk == null ? '—' : stormMetrics.peakRisk.toFixed(0),
              icon: Gauge,
              tone:
                stormMetrics.peakRisk != null && stormMetrics.peakRisk >= 70
                  ? 'danger'
                  : 'default',
            },
            {
              label: 'Managed Switches',
              value: String(stormMetrics.managedSwitches),
              icon: Network,
            },
            {
              label: 'Monitored Switches',
              value: String(stormMetrics.monitoredSwitches),
              icon: Activity,
              tone: 'accent',
            },
          ]}
          ctaLabel="Open Storm Protection"
          ctaTo="/storm"
        />
      </motion.section>

      {/* Row 2 — Overall Network Health */}
      <motion.section variants={fadeUp} className="space-y-4" aria-label="Network health">
        <SectionHeading
          title="Overall Network Health"
          description="Platform posture derived from live monitoring data."
        />
        <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
          <HealthGauge summary={dash.summary} />
          <Card className="glass">
            <CardHeader>
              <CardTitle className="text-base">System Status</CardTitle>
              <CardDescription>Backend and data-plane connectivity</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <StatusTile
                  label="Network Health Score"
                  value={`${health.score}%`}
                  hint={health.label}
                  color={healthColor(health.label)}
                />
                <StatusTile
                  label="System Status"
                  value={
                    dash.error
                      ? 'Degraded'
                      : health.label === 'Critical'
                        ? 'Critical'
                        : health.label === 'Warning'
                          ? 'Attention'
                          : 'Operational'
                  }
                  hint={dash.error ? 'Dashboard data incomplete' : 'Monitoring active'}
                  ok={!dash.error && health.label !== 'Critical'}
                />
                <StatusTile
                  label="Backend Health"
                  value={apiOk === null ? 'Checking…' : apiOk ? 'Online' : 'Down'}
                  hint="API service"
                  ok={apiOk}
                />
                <StatusTile
                  label="Database"
                  value={dbOk === null ? 'Checking…' : dbOk ? 'Connected' : 'Disconnected'}
                  hint="Persistence layer"
                  ok={dbOk}
                  icon={Database}
                />
              </div>
              <p className="mt-4 text-xs text-muted-foreground">
                Last refresh:{' '}
                <span className="font-medium text-foreground">{lastRefresh ?? '—'}</span>
              </p>
            </CardContent>
          </Card>
        </div>
      </motion.section>

      {/* Row 3 — Recent Activity */}
      <motion.section variants={fadeUp} className="space-y-4" aria-label="Recent activity">
        <SectionHeading
          title="Recent Activity"
          description="Latest device status changes, scans, and storm-related events."
        />
        <Card className="glass">
          <CardContent className="p-0">
            {recentActivity.length === 0 ? (
              <div className="p-6">
                <EmptyState title="No recent activity" description="History and alerts will appear here." />
              </div>
            ) : (
              <ul className="divide-y divide-border/60">
                {recentActivity.map((item) => (
                  <li key={item.id}>
                    <Link
                      to={item.href || '/'}
                      className="flex items-start gap-3 px-5 py-3.5 transition-colors hover:bg-secondary/40"
                    >
                      <span
                        className={cn(
                          'mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg',
                          item.kind === 'storm' && 'bg-violet-500/15 text-violet-300',
                          item.kind === 'alert' && 'bg-danger/15 text-danger',
                          item.kind === 'status' && 'bg-warning/15 text-warning',
                          item.kind === 'scan' && 'bg-success/15 text-success',
                        )}
                      >
                        {item.kind === 'storm' ? (
                          <Shield className="h-4 w-4" />
                        ) : item.kind === 'alert' ? (
                          <Bell className="h-4 w-4" />
                        ) : item.kind === 'status' ? (
                          <WifiOff className="h-4 w-4" />
                        ) : (
                          <Activity className="h-4 w-4" />
                        )}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold">{item.title}</p>
                        <p className="truncate text-xs text-muted-foreground">{item.detail}</p>
                      </div>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {formatRelative(item.timestamp)}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </motion.section>

      {/* Row 4 — Critical Alerts */}
      <motion.section variants={fadeUp} className="space-y-4" aria-label="Critical alerts">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <SectionHeading
            title="Critical Alerts"
            description="Highest-priority active alerts requiring attention."
          />
          <Button type="button" variant="ghost" size="sm" asChild>
            <Link to="/alerts">
              View all alerts
              <ArrowRight className="ml-1 h-3.5 w-3.5" />
            </Link>
          </Button>
        </div>
        <Card className="glass">
          <CardContent className="p-0">
            {criticalAlerts.length === 0 ? (
              <div className="p-6">
                <EmptyState
                  title="No critical alerts"
                  description="No high-severity alerts are currently active."
                  icon={Bell}
                />
              </div>
            ) : (
              <ul className="divide-y divide-border/60">
                {criticalAlerts.map((alert) => (
                  <li
                    key={alert._id}
                    className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div className="min-w-0 space-y-1.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <SeverityChip alert={alert} />
                        {isStormAlert(alert) ? (
                          <Badge variant="secondary" className="uppercase tracking-wide">
                            Storm
                          </Badge>
                        ) : (
                          <Badge variant="outline" className="uppercase tracking-wide">
                            Ping
                          </Badge>
                        )}
                        <StatusBadge status={alert.status} pulse={false} />
                      </div>
                      <p className="truncate text-sm font-semibold">
                        {alert.title || alert.message || alert.hostname}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        Source: {alert.hostname}
                        {alert.interface ? ` · ${alert.interface}` : ''}
                        {alert.ipAddress ? ` · ${alert.ipAddress}` : ''}
                        {' · '}
                        {formatDateTime(alert.createdAt)}
                      </p>
                    </div>
                    <Button type="button" size="sm" variant="secondary" asChild>
                      <Link
                        to={
                          isStormAlert(alert) && alert.incidentId
                            ? `/storm?incident=${encodeURIComponent(alert.incidentId)}`
                            : isStormAlert(alert)
                              ? '/storm'
                              : '/alerts'
                        }
                      >
                        Quick View
                      </Link>
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </motion.section>

      {/* Row 5 — Quick Actions */}
      <motion.section variants={fadeUp} className="space-y-4" aria-label="Quick actions">
        <SectionHeading
          title="Quick Actions"
          description="Jump into common operational workflows."
        />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          <QuickAction to="/devices" icon={Server} label="Scan Devices" hint="Open device inventory" />
          {isAdmin ? (
            <QuickAction to="/discovery" icon={Radar} label="Discover Network" hint="Subnet discovery" />
          ) : null}
          <QuickAction to="/interfaces" icon={Network} label="Open Interfaces" hint="Switch port inventory" />
          <QuickAction to="/reports" icon={FileBarChart} label="Open Reports" hint="Exports & uptime" />
          {isAdmin ? (
            <QuickAction to="/settings" icon={Settings} label="Open Settings" hint="Platform configuration" />
          ) : null}
        </div>
      </motion.section>
    </motion.div>
  )
}

function WorkspaceCard({
  title,
  description,
  icon: Icon,
  accent,
  iconClass,
  metrics,
  ctaLabel,
  ctaTo,
}: {
  title: string
  description: string
  icon: typeof Activity
  accent: string
  iconClass: string
  metrics: Array<{
    label: string
    value: string
    icon: typeof Activity
    tone?: 'default' | 'success' | 'warning' | 'danger' | 'accent'
  }>
  ctaLabel: string
  ctaTo: string
}) {
  return (
    <motion.div whileHover={{ y: -3 }} transition={{ type: 'spring', stiffness: 380, damping: 28 }}>
      <Card className="glass relative h-full overflow-hidden border-border/70 shadow-sm transition-shadow hover:shadow-lg hover:shadow-primary/5">
        <div className={cn('pointer-events-none absolute inset-0 bg-gradient-to-br', accent)} />
        <CardHeader className="relative space-y-4 pb-4">
          <div className="flex items-start justify-between gap-3">
            <div className="space-y-1.5">
              <CardTitle className="text-xl tracking-tight">{title}</CardTitle>
              <CardDescription className="max-w-md text-sm leading-relaxed">
                {description}
              </CardDescription>
            </div>
            <div className={cn('flex h-12 w-12 shrink-0 items-center justify-center rounded-xl', iconClass)}>
              <Icon className="h-6 w-6" />
            </div>
          </div>
        </CardHeader>
        <CardContent className="relative flex h-[calc(100%-7rem)] flex-col justify-between gap-6">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-2 xl:grid-cols-3">
            {metrics.map((metric) => (
              <div
                key={metric.label}
                className="rounded-xl border border-border/60 bg-card/70 px-3 py-3 backdrop-blur-sm"
              >
                <div className="flex items-center gap-2 text-muted-foreground">
                  <metric.icon className="h-3.5 w-3.5" />
                  <p className="text-[10px] font-semibold uppercase tracking-wider">{metric.label}</p>
                </div>
                <p
                  className={cn(
                    'mt-1.5 text-lg font-bold tracking-tight',
                    metric.tone === 'success' && 'text-success',
                    metric.tone === 'warning' && 'text-warning',
                    metric.tone === 'danger' && 'text-danger',
                    metric.tone === 'accent' && 'text-primary',
                  )}
                >
                  {metric.value}
                </p>
              </div>
            ))}
          </div>
          <Button type="button" className="w-full sm:w-auto" asChild>
            <Link to={ctaTo}>
              {ctaLabel}
              <ArrowRight className="ml-2 h-4 w-4" />
            </Link>
          </Button>
        </CardContent>
      </Card>
    </motion.div>
  )
}

function StatusTile({
  label,
  value,
  hint,
  ok,
  color,
  icon: Icon,
}: {
  label: string
  value: string
  hint?: string
  ok?: boolean | null
  color?: string
  icon?: typeof Database
}) {
  return (
    <div className="rounded-xl border border-border/60 bg-secondary/20 p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {label}
        </p>
        {Icon ? <Icon className="h-3.5 w-3.5 text-muted-foreground" /> : null}
      </div>
      <p className="mt-2 text-lg font-bold tracking-tight" style={color ? { color } : undefined}>
        <span
          className={cn(
            !color && ok === true && 'text-success',
            !color && ok === false && 'text-danger',
          )}
        >
          {value}
        </span>
      </p>
      {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  )
}

function SeverityChip({ alert }: { alert: AlertItem }) {
  const severity = (alert.severity || '').toUpperCase()
  const label = severity || (alert.status === 'Offline (Critical)' ? 'CRITICAL' : 'HIGH')
  const variant =
    label === 'CRITICAL' || label === 'EMERGENCY'
      ? 'danger'
      : label === 'HIGH'
        ? 'warning'
        : 'secondary'
  return (
    <Badge variant={variant} className="font-semibold uppercase tracking-wide">
      {label}
    </Badge>
  )
}

function QuickAction({
  to,
  icon: Icon,
  label,
  hint,
}: {
  to: string
  icon: typeof Server
  label: string
  hint: string
}) {
  return (
    <Link to={to} className="group block h-full">
      <Card className="glass h-full transition-all duration-200 group-hover:-translate-y-0.5 group-hover:border-primary/40 group-hover:shadow-md">
        <CardContent className="flex h-full flex-col gap-3 p-4">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary transition-colors group-hover:bg-primary/15">
            <Icon className="h-5 w-5" />
          </div>
          <div>
            <p className="text-sm font-semibold">{label}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>
          </div>
          <span className="mt-auto inline-flex items-center text-xs font-medium text-primary opacity-80 group-hover:opacity-100">
            Open
            <ArrowRight className="ml-1 h-3.5 w-3.5" />
          </span>
        </CardContent>
      </Card>
    </Link>
  )
}
