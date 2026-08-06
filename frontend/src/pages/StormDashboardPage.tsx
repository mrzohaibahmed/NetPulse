import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import { AlertOctagon, Bell, CloudLightning, ShieldAlert, ShieldCheck, Siren, Zap } from 'lucide-react'
import {
  useAlertsQuery,
  useConfirmationQuery,
  useEligibilityQuery,
  useMitigationHistoryQuery,
  useRecoveryHistoryQuery,
  useRiskQuery,
  useSafetyQuery,
  useStormIncidentsQuery,
} from '@/hooks/queries'
import { isStormAlert } from '@/lib/status'
import type { HealthLabel } from '@/lib/health'
import { formatDateTime } from '@/utils/format'
import { KpiCard } from '@/components/shared/KpiCard'
import { HealthGauge } from '@/components/shared/HealthGauge'
import { EmptyState } from '@/components/shared/EmptyState'
import { ErrorState } from '@/components/shared/ErrorState'
import { DashboardSkeleton } from '@/components/shared/LoadingState'
import { PageHeader } from '@/components/shared/PageHeader'
import { Badge, type BadgeProps } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

// Reuses the app's existing semantic tokens only (success/warning/danger/
// primary) — deliberately not introducing a new hardcoded color for the
// 4th (HIGH) severity band.
const SEVERITY_COLORS: Record<string, string> = {
  LOW: '#22C55E',
  MEDIUM: '#F59E0B',
  HIGH: '#3B82F6',
  CRITICAL: '#EF4444',
}

function incidentStatusVariant(status: string): BadgeProps['variant'] {
  const value = (status || '').toUpperCase()
  if (value === 'RESOLVED') return 'success'
  if (value === 'CANCELLED') return 'muted'
  if (value === 'MITIGATION_FAILED' || value === 'RECOVERY_FAILED') return 'danger'
  if (value === 'MITIGATED') return 'danger'
  if (value === 'MONITORING') return 'secondary'
  return 'warning'
}

function computeStormHealth(input: {
  eligibleCount: number
  unsafeCount: number
  confirmedCount: number
  openIncidentCount: number
}): { score: number; label: HealthLabel } {
  if (input.eligibleCount === 0) return { score: 100, label: 'Excellent' }
  const unsafeRatio = (input.unsafeCount / Math.max(input.eligibleCount, 1)) * 100
  const score = Math.max(
    0,
    Math.min(100, Math.round(100 - unsafeRatio * 0.5 - input.confirmedCount * 15 - input.openIncidentCount * 10)),
  )
  let label: HealthLabel = 'Excellent'
  if (score < 50) label = 'Critical'
  else if (score < 70) label = 'Warning'
  else if (score < 90) label = 'Good'
  return { score, label }
}

const COUNT_ONLY = { limit: 1 }

