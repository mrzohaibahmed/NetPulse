import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Activity,
  AlertTriangle,
  Bell,
  Check,
  CheckCircle2,
  CloudLightning,
  Database,
  Network,
  Radar,
  RefreshCw,
  Search,
  Server,
  Shield,
  WifiOff,
  X,
} from 'lucide-react'
import { useAuth } from '@/shared/auth/AuthContext'
import { useAlertMutations, useAlertsQuery, useHealthQuery } from '@/hooks/queries'
import { useClientPagination } from '@/hooks/useClientPagination'
import { formatDateTime, formatRelative } from '@/utils/format'
import type { AlertItem } from '@/types'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { KpiCard } from '@/shared/components/KpiCard'
import { TableSkeleton } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/ui/card'
import { Input } from '@/shared/ui/input'
import { cn } from '@/lib/utils'

type ApiStatus = 'active' | 'acknowledged' | 'dismissed' | 'all'
type QuickFilter = 'all' | 'critical' | 'warning' | 'storm' | 'devices' | 'acknowledged' | 'active'

function isStormAlert(alert: AlertItem): boolean {
  const category = (alert.category || alert.alertType || alert.scanType || '').toLowerCase()
  return (
    Boolean(alert.incidentId) ||
    category.includes('storm') ||
    category.includes('mitigation') ||
    category.includes('recovery')
  )
}

/**
 * Returns true when the alert carries a valid device + interface reference
 * that can be used to navigate to the Interface Detail page.
 */
function hasInterfaceReference(alert: AlertItem): boolean {
  if (!alert.deviceId) return false
  const iface = (alert.interface ?? '').trim()
  return iface.length > 0 && iface !== 'unknown' && iface !== '—'
}

/** Build the existing `/interfaces/:deviceId/:interfaceName` route path. */
function interfaceDetailPath(alert: AlertItem): string {
  return `/interfaces/${alert.deviceId}/${encodeURIComponent(alert.interface!)}`
}

function alertSeverityTone(alert: AlertItem): 'danger' | 'warning' | 'info' | 'default' {
  const severity = (alert.severity || '').toUpperCase()
  if (severity === 'CRITICAL' || severity === 'EMERGENCY') return 'danger'
  if (severity === 'WARNING' || severity === 'HIGH' || severity === 'MEDIUM') return 'warning'
  if (severity === 'INFO') return 'info'

  if (alert.status === 'Offline (Critical)' || alert.status === 'Offline') return 'danger'
  if (alert.status === 'Not Reachable') return 'warning'
  if (alert.status === 'MITIGATION_FAILED' || alert.status === 'RECOVERY_FAILED') return 'danger'
  if (alert.status === 'MITIGATED') return 'danger'
  if (alert.status === 'RECOVERED' || alert.status === 'MONITORING') return 'info'
  return 'default'
}

function isCriticalAlert(alert: AlertItem): boolean {
  const severity = (alert.severity || '').toUpperCase()
  return (
    severity === 'CRITICAL' ||
    severity === 'EMERGENCY' ||
    alert.status === 'Offline (Critical)' ||
    alert.status === 'Offline' ||
    alert.status === 'MITIGATION_FAILED' ||
    alert.status === 'RECOVERY_FAILED'
  )
}

function isWarningAlert(alert: AlertItem): boolean {
  if (isCriticalAlert(alert)) return false
  const severity = (alert.severity || '').toUpperCase()
  return (
    severity === 'WARNING' ||
    severity === 'HIGH' ||
    severity === 'MEDIUM' ||
    alert.status === 'Not Reachable' ||
    alertSeverityTone(alert) === 'warning'
  )
}

function SeverityBadge({ severity }: { severity: string }) {
  const value = severity.toUpperCase()
  const variant =
    value === 'CRITICAL' || value === 'EMERGENCY'
      ? 'danger'
      : value === 'WARNING' || value === 'HIGH'
        ? 'warning'
        : value === 'INFO'
          ? 'success'
          : 'secondary'
  return (
    <Badge variant={variant} className="font-semibold uppercase tracking-wide">
      {value}
    </Badge>
  )
}