export function StormDashboardPage() {
  const eligible = useEligibilityQuery({ ...COUNT_ONLY, eligible: true })
  const riskHigh = useRiskQuery({ ...COUNT_ONLY, severity: 'HIGH' })
  const riskCritical = useRiskQuery({ ...COUNT_ONLY, severity: 'CRITICAL' })
  const riskMedium = useRiskQuery({ ...COUNT_ONLY, severity: 'MEDIUM' })
  const riskLow = useRiskQuery({ ...COUNT_ONLY, severity: 'LOW' })
  const confirmed = useConfirmationQuery({ ...COUNT_ONLY, state: 'CONFIRMED' })
  const unsafe = useSafetyQuery({ ...COUNT_ONLY, safetyStatus: 'UNSAFE' })
  // "Recent Incidents" table — a recency-capped sample is fine here.
  const incidents = useStormIncidentsQuery({ limit: 50 })
  // Open/active incident KPI needs an exact total, not a sample — count
  // each non-terminal status separately via cheap limit=1 queries and sum.
  const incidentsOpen = useStormIncidentsQuery({ ...COUNT_ONLY, status: 'OPEN' })
  const incidentsReady = useStormIncidentsQuery({ ...COUNT_ONLY, status: 'READY_FOR_MITIGATION' })
  const incidentsMitigated = useStormIncidentsQuery({ ...COUNT_ONLY, status: 'MITIGATED' })
  const incidentsMonitoring = useStormIncidentsQuery({ ...COUNT_ONLY, status: 'MONITORING' })
  const mitigationHistory = useMitigationHistoryQuery(COUNT_ONLY)
  const recoveryHistory = useRecoveryHistoryQuery(COUNT_ONLY)
  const alertsQuery = useAlertsQuery('active', 100)

  const isLoading =
    eligible.isLoading ||
    riskHigh.isLoading ||
    riskCritical.isLoading ||
    confirmed.isLoading ||
    unsafe.isLoading ||
    incidents.isLoading

  const error =
    eligible.error || riskHigh.error || riskCritical.error || confirmed.error || unsafe.error || incidents.error

  const refetchAll = () =>
    Promise.all([
      eligible.refetch(),
      riskHigh.refetch(),
      riskCritical.refetch(),
      riskMedium.refetch(),
      riskLow.refetch(),
      confirmed.refetch(),
      unsafe.refetch(),
      incidents.refetch(),
      incidentsOpen.refetch(),
      incidentsReady.refetch(),
      incidentsMitigated.refetch(),
      incidentsMonitoring.refetch(),
      mitigationHistory.refetch(),
      recoveryHistory.refetch(),
      alertsQuery.refetch(),
    ])

  const eligibleCount = eligible.data?.total ?? 0
  const highCount = riskHigh.data?.total ?? 0
  const criticalCount = riskCritical.data?.total ?? 0
  const mediumCount = riskMedium.data?.total ?? 0
  const lowCount = riskLow.data?.total ?? 0
  const elevatedRiskCount = highCount + criticalCount
  const confirmedCount = confirmed.data?.total ?? 0
  const unsafeCount = unsafe.data?.total ?? 0
  const mitigationTotal = mitigationHistory.data?.total ?? 0
  const recoveryTotal = recoveryHistory.data?.total ?? 0

  const incidentRows = incidents.data?.data ?? []
  const openIncidentCount =
    (incidentsOpen.data?.total ?? 0) +
    (incidentsReady.data?.total ?? 0) +
    (incidentsMitigated.data?.total ?? 0) +
    (incidentsMonitoring.data?.total ?? 0)

  const stormAlerts = useMemo(() => (alertsQuery.data?.data ?? []).filter(isStormAlert), [alertsQuery.data])

  const health = computeStormHealth({
    eligibleCount,
    unsafeCount,
    confirmedCount,
    openIncidentCount,
  })

  const severityChart = useMemo(
    () =>
      [
        { name: 'LOW', value: lowCount },
        { name: 'MEDIUM', value: mediumCount },
        { name: 'HIGH', value: highCount },
        { name: 'CRITICAL', value: criticalCount },
      ].filter((entry) => entry.value > 0),
    [lowCount, mediumCount, highCount, criticalCount],
  )

  const actionsChart = useMemo(
    () =>
      [
        { name: 'Mitigations', value: mitigationTotal, color: '#3B82F6' },
        { name: 'Recoveries', value: recoveryTotal, color: '#22C55E' },
      ].filter((entry) => entry.value > 0),
    [mitigationTotal, recoveryTotal],
  )

  if (isLoading && incidentRows.length === 0 && !eligible.data) {
    return (
      <div className="space-y-6">
        <PageHeader title="Storm Dashboard" description="Live overview of switch storm-protection status" />
        <DashboardSkeleton />
      </div>
    )
  }

  if (error && !eligible.data) {
    return (
      <div className="space-y-6">
        <PageHeader title="Storm Dashboard" />
        <ErrorState
          message={error instanceof Error ? error.message : 'Failed to load storm dashboard'}
          onRetry={() => void refetchAll()}
        />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Storm Dashboard"
        description="Live overview of switch storm-protection status"
        actions={
          <Button type="button" variant="secondary" onClick={() => void refetchAll()}>
            Refresh
          </Button>
        }
      />

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-7">
        <KpiCard label="Eligible Interfaces" value={eligibleCount} icon={ShieldCheck} tone="success" />
        <KpiCard label="Elevated Risk" value={elevatedRiskCount} icon={Zap} tone="warning" />
        <KpiCard label="Confirmed Storms" value={confirmedCount} icon={Siren} tone="danger" />
        <KpiCard label="Unsafe Interfaces" value={unsafeCount} icon={ShieldAlert} tone="warning" />
        <KpiCard label="Open Incidents" value={openIncidentCount} icon={AlertOctagon} tone="danger" />
        <KpiCard label="Storm Alerts" value={stormAlerts.length} icon={Bell} tone={stormAlerts.length ? 'danger' : 'success'} />
        <KpiCard
          label="Storm Safety"
          value={`${health.score}%`}
          hint={health.label}
          icon={CloudLightning}
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
        <HealthGauge score={health.score} label={health.label} title="Storm Safety" subtitle="Safety score" />
        <Card className="glass lg:col-span-2">
          <CardHeader>
            <CardTitle>Risk Severity Distribution</CardTitle>
          </CardHeader>
          <CardContent>
            {severityChart.length === 0 ? (
              <EmptyState title="No elevated-risk interfaces" description="Nothing is currently above LOW risk." />
            ) : (
              <div className="flex flex-col items-center gap-4 sm:flex-row">
                <div className="h-52 w-full sm:w-1/2">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={severityChart} dataKey="value" nameKey="name" innerRadius={52} outerRadius={78} paddingAngle={3}>
                        {severityChart.map((entry) => (
                          <Cell key={entry.name} fill={SEVERITY_COLORS[entry.name] ?? '#64748B'} />
                        ))}
                      </Pie>
                      <Tooltip />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <ul className="w-full space-y-2 text-sm sm:w-1/2">
                  {severityChart.map((entry) => (
                    <li key={entry.name} className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-2">
                        <span className="h-2.5 w-2.5 rounded-full" style={{ background: SEVERITY_COLORS[entry.name] ?? '#64748B' }} />
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
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <Card className="glass">
          <CardHeader>
            <CardTitle>Mitigation &amp; Recovery Activity</CardTitle>
          </CardHeader>
          <CardContent>
            {actionsChart.length === 0 ? (
              <EmptyState title="No storm actions yet" description="No mitigation or recovery attempts recorded." />
            ) : (
              <div className="flex flex-col items-center gap-4 sm:flex-row">
                <div className="h-52 w-full sm:w-1/2">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={actionsChart} dataKey="value" nameKey="name" outerRadius={78} paddingAngle={2}>
                        {actionsChart.map((entry) => (
                          <Cell key={entry.name} fill={entry.color} />
                        ))}
                      </Pie>
                      <Tooltip />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <ul className="w-full space-y-2 text-sm sm:w-1/2">
                  {actionsChart.map((entry) => (
                    <li key={entry.name} className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-2">
                        <span className="h-2.5 w-2.5 rounded-full" style={{ background: entry.color }} />
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
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Active Storm Alerts</CardTitle>
            <Button type="button" variant="ghost" size="sm" asChild>
              <Link to="/alerts?tab=storm">View all</Link>
            </Button>
          </CardHeader>
          <CardContent>
            {stormAlerts.length === 0 ? (
              <EmptyState title="No active storm alerts" description="All clear — no outstanding storm alerts." icon={ShieldCheck} />
            ) : (
              <div className="space-y-3">
                {stormAlerts.slice(0, 6).map((alert) => (
                  <div
                    key={alert._id}
                    className="flex flex-col gap-1 rounded-lg border border-border/70 bg-secondary/30 p-3"
                  >
                    <p className="font-semibold">{alert.title || alert.hostname}</p>
                    <p className="text-sm text-muted-foreground">{alert.message}</p>
                    <p className="mono text-xs text-muted-foreground">{formatDateTime(alert.createdAt)}</p>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </section>

      <Card className="glass">
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>Recent Incidents</CardTitle>
          <Button type="button" variant="ghost" size="sm" asChild>
            <Link to="/storm">Open Storm Protection</Link>
          </Button>
        </CardHeader>
        <CardContent>
          {incidentRows.length === 0 ? (
            <EmptyState title="No incidents yet" description="No storm incidents have been recorded." />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Incident</TableHead>
                  <TableHead>Device / Interface</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {incidentRows.slice(0, 10).map((row) => (
                  <TableRow key={row.incidentId}>
                    <TableCell>
                      <Link
                        to={`/storm?incident=${encodeURIComponent(row.incidentId)}`}
                        className="mono font-semibold text-primary hover:underline"
                      >
                        {row.incidentId}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <p>{row.hostname || row.deviceId}</p>
                      <p className="mono text-xs text-muted-foreground">{row.interface}</p>
                    </TableCell>
                    <TableCell>
                      <Badge variant={incidentStatusVariant(row.status)} className="font-semibold uppercase tracking-wide">
                        {row.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{formatDateTime(row.createdAt)}</TableCell>
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