function alertCategoryLabel(alert: AlertItem): string {
  return alert.category || alert.alertType || alert.scanType || (isStormAlert(alert) ? 'Storm' : 'Device')
}

type OpsActivity = {
  id: string
  kind: 'mitigation' | 'recovery' | 'discovery' | 'device' | 'alert'
  title: string
  detail: string
  timestamp: string
  href?: string
}

function buildRecentOperations(alerts: AlertItem[]): OpsActivity[] {
  const items: OpsActivity[] = []

  for (const alert of alerts) {
    const action = (alert.action || '').toLowerCase()
    const status = (alert.status || '').toUpperCase()
    const storm = isStormAlert(alert)
    let kind: OpsActivity['kind'] = storm ? 'alert' : 'device'

    if (action.includes('mitigat') || status.includes('MITIGAT')) kind = 'mitigation'
    else if (action.includes('recover') || status.includes('RECOVER') || status === 'MONITORING')
      kind = 'recovery'
    else if ((alert.scanType || '').toLowerCase().includes('discover')) kind = 'discovery'
    else if (storm) kind = 'alert'
    else kind = 'device'

    items.push({
      id: `alert-${alert._id}`,
      kind,
      title: alert.title || alert.message || `${alert.hostname} alert`,
      detail: [alert.hostname, alert.interface, alert.incidentId, alert.action]
        .filter(Boolean)
        .join(' · '),
      timestamp: alert.createdAt,
      href:
        storm && alert.incidentId
          ? `/storm?incident=${encodeURIComponent(alert.incidentId)}`
          : storm
            ? '/storm'
            : alert.deviceId
              ? `/devices/${alert.deviceId}`
              : '/alerts',
    })
  }

  return items
    .filter((i) => Boolean(i.timestamp))
    .sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp)))
    .slice(0, 10)
}

const QUICK_FILTERS: Array<{ id: QuickFilter; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'critical', label: 'Critical' },
  { id: 'warning', label: 'Warnings' },
  { id: 'storm', label: 'Storm' },
  { id: 'devices', label: 'Devices' },
  { id: 'acknowledged', label: 'Acknowledged' },
  { id: 'active', label: 'Active' },
]

export function AlertsPage() {
  const { isUser } = useAuth()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const initialFilter = searchParams.get('filter')
  const [status, setStatus] = useState<ApiStatus>(() =>
    initialFilter === 'acknowledged'
      ? 'acknowledged'
      : initialFilter === 'dismissed'
        ? 'dismissed'
        : initialFilter === 'all'
          ? 'all'
          : 'active',
  )
  const [quickFilter, setQuickFilter] = useState<QuickFilter>(() => {
    const allowed: QuickFilter[] = [
      'all',
      'critical',
      'warning',
      'storm',
      'devices',
      'acknowledged',
      'active',
    ]
    return initialFilter && allowed.includes(initialFilter as QuickFilter)
      ? (initialFilter as QuickFilter)
      : initialFilter === 'dismissed'
        ? 'all'
        : 'active'
  })
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const alertsQuery = useAlertsQuery(status, 100)
  const acknowledgedTotalsQuery = useAlertsQuery('acknowledged', 1)
  const dismissedTotalsQuery = useAlertsQuery('dismissed', 1)
  const { acknowledge, dismiss } = useAlertMutations()
  const healthQuery = useHealthQuery()

  const alerts = alertsQuery.data?.data ?? []
  const apiTotal = alertsQuery.data?.total ?? alerts.length
  const breakdownIsPartial = apiTotal > alerts.length

  const kpis = useMemo(() => {
    const critical = alerts.filter(isCriticalAlert).length
    const warning = alerts.filter(isWarningAlert).length
    const storm = alerts.filter(isStormAlert).length
    const device = alerts.length - storm
    return {
      total: apiTotal,
      critical,
      warning,
      acknowledged: acknowledgedTotalsQuery.data?.total ?? 0,
      dismissed: dismissedTotalsQuery.data?.total ?? 0,
      storm,
      device: Math.max(0, device),
      breakdownIsPartial,
      breakdownSampleSize: alerts.length,
    }
  }, [alerts, apiTotal, acknowledgedTotalsQuery.data?.total, dismissedTotalsQuery.data?.total, breakdownIsPartial])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return alerts.filter((a) => {
      if (quickFilter === 'critical' && !isCriticalAlert(a)) return false
      if (quickFilter === 'warning' && !isWarningAlert(a)) return false
      if (quickFilter === 'storm' && !isStormAlert(a)) return false
      if (quickFilter === 'devices' && isStormAlert(a)) return false
      if (quickFilter === 'acknowledged' && !a.acknowledged) return false
      if (quickFilter === 'active' && a.dismissed) return false

      if (!q) return true
      return (
        a.hostname.toLowerCase().includes(q) ||
        a.ipAddress.toLowerCase().includes(q) ||
        a.message.toLowerCase().includes(q) ||
        (a.title || '').toLowerCase().includes(q) ||
        (a.interface || '').toLowerCase().includes(q) ||
        (a.incidentId || '').toLowerCase().includes(q) ||
        (a.category || '').toLowerCase().includes(q)
      )
    })
  }, [alerts, query, quickFilter])

  const pagination = useClientPagination(filtered, 25)
  const { reset: resetPagination } = pagination

  useEffect(() => {
    resetPagination()
  }, [query, status, quickFilter, resetPagination])

  useEffect(() => {
    if (!selectedId && pagination.pageItems[0]) {
      setSelectedId(pagination.pageItems[0]._id)
      return
    }
    if (selectedId && !filtered.some((a) => a._id === selectedId)) {
      setSelectedId(pagination.pageItems[0]?._id ?? null)
    }
  }, [filtered, pagination.pageItems, selectedId])

  useEffect(() => {
    if (!searchParams.get('filter')) return
    const timer = window.setTimeout(() => {
      document.getElementById('alert-stream-section')?.scrollIntoView({ behavior: 'smooth' })
    }, 80)
    return () => window.clearTimeout(timer)
  }, [searchParams])

  const selected = useMemo(
    () => filtered.find((a) => a._id === selectedId) ?? null,
    [filtered, selectedId],
  )

  const recentOps = useMemo(() => buildRecentOperations(alerts), [alerts])

  const apiOk = healthQuery.isError ? false : healthQuery.data ? true : null
  const dbOk = healthQuery.data
    ? healthQuery.data.database === 'Connected'
    : healthQuery.isError
      ? false
      : null
  const lastRefresh = alertsQuery.dataUpdatedAt
    ? formatRelative(new Date(alertsQuery.dataUpdatedAt).toISOString())
    : null

  const onQuickFilter = (id: QuickFilter) => {
    setQuickFilter(id)
    if (id === 'acknowledged') setStatus('acknowledged')
    else if (id === 'active') setStatus('active')
    else if (id === 'all') setStatus('all')
    // critical/warning/storm/devices keep current API status (prefer all for broader pool)
    else if (status === 'acknowledged' || status === 'dismissed') setStatus('all')
  }

  const scrollToAlertStream = () => {
    document.getElementById('alert-stream-section')?.scrollIntoView({ behavior: 'smooth' })
  }

  const handleKpiClick = (id: QuickFilter | 'dismissed') => {
    if (id === 'dismissed') {
      setQuickFilter('all')
      setStatus('dismissed')
      const next = new URLSearchParams(searchParams)
      next.set('filter', 'dismissed')
      setSearchParams(next, { replace: true })
    } else {
      onQuickFilter(id)
      const next = new URLSearchParams(searchParams)
      next.set('filter', id)
      setSearchParams(next, { replace: true })
    }
    scrollToAlertStream()
  }

  const openStorm = (alert: AlertItem) => {
    if (!isStormAlert(alert)) return
    if (alert.incidentId) {
      navigate(`/storm?incident=${encodeURIComponent(alert.incidentId)}`)
    } else {
      navigate('/storm')
    }
  }

  const openInterface = (alert: AlertItem) => {
    if (!hasInterfaceReference(alert)) return
    navigate(interfaceDetailPath(alert))
  }

  return (
    <div className="np-page">
      <PageHeader
        title="Operations Center"
        description="Real-time monitoring of network events, alerts and system health."
        actions={
          <Button type="button" variant="secondary" onClick={() => void alertsQuery.refetch()}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        }
      />

      {/* KPI row */}
      <section className="space-y-2" aria-label="Alert KPIs">
        {kpis.breakdownIsPartial ? (
          <p className="text-xs text-muted-foreground">
            Critical, warning, storm, and device counts are from the first {kpis.breakdownSampleSize}{' '}
            loaded alerts. Total, acknowledged, and dismissed use server totals.
          </p>
        ) : null}
        <div className="grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-7">
        <KpiCard
          label="Total Alerts"
          value={kpis.total}
          icon={Bell}
          tone="accent"
          onClick={() => handleKpiClick('all')}
        />
        <KpiCard
          label="Critical"
          value={kpis.critical}
          icon={AlertTriangle}
          tone={kpis.critical ? 'danger' : 'default'}
          onClick={() => handleKpiClick('critical')}
        />
        <KpiCard
          label="Warning"
          value={kpis.warning}
          icon={AlertTriangle}
          tone={kpis.warning ? 'warning' : 'default'}
          onClick={() => handleKpiClick('warning')}
        />
        <KpiCard
          label="Acknowledged"
          value={kpis.acknowledged}
          icon={CheckCircle2}
          tone="success"
          onClick={() => handleKpiClick('acknowledged')}
        />
        <KpiCard
          label="Dismissed"
          value={kpis.dismissed}
          icon={X}
          tone="default"
          onClick={() => handleKpiClick('dismissed')}
        />
        <KpiCard
          label="Storm Events"
          value={kpis.storm}
          icon={CloudLightning}
          tone={kpis.storm ? 'warning' : 'default'}
          onClick={() => handleKpiClick('storm')}
        />
        <KpiCard
          label="Device Events"
          value={kpis.device}
          icon={Server}
          tone="accent"
          onClick={() => handleKpiClick('devices')}
        />
        </div>
      </section>
      <section aria-label="System status">
        <Card className="glass">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">System Status</CardTitle>
            <CardDescription>Cached platform health for operations awareness</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <StatusTile
                label="Backend Status"
                value={apiOk === null ? 'Checking…' : apiOk ? 'Online' : 'Down'}
                ok={apiOk}
                icon={Activity}
              />
              <StatusTile
                label="Database Status"
                value={dbOk === null ? 'Checking…' : dbOk ? 'Connected' : 'Disconnected'}
                ok={dbOk}
                icon={Database}
              />
              <StatusTile
                label="Monitoring Engine"
                value={alertsQuery.isError ? 'Degraded' : 'Active'}
                ok={!alertsQuery.isError}
                icon={Radar}
              />
              <StatusTile
                label="Last Refresh"
                value={lastRefresh ?? '—'}
                hint="Alert stream"
                icon={RefreshCw}
              />
            </div>
          </CardContent>
        </Card>
      </section>

      {/* Quick filters + search */}
      <section className="min-w-0 space-y-3" aria-label="Filters">
        <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
          {QUICK_FILTERS.map((filter) => (
            <Button
              key={filter.id}
              type="button"
              size="sm"
              variant={quickFilter === filter.id ? 'default' : 'secondary'}
              className="shrink-0"
              onClick={() => {
                onQuickFilter(filter.id)
                scrollToAlertStream()
              }}
            >
              {filter.label}
            </Button>
          ))}
        </div>
        <div className="relative max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-9"
            placeholder="Search hostname, IP, incident, message…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </section>

      {alertsQuery.isLoading && alerts.length === 0 ? (
        <TableSkeleton rows={6} />
      ) : alertsQuery.error && alerts.length === 0 ? (
        <ErrorState
          message={
            alertsQuery.error instanceof Error ? alertsQuery.error.message : 'Failed to load alerts'
          }
          onRetry={() => void alertsQuery.refetch()}
        />
      ) : (
        <div className="grid min-w-0 gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(280px,0.9fr)]">
          {/* Live alert stream */}
          <section
            id="alert-stream-section"
            className="space-y-4 scroll-mt-24"
            aria-label="Live alert stream"
          >
            <div>
              <h2 className="text-lg font-semibold tracking-tight">Live Alert Stream</h2>
              <p className="text-sm text-muted-foreground">
                {filtered.length} event{filtered.length === 1 ? '' : 's'} in current view
              </p>
            </div>

            {filtered.length === 0 ? (
              <Card className="glass">
                <EmptyState title="No alerts" description="Nothing matches the current filters." />
              </Card>
            ) : (
              <>
                <div className="space-y-2">
                  {pagination.pageItems.map((alert, index) => (
                    <AlertStreamRow
                      key={alert._id}
                      alert={alert}
                      index={index}
                      selected={selectedId === alert._id}
                      canAct={isUser}
                      busy={acknowledge.isPending || dismiss.isPending}
                      onSelect={() => setSelectedId(alert._id)}
                      onAck={() => acknowledge.mutate(alert._id)}
                      onDismiss={() => dismiss.mutate(alert._id)}
                      onOpenStorm={isStormAlert(alert) ? () => openStorm(alert) : undefined}
                      onOpenInterface={
                        hasInterfaceReference(alert) ? () => openInterface(alert) : undefined
                      }
                    />
                  ))}
                </div>
                {pagination.totalPages > 1 || pagination.total > pagination.limit ? (
                  <PaginationControls
                    page={pagination.page}
                    totalPages={Math.max(pagination.totalPages, 1)}
                    total={pagination.total}
                    limit={pagination.limit}
                    onPageChange={pagination.setPage}
                    onLimitChange={pagination.setLimit}
                    limitOptions={[10, 25, 50, 100]}
                  />
                ) : null}
              </>
            )}

            {/* Recent operations (mobile stacks below stream; desktop also under stream column) */}
            <div className="space-y-3 pt-2">
              <div>
                <h2 className="text-lg font-semibold tracking-tight">Recent Operations</h2>
                <p className="text-sm text-muted-foreground">
                  Mitigations, recoveries, discoveries, and device state changes
                </p>
              </div>
              <Card className="glass">
                <CardContent className="p-0">
                  {recentOps.length === 0 ? (
                    <div className="p-6">
                      <EmptyState title="No recent operations" description="Activity will appear as events arrive." />
                    </div>
                  ) : (
                    <ul className="divide-y divide-border/60">
                      {recentOps.map((item) => (
                        <li key={item.id}>
                          <Link
                            to={item.href || '/alerts'}
                            className="flex items-start gap-3 px-4 py-3 transition-colors hover:bg-secondary/40"
                          >
                            <span
                              className={cn(
                                'mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg',
                                item.kind === 'mitigation' && 'bg-danger/15 text-danger',
                                item.kind === 'recovery' && 'bg-success/15 text-success',
                                item.kind === 'discovery' && 'bg-primary/15 text-primary',
                                item.kind === 'device' && 'bg-warning/15 text-warning',
                                item.kind === 'alert' && 'bg-violet-500/15 text-violet-300',
                              )}
                            >
                              {item.kind === 'mitigation' ? (
                                <Shield className="h-4 w-4" />
                              ) : item.kind === 'recovery' ? (
                                <CheckCircle2 className="h-4 w-4" />
                              ) : item.kind === 'discovery' ? (
                                <Radar className="h-4 w-4" />
                              ) : item.kind === 'device' ? (
                                <WifiOff className="h-4 w-4" />
                              ) : (
                                <CloudLightning className="h-4 w-4" />
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
            </div>
          </section>

          {/* Detail panel */}
          <aside className="xl:sticky xl:top-20 xl:self-start" aria-label="Alert detail">
            <AnimatePresence mode="wait">
              {selected ? (
                <motion.div
                  key={selected._id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 8 }}
                  transition={{ duration: 0.18 }}
                >
                  <AlertDetailPanel
                    alert={selected}
                    canAct={isUser}
                    busy={acknowledge.isPending || dismiss.isPending}
                    onAck={() => acknowledge.mutate(selected._id)}
                    onDismiss={() => dismiss.mutate(selected._id)}
                    onOpenStorm={isStormAlert(selected) ? () => openStorm(selected) : undefined}
                    onOpenInterface={
                      hasInterfaceReference(selected) ? () => openInterface(selected) : undefined
                    }
                    interfacePath={
                      hasInterfaceReference(selected) ? interfaceDetailPath(selected) : undefined
                    }
                  />
                </motion.div>
              ) : (
                <Card className="glass">
                  <EmptyState
                    title="Select an alert"
                    description="Choose an event from the live stream to inspect details."
                    icon={Bell}
                  />
                </Card>
              )}
            </AnimatePresence>
          </aside>
        </div>
      )}
    </div>
  )
}

function StatusTile({
  label,
  value,
  hint,
  ok,
  icon: Icon,
}: {
  label: string
  value: string
  hint?: string
  ok?: boolean | null
  icon: typeof Activity
}) {
  return (
    <div className="rounded-xl border border-border/60 bg-secondary/20 p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {label}
        </p>
        <Icon className="h-3.5 w-3.5 text-muted-foreground" />
      </div>
      <p
        className={cn(
          'mt-2 text-lg font-bold tracking-tight',
          ok === true && 'text-success',
          ok === false && 'text-danger',
        )}
      >
        {value}
      </p>
      {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  )
}

function AlertStreamRow({
  alert,
  index,
  selected,
  canAct,
  busy,
  onSelect,
  onAck,
  onDismiss,
  onOpenStorm,
  onOpenInterface,
}: {
  alert: AlertItem
  index: number
  selected: boolean
  canAct: boolean
  busy: boolean
  onSelect: () => void
  onAck: () => void
  onDismiss: () => void
  onOpenStorm?: () => void
  onOpenInterface?: () => void
}) {
  const storm = isStormAlert(alert)
  const severity = alertSeverityTone(alert)
  const displayName = alert.deviceName || alert.hostname

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(index * 0.02, 0.2) }}
    >
      <Card
        className={cn(
          'glass cursor-pointer border-l-[3px] transition-all hover:bg-secondary/30',
          severity === 'danger' && 'border-l-danger',
          severity === 'warning' && 'border-l-warning',
          severity === 'info' && 'border-l-success',
          severity === 'default' && 'border-l-primary',
          selected && 'ring-1 ring-primary/40 bg-primary/5',
        )}
        onClick={onSelect}
      >
        <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={cn(
                  'inline-flex h-8 w-8 items-center justify-center rounded-lg',
                  storm ? 'bg-violet-500/15 text-violet-300' : 'bg-sky-500/15 text-sky-400',
                )}
              >
                {storm ? <CloudLightning className="h-4 w-4" /> : <Server className="h-4 w-4" />}
              </span>
              <p className="truncate font-semibold">{alert.title || displayName}</p>
              {alert.severity ? (
                <SeverityBadge severity={alert.severity} />
              ) : (
                <StatusBadge status={alert.status} pulse={false} />
              )}
              <Badge variant="outline">{alertCategoryLabel(alert)}</Badge>
              {alert.acknowledged ? <Badge variant="secondary">Acknowledged</Badge> : null}
              {alert.dismissed ? <Badge variant="muted">Dismissed</Badge> : null}
            </div>
            <p className="line-clamp-2 text-sm text-muted-foreground">{alert.message}</p>
            <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span className="font-medium text-foreground/80">{displayName}</span>
              <span className="mono">{alert.ipAddress}</span>
              <span>{formatDateTime(alert.createdAt)}</span>
              <span className="text-muted-foreground/80">{formatRelative(alert.createdAt)}</span>
            </div>
          </div>

          {canAct && !alert.dismissed ? (
            <div
              className="flex shrink-0 flex-wrap gap-2"
              onClick={(e) => e.stopPropagation()}
            >
              {!alert.acknowledged ? (
                <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={onAck}>
                  <Check className="h-3.5 w-3.5" />
                  Acknowledge
                </Button>
              ) : null}
              <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onDismiss}>
                <X className="h-3.5 w-3.5" />
                Dismiss
              </Button>
              {onOpenStorm ? (
                <Button type="button" size="sm" variant="outline" onClick={onOpenStorm}>
                  <Shield className="h-3.5 w-3.5" />
                  Storm
                </Button>
              ) : null}
              {onOpenInterface ? (
                <Button type="button" size="sm" variant="outline" onClick={onOpenInterface}>
                  <Network className="h-3.5 w-3.5" />
                  Open Interface
                </Button>
              ) : null}
            </div>
          ) : onOpenStorm || onOpenInterface ? (
            <div
              className="flex shrink-0 flex-wrap gap-2"
              onClick={(e) => e.stopPropagation()}
            >
              {onOpenStorm ? (
                <Button type="button" size="sm" variant="outline" onClick={onOpenStorm}>
                  <Shield className="h-3.5 w-3.5" />
                  Storm
                </Button>
              ) : null}
              {onOpenInterface ? (
                <Button type="button" size="sm" variant="outline" onClick={onOpenInterface}>
                  <Network className="h-3.5 w-3.5" />
                  Open Interface
                </Button>
              ) : null}
            </div>
          ) : null}
        </CardContent>
      </Card>
    </motion.div>
  )
}

function AlertDetailPanel({
  alert,
  canAct,
  busy,
  onAck,
  onDismiss,
  onOpenStorm,
  onOpenInterface,
  interfacePath,
}: {
  alert: AlertItem
  canAct: boolean
  busy: boolean
  onAck: () => void
  onDismiss: () => void
  onOpenStorm?: () => void
  onOpenInterface?: () => void
  interfacePath?: string
}) {
  const storm = isStormAlert(alert)
  const displayName = alert.deviceName || alert.hostname

  return (
    <Card className="glass overflow-hidden">
      <CardHeader className="border-b border-border/60 bg-secondary/20">
        <div className="flex flex-wrap items-center gap-2">
          {alert.severity ? <SeverityBadge severity={alert.severity} /> : null}
          <StatusBadge status={alert.status} pulse={false} />
          {storm ? <Badge variant="outline">Storm</Badge> : <Badge variant="outline">Device</Badge>}
        </div>
        <CardTitle className="mt-2 text-lg leading-snug">
          {alert.title || displayName}
        </CardTitle>
        <CardDescription className="whitespace-pre-line">{alert.message}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5 p-5">
        <DetailGroup title="Alert">
          <DetailRow label="Status" value={alert.status} />
          <DetailRow label="Severity" value={alert.severity || '—'} />
          <DetailRow label="Category" value={alertCategoryLabel(alert)} />
          <DetailRow label="Scan type" value={alert.scanType || '—'} />
          <DetailRow label="Generated by" value={alert.generatedBy || '—'} />
        </DetailGroup>

        <DetailGroup title="Source">
          <DetailRow label="Hostname" value={displayName} />
          <DetailRow label="IP address" value={alert.ipAddress} mono />
          <DetailRow label="Interface" value={alert.interface || '—'} />
          <DetailRow
            label="Risk score"
            value={alert.riskScore == null ? '—' : Number(alert.riskScore).toFixed(0)}
          />
        </DetailGroup>

        <DetailGroup title="Related Device">
          {alert.deviceId ? (
            <Button type="button" variant="secondary" size="sm" className="w-full justify-start" asChild>
              <Link to={`/devices/${alert.deviceId}`}>
                <Server className="mr-2 h-4 w-4" />
                Open {displayName}
              </Link>
            </Button>
          ) : (
            <p className="text-sm text-muted-foreground">No linked device id.</p>
          )}
          {interfacePath ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="mt-1 w-full justify-start"
              asChild
            >
              <Link to={interfacePath}>
                <Network className="mr-2 h-4 w-4" />
                Open Interface
              </Link>
            </Button>
          ) : null}
        </DetailGroup>

        <DetailGroup title="Incident">
          {alert.incidentId ? (
            <>
              <DetailRow label="Incident ID" value={alert.incidentId} mono />
              <DetailRow label="Action" value={alert.action || '—'} />
              <DetailRow label="Recovery" value={alert.recoveryDuration || '—'} />
              {onOpenStorm ? (
                <Button type="button" size="sm" className="mt-1 w-full" onClick={onOpenStorm}>
                  <Shield className="mr-2 h-4 w-4" />
                  Open in Storm Protection
                </Button>
              ) : null}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">No storm incident linked.</p>
          )}
        </DetailGroup>

        <DetailGroup title="Actions">
          <div className="flex flex-wrap gap-2">
            {canAct && !alert.dismissed && !alert.acknowledged ? (
              <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={onAck}>
                <Check className="h-3.5 w-3.5" />
                Acknowledge
              </Button>
            ) : null}
            {canAct && !alert.dismissed ? (
              <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onDismiss}>
                <X className="h-3.5 w-3.5" />
                Dismiss
              </Button>
            ) : null}
            {onOpenInterface ? (
              <Button type="button" size="sm" variant="outline" onClick={onOpenInterface}>
                <Network className="h-3.5 w-3.5" />
                Open Interface
              </Button>
            ) : null}
            {alert.emailSent ? <Badge variant="outline">Email sent</Badge> : null}
            {alert.acknowledged ? <Badge variant="secondary">Acknowledged</Badge> : null}
            {alert.dismissed ? <Badge variant="muted">Dismissed</Badge> : null}
          </div>
        </DetailGroup>

        <DetailGroup title="Timeline">
          <DetailRow label="Created" value={formatDateTime(alert.createdAt)} />
          <DetailRow
            label="Acknowledged at"
            value={alert.acknowledgedAt ? formatDateTime(alert.acknowledgedAt) : '—'}
          />
          <DetailRow
            label="Dismissed at"
            value={alert.dismissedAt ? formatDateTime(alert.dismissedAt) : '—'}
          />
          <DetailRow label="Relative" value={formatRelative(alert.createdAt)} />
        </DetailGroup>
      </CardContent>
    </Card>
  )
}

function DetailGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-2">
      <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
        {title}
      </p>
      <div className="space-y-1.5 rounded-xl border border-border/60 bg-secondary/15 p-3">
        {children}
      </div>
    </div>
  )
}

function DetailRow({
  label,
  value,
  mono,
}: {
  label: string
  value: string
  mono?: boolean
}) {
  return (
    <div className="flex items-start justify-between gap-3 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn('max-w-[60%] text-right font-medium', mono && 'mono')}>{value}</span>
    </div>
  )
}
