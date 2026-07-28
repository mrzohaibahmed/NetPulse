import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Activity, CheckCircle2, RefreshCw, Shield } from 'lucide-react'
import { PortClassificationBadges } from '@/components/interfaces/InterfaceStatusBadge'
import { EmptyState } from '@/components/shared/EmptyState'
import { ErrorState } from '@/components/shared/ErrorState'
import { TableSkeleton } from '@/components/shared/LoadingState'
import { PageHeader } from '@/components/shared/PageHeader'
import { PaginationControls } from '@/components/shared/PaginationControls'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useAuth } from '@/auth/AuthContext'
import {
  useConfirmationMutations,
  useConfirmationQuery,
  useEligibilityMutations,
  useEligibilityQuery,
  useInterfaceRiskQuery,
  useRiskMutations,
  useRiskQuery,
  useStormConfigQuery,
} from '@/hooks/queries'
import { cn } from '@/lib/utils'
import type {
  ConfirmationResult,
  EligibilityResult,
  NetworkInterface,
  RiskResult,
} from '@/types'
import { formatDateTime, formatRelative } from '@/utils/format'

const DEFAULT_LIMIT = 25

function EligibilityBadge({ eligible }: { eligible: boolean }) {
  return (
    <Badge variant={eligible ? 'success' : 'danger'} className="font-semibold">
      <span
        className={`h-1.5 w-1.5 rounded-full ${eligible ? 'bg-success' : 'bg-danger'}`}
        aria-hidden
      />
      {eligible ? 'Eligible' : 'Not Eligible'}
    </Badge>
  )
}

function severityTone(severity: string | undefined): {
  badge: 'success' | 'warning' | 'danger' | 'default' | 'secondary'
  text: string
  bar: string
} {
  const value = (severity || 'LOW').toUpperCase()
  if (value === 'CRITICAL') {
    return { badge: 'danger', text: 'text-danger', bar: 'bg-danger' }
  }
  if (value === 'HIGH') {
    return { badge: 'warning', text: 'text-orange-400', bar: 'bg-orange-400' }
  }
  if (value === 'MEDIUM') {
    return { badge: 'warning', text: 'text-warning', bar: 'bg-warning' }
  }
  return { badge: 'success', text: 'text-success', bar: 'bg-success' }
}

function SeverityBadge({ severity }: { severity: string }) {
  const tone = severityTone(severity)
  return (
    <Badge variant={tone.badge} className="font-semibold uppercase tracking-wide">
      <span className={cn('h-1.5 w-1.5 rounded-full', tone.bar)} aria-hidden />
      {severity}
    </Badge>
  )
}

function formatRate(value: number | null | undefined, suffix = '/s'): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  const n = Number(value)
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k${suffix}`
  return `${n.toFixed(n >= 10 ? 0 : 1)}${suffix}`
}

function ConfirmationStateBadge({ state }: { state: string }) {
  const value = (state || 'NOT_CONFIRMED').toUpperCase()
  if (value === 'CONFIRMED') {
    return (
      <Badge variant="success" className="font-semibold uppercase tracking-wide">
        <span className="h-1.5 w-1.5 rounded-full bg-success" aria-hidden />
        Confirmed
      </Badge>
    )
  }
  if (value === 'PENDING') {
    return (
      <Badge variant="warning" className="font-semibold uppercase tracking-wide">
        <span className="h-1.5 w-1.5 rounded-full bg-warning" aria-hidden />
        Pending
      </Badge>
    )
  }
  return (
    <Badge variant="muted" className="font-semibold uppercase tracking-wide">
      <span className="h-1.5 w-1.5 rounded-full bg-slate-400" aria-hidden />
      Not confirmed
    </Badge>
  )
}

function ConfirmationProgressBar({
  consecutive,
  required,
  state,
}: {
  consecutive: number
  required: number
  state: string
}) {
  const total = Math.max(required || 1, 1)
  const filled = Math.min(Math.max(consecutive, 0), total)
  const tone =
    String(state).toUpperCase() === 'CONFIRMED'
      ? 'bg-success'
      : String(state).toUpperCase() === 'PENDING'
        ? 'bg-warning'
        : 'bg-slate-500'

  return (
    <div className="min-w-[120px] space-y-1">
      <div className="flex gap-0.5" aria-hidden>
        {Array.from({ length: total }).map((_, index) => (
          <div
            key={index}
            className={cn(
              'h-2 flex-1 rounded-sm',
              index < filled ? tone : 'bg-secondary',
            )}
          />
        ))}
      </div>
      <p className="mono text-xs text-muted-foreground">
        {filled} / {total}
      </p>
    </div>
  )
}

function classificationIface(row: EligibilityResult): Pick<
  NetworkInterface,
  | 'portMode'
  | 'mode'
  | 'isAccess'
  | 'isTrunk'
  | 'isUplink'
  | 'isInfrastructure'
  | 'isManagement'
  | 'isProtected'
> {
  return {
    portMode: row.portMode || 'unknown',
    mode: row.portMode || 'unknown',
    isAccess: Boolean(row.isAccess),
    isTrunk: Boolean(row.isTrunk),
    isUplink: Boolean(row.isUplink),
    isInfrastructure: Boolean(row.isInfrastructure),
    isManagement: Boolean(row.isManagement),
    isProtected: Boolean(row.isProtected),
  }
}

export function StormProtectionPage() {
  const { isAdmin } = useAuth()
  const eligibilityMutations = useEligibilityMutations()
  const riskMutations = useRiskMutations()
  const confirmationMutations = useConfirmationMutations()
  const stormConfig = useStormConfigQuery()

  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [eligibleFilter, setEligibleFilter] = useState('all')
  const [page, setPage] = useState(1)
  const [limit, setLimit] = useState(DEFAULT_LIMIT)

  const [riskQuery, setRiskQuery] = useState('')
  const [debouncedRiskQuery, setDebouncedRiskQuery] = useState('')
  const [severityFilter, setSeverityFilter] = useState('all')
  const [riskPage, setRiskPage] = useState(1)
  const [riskLimit, setRiskLimit] = useState(DEFAULT_LIMIT)
  const [selectedRisk, setSelectedRisk] = useState<RiskResult | null>(null)

  const [confirmQuery, setConfirmQuery] = useState('')
  const [debouncedConfirmQuery, setDebouncedConfirmQuery] = useState('')
  const [confirmStateFilter, setConfirmStateFilter] = useState('all')
  const [confirmPage, setConfirmPage] = useState(1)
  const [confirmLimit, setConfirmLimit] = useState(DEFAULT_LIMIT)

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 300)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedRiskQuery(riskQuery), 300)
    return () => window.clearTimeout(timer)
  }, [riskQuery])

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedConfirmQuery(confirmQuery), 300)
    return () => window.clearTimeout(timer)
  }, [confirmQuery])

  useEffect(() => {
    setPage(1)
  }, [debouncedQuery, eligibleFilter, limit])

  useEffect(() => {
    setRiskPage(1)
  }, [debouncedRiskQuery, severityFilter, riskLimit])

  useEffect(() => {
    setConfirmPage(1)
  }, [debouncedConfirmQuery, confirmStateFilter, confirmLimit])

  const eligibilityQuery = useEligibilityQuery({
    page,
    limit,
    q: debouncedQuery,
    eligible:
      eligibleFilter === 'eligible'
        ? true
        : eligibleFilter === 'ineligible'
          ? false
          : undefined,
  })

  const riskListQuery = useRiskQuery({
    page: riskPage,
    limit: riskLimit,
    q: debouncedRiskQuery,
    severity: severityFilter === 'all' ? undefined : severityFilter,
  })

  const confirmationQuery = useConfirmationQuery({
    page: confirmPage,
    limit: confirmLimit,
    q: debouncedConfirmQuery,
    state: confirmStateFilter === 'all' ? undefined : confirmStateFilter,
  })

  const selectedHistoryQuery = useInterfaceRiskQuery(
    selectedRisk?.deviceId || '',
    selectedRisk?.interface || '',
    Boolean(selectedRisk),
  )

  const rows = eligibilityQuery.data?.data ?? []
  const total = eligibilityQuery.data?.total ?? eligibilityQuery.data?.count ?? 0
  const totalPages = eligibilityQuery.data?.totalPages ?? 1

  const riskRows = riskListQuery.data?.data ?? []
  const riskTotal = riskListQuery.data?.total ?? riskListQuery.data?.count ?? 0
  const riskTotalPages = riskListQuery.data?.totalPages ?? 1

  const confirmRows = confirmationQuery.data?.data ?? []
  const confirmTotal =
    confirmationQuery.data?.total ?? confirmationQuery.data?.count ?? 0
  const confirmTotalPages = confirmationQuery.data?.totalPages ?? 1

  const eligibleCount = useMemo(() => rows.filter((r) => r.eligible).length, [rows])
  const ineligibleCount = rows.length - eligibleCount

  const confirmedCount = useMemo(
    () => confirmRows.filter((r) => String(r.state).toUpperCase() === 'CONFIRMED').length,
    [confirmRows],
  )
  const pendingCount = useMemo(
    () => confirmRows.filter((r) => String(r.state).toUpperCase() === 'PENDING').length,
    [confirmRows],
  )

  const criticalCount = useMemo(
    () => riskRows.filter((r) => String(r.severity).toUpperCase() === 'CRITICAL').length,
    [riskRows],
  )
  const avgRisk = useMemo(() => {
    if (!riskRows.length) return 0
    return riskRows.reduce((sum, r) => sum + (r.riskScore || 0), 0) / riskRows.length
  }, [riskRows])
  const maxRisk = useMemo(
    () => riskRows.reduce((max, r) => Math.max(max, r.riskScore || 0), 0),
    [riskRows],
  )

  const trendData = useMemo(() => {
    const history = selectedHistoryQuery.data?.history ?? []
    return [...history]
      .reverse()
      .map((point) => ({
        time: formatDateTime(point.timestamp) || '',
        label: formatRelative(point.timestamp) || '',
        riskScore: point.riskScore,
        severity: point.severity,
      }))
  }, [selectedHistoryQuery.data?.history])

  const isBusy =
    eligibilityMutations.evaluateAll.isPending ||
    riskMutations.calculateAll.isPending ||
    confirmationMutations.evaluateAll.isPending

  const refreshAll = () => {
    void eligibilityQuery.refetch()
    void riskListQuery.refetch()
    void confirmationQuery.refetch()
    if (selectedRisk) void selectedHistoryQuery.refetch()
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title="Storm Protection"
        description="Port eligibility gate and Layer-2 storm risk scoring — no mitigation is performed here"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {isAdmin ? (
              <>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={isBusy || stormConfig.data?.enableEligibility === false}
                  onClick={() => eligibilityMutations.evaluateAll.mutate()}
                >
                  <Shield className="mr-2 h-4 w-4" />
                  {eligibilityMutations.evaluateAll.isPending
                    ? 'Evaluating…'
                    : 'Evaluate eligibility'}
                </Button>
                <Button
                  type="button"
                  disabled={isBusy || stormConfig.data?.risk?.enableRisk === false}
                  onClick={() => riskMutations.calculateAll.mutate()}
                >
                  <Activity className="mr-2 h-4 w-4" />
                  {riskMutations.calculateAll.isPending
                    ? 'Scoring…'
                    : 'Calculate risk'}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={
                    isBusy ||
                    stormConfig.data?.confirmation?.confirmationEnabled === false
                  }
                  onClick={() => confirmationMutations.evaluateAll.mutate()}
                >
                  <CheckCircle2 className="mr-2 h-4 w-4" />
                  {confirmationMutations.evaluateAll.isPending
                    ? 'Confirming…'
                    : 'Evaluate confirmation'}
                </Button>
              </>
            ) : null}
            <Button type="button" variant="secondary" onClick={refreshAll}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
          </div>
        }
      />

      {/* ── Risk Score ─────────────────────────────────────────────── */}
      <section className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Risk Score</h2>
          <p className="text-sm text-muted-foreground">
            Rate-based storm probability for eligible access ports. Scores use
            analyzer contributions — not raw counters.
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Interfaces scored
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold">{riskTotal}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Avg risk (page)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className={cn('text-2xl font-bold', severityTone(
                avgRisk >= 75 ? 'CRITICAL' : avgRisk >= 50 ? 'HIGH' : avgRisk >= 25 ? 'MEDIUM' : 'LOW',
              ).text)}>
                {avgRisk.toFixed(1)}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Peak risk (page)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className={cn('text-2xl font-bold', severityTone(
                maxRisk >= 75 ? 'CRITICAL' : maxRisk >= 50 ? 'HIGH' : maxRisk >= 25 ? 'MEDIUM' : 'LOW',
              ).text)}>
                {maxRisk.toFixed(1)}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Critical (page)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-danger">{criticalCount}</p>
            </CardContent>
          </Card>
        </div>

        <div className="flex flex-wrap gap-3">
          <Input
            type="search"
            className="max-w-sm"
            placeholder="Search interface, host, severity…"
            value={riskQuery}
            onChange={(e) => setRiskQuery(e.target.value)}
          />
          <Select value={severityFilter} onValueChange={setSeverityFilter}>
            <SelectTrigger className="w-[180px]">
              <SelectValue placeholder="Severity" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All severities</SelectItem>
              <SelectItem value="LOW">Low</SelectItem>
              <SelectItem value="MEDIUM">Medium</SelectItem>
              <SelectItem value="HIGH">High</SelectItem>
              <SelectItem value="CRITICAL">Critical</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {riskListQuery.isLoading ? (
          <TableSkeleton rows={8} />
        ) : riskListQuery.isError ? (
          <ErrorState
            title="Unable to load risk results"
            message={
              riskListQuery.error instanceof Error
                ? riskListQuery.error.message
                : 'Unexpected error'
            }
            onRetry={() => void riskListQuery.refetch()}
          />
        ) : riskRows.length === 0 ? (
          <EmptyState
            icon={Activity}
            title="No risk scores yet"
            description="Run eligibility, then calculate risk. The scheduler scores interfaces after each stats + eligibility cycle."
          />
        ) : (
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(320px,1fr)]">
            <Card>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Interface</TableHead>
                      <TableHead>Risk</TableHead>
                      <TableHead>Severity</TableHead>
                      <TableHead>Broadcast</TableHead>
                      <TableHead>Multicast</TableHead>
                      <TableHead>Util</TableHead>
                      <TableHead>Errors</TableHead>
                      <TableHead>Confidence</TableHead>
                      <TableHead>Last calculated</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {riskRows.map((row) => {
                      const active =
                        selectedRisk?.deviceId === row.deviceId &&
                        selectedRisk?.interface === row.interface
                      const tone = severityTone(row.severity)
                      return (
                        <TableRow
                          key={`${row.deviceId}-${row.interface}-${row._id ?? ''}`}
                          className={cn(
                            'cursor-pointer',
                            active && 'bg-primary/10',
                          )}
                          onClick={() => setSelectedRisk(row)}
                        >
                          <TableCell className="font-medium">
                            <div>
                              <Link
                                to={`/interfaces/${row.deviceId}/${encodeURIComponent(row.interface)}`}
                                className="text-primary hover:underline"
                                onClick={(e) => e.stopPropagation()}
                              >
                                {row.interface}
                              </Link>
                              <p className="text-xs text-muted-foreground">
                                {row.hostname || row.ipAddress || row.deviceId}
                              </p>
                            </div>
                          </TableCell>
                          <TableCell>
                            <div className="flex min-w-[88px] items-center gap-2">
                              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-secondary">
                                <div
                                  className={cn('h-full rounded-full', tone.bar)}
                                  style={{
                                    width: `${Math.min(100, Math.max(0, row.riskScore))}%`,
                                  }}
                                />
                              </div>
                              <span className={cn('mono w-10 text-right text-xs font-semibold', tone.text)}>
                                {row.riskScore.toFixed(0)}
                              </span>
                            </div>
                          </TableCell>
                          <TableCell>
                            <SeverityBadge severity={String(row.severity)} />
                          </TableCell>
                          <TableCell className="mono text-xs">
                            {formatRate(row.broadcastRate)}
                          </TableCell>
                          <TableCell className="mono text-xs">
                            {formatRate(row.multicastRate)}
                          </TableCell>
                          <TableCell className="mono text-xs">
                            {row.utilization == null
                              ? '—'
                              : `${Number(row.utilization).toFixed(1)}%`}
                          </TableCell>
                          <TableCell className="mono text-xs">
                            {formatRate(row.errorRate)}
                          </TableCell>
                          <TableCell>{Number(row.confidence).toFixed(0)}%</TableCell>
                          <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                            <div title={formatDateTime(row.timestamp) || undefined}>
                              {formatRelative(row.timestamp) || '—'}
                            </div>
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>

            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">
                    {selectedRisk
                      ? `${selectedRisk.interface} detail`
                      : 'Select an interface'}
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {!selectedRisk ? (
                    <p className="text-sm text-muted-foreground">
                      Click a row to inspect contributors, rates, and risk trend.
                    </p>
                  ) : (
                    <>
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className={cn(
                            'text-3xl font-bold',
                            severityTone(selectedRisk.severity).text,
                          )}
                        >
                          {selectedRisk.riskScore.toFixed(1)}
                        </span>
                        <SeverityBadge severity={String(selectedRisk.severity)} />
                        <EligibilityBadge eligible={selectedRisk.eligible} />
                      </div>

                      <div className="grid grid-cols-2 gap-2 text-sm">
                        <MetricCell label="Broadcast" value={formatRate(selectedRisk.broadcastRate)} />
                        <MetricCell label="Multicast" value={formatRate(selectedRisk.multicastRate)} />
                        <MetricCell
                          label="Unknown unicast"
                          value={formatRate(selectedRisk.unknownUnicastRate)}
                        />
                        <MetricCell
                          label="Utilization"
                          value={
                            selectedRisk.utilization == null
                              ? '—'
                              : `${Number(selectedRisk.utilization).toFixed(1)}%`
                          }
                        />
                        <MetricCell label="Errors" value={formatRate(selectedRisk.errorRate)} />
                        <MetricCell label="Discards" value={formatRate(selectedRisk.discardRate)} />
                        <MetricCell label="CRC" value={formatRate(selectedRisk.crcRate)} />
                        <MetricCell
                          label="Confidence"
                          value={`${Number(selectedRisk.confidence).toFixed(0)}%`}
                        />
                      </div>

                      <div>
                        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                          Contributors
                        </p>
                        <div className="space-y-2">
                          {(selectedRisk.contributors || []).length === 0 ? (
                            <p className="text-sm text-muted-foreground">No active contributors</p>
                          ) : (
                            selectedRisk.contributors.map((c) => (
                              <div
                                key={c.metric}
                                className="flex items-center justify-between gap-2 rounded-md border border-border/60 px-2.5 py-1.5 text-sm"
                              >
                                <span className="capitalize">
                                  {c.metric.replaceAll('_', ' ')}
                                </span>
                                <span className="mono text-xs text-muted-foreground">
                                  val {c.value ?? '—'} · score {c.score} · w{c.weight}
                                </span>
                              </div>
                            ))
                          )}
                        </div>
                      </div>

                      <p className="text-xs text-muted-foreground">
                        Last calculated{' '}
                        {formatDateTime(selectedRisk.timestamp) || '—'}
                      </p>
                    </>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Risk trend</CardTitle>
                </CardHeader>
                <CardContent className="h-56">
                  {!selectedRisk ? (
                    <p className="text-sm text-muted-foreground">
                      Select an interface to load history.
                    </p>
                  ) : selectedHistoryQuery.isLoading ? (
                    <p className="text-sm text-muted-foreground">Loading history…</p>
                  ) : trendData.length < 2 ? (
                    <p className="text-sm text-muted-foreground">
                      Not enough history points yet. Scores append on each calculation cycle.
                    </p>
                  ) : (
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={trendData}>
                        <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                        <XAxis dataKey="label" tick={{ fontSize: 10 }} />
                        <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} />
                        <Tooltip
                          contentStyle={{
                            background: 'hsl(var(--card))',
                            border: '1px solid hsl(var(--border))',
                            borderRadius: 8,
                          }}
                        />
                        <Area
                          type="monotone"
                          dataKey="riskScore"
                          stroke="hsl(var(--primary))"
                          fill="hsl(var(--primary) / 0.2)"
                          strokeWidth={2}
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  )}
                </CardContent>
              </Card>
            </div>
          </div>
        )}

        {riskTotalPages > 1 || riskTotal > riskLimit ? (
          <PaginationControls
            page={riskPage}
            totalPages={Math.max(riskTotalPages, 1)}
            total={riskTotal}
            limit={riskLimit}
            onPageChange={setRiskPage}
            onLimitChange={setRiskLimit}
          />
        ) : null}
      </section>

      {/* ── Confirmation ───────────────────────────────────────────── */}
      <section className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Confirmation</h2>
          <p className="text-sm text-muted-foreground">
            Tracks whether high risk persists across consecutive polling cycles
            before a storm is confirmed. No mitigation is performed here.
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Tracked interfaces
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold">{confirmTotal}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Pending (page)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-warning">{pendingCount}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Confirmed (page)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-success">{confirmedCount}</p>
            </CardContent>
          </Card>
        </div>

        <div className="flex flex-wrap gap-3">
          <Input
            type="search"
            className="max-w-sm"
            placeholder="Search interface, host, reason…"
            value={confirmQuery}
            onChange={(e) => setConfirmQuery(e.target.value)}
          />
          <Select value={confirmStateFilter} onValueChange={setConfirmStateFilter}>
            <SelectTrigger className="w-[200px]">
              <SelectValue placeholder="State" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All states</SelectItem>
              <SelectItem value="NOT_CONFIRMED">Not confirmed</SelectItem>
              <SelectItem value="PENDING">Pending</SelectItem>
              <SelectItem value="CONFIRMED">Confirmed</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {confirmationQuery.isLoading ? (
          <TableSkeleton rows={8} />
        ) : confirmationQuery.isError ? (
          <ErrorState
            title="Unable to load confirmation results"
            message={
              confirmationQuery.error instanceof Error
                ? confirmationQuery.error.message
                : 'Unexpected error'
            }
            onRetry={() => void confirmationQuery.refetch()}
          />
        ) : confirmRows.length === 0 ? (
          <EmptyState
            icon={CheckCircle2}
            title="No confirmation results"
            description="Calculate risk first, then evaluate confirmation. The scheduler confirms after each risk cycle."
          />
        ) : (
          <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Interface</TableHead>
                    <TableHead>State</TableHead>
                    <TableHead>Current risk</TableHead>
                    <TableHead>Highest</TableHead>
                    <TableHead>Average</TableHead>
                    <TableHead>Progress</TableHead>
                    <TableHead>Reason</TableHead>
                    <TableHead>Last updated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {confirmRows.map((row: ConfirmationResult) => (
                    <TableRow
                      key={`${row.deviceId}-${row.interface}-${row._id ?? ''}`}
                    >
                      <TableCell className="font-medium">
                        <div>
                          <Link
                            to={`/interfaces/${row.deviceId}/${encodeURIComponent(row.interface)}`}
                            className="text-primary hover:underline"
                          >
                            {row.interface}
                          </Link>
                          <p className="text-xs text-muted-foreground">
                            {row.hostname || row.ipAddress || row.deviceId}
                          </p>
                        </div>
                      </TableCell>
                      <TableCell>
                        <ConfirmationStateBadge state={String(row.state)} />
                      </TableCell>
                      <TableCell className="mono text-sm">
                        {Number(row.currentRisk).toFixed(1)}
                      </TableCell>
                      <TableCell className="mono text-sm">
                        {Number(row.highestRisk).toFixed(1)}
                      </TableCell>
                      <TableCell className="mono text-sm">
                        {Number(row.averageRisk).toFixed(1)}
                      </TableCell>
                      <TableCell>
                        <ConfirmationProgressBar
                          consecutive={row.consecutiveHighSamples}
                          required={row.requiredSamples}
                          state={String(row.state)}
                        />
                      </TableCell>
                      <TableCell className="max-w-[260px] truncate text-sm text-muted-foreground">
                        {row.reason}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                        <div title={formatDateTime(row.timestamp) || undefined}>
                          {formatRelative(row.timestamp) || '—'}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        )}

        {confirmTotalPages > 1 || confirmTotal > confirmLimit ? (
          <PaginationControls
            page={confirmPage}
            totalPages={Math.max(confirmTotalPages, 1)}
            total={confirmTotal}
            limit={confirmLimit}
            onPageChange={setConfirmPage}
            onLimitChange={setConfirmLimit}
          />
        ) : null}
      </section>

      {/* ── Port Eligibility ───────────────────────────────────────── */}
      <section className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Port Eligibility</h2>
          <p className="text-sm text-muted-foreground">
            Deterministic gate that decides which access ports may enter risk scoring
            and future storm engines.
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Latest evaluations
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold">{total}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Eligible (page)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-success">{eligibleCount}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Not eligible (page)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-danger">{ineligibleCount}</p>
            </CardContent>
          </Card>
        </div>

        <div className="flex flex-wrap gap-3">
          <Input
            type="search"
            className="max-w-sm"
            placeholder="Search interface, host, reason, rule…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <Select value={eligibleFilter} onValueChange={setEligibleFilter}>
            <SelectTrigger className="w-[180px]">
              <SelectValue placeholder="Eligibility" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="eligible">Eligible</SelectItem>
              <SelectItem value="ineligible">Not eligible</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {eligibilityQuery.isLoading ? (
          <TableSkeleton rows={8} />
        ) : eligibilityQuery.isError ? (
          <ErrorState
            title="Unable to load eligibility results"
            message={
              eligibilityQuery.error instanceof Error
                ? eligibilityQuery.error.message
                : 'Unexpected error'
            }
            onRetry={() => void eligibilityQuery.refetch()}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={Shield}
            title="No eligibility results"
            description="Run interface discovery and stats collection, then evaluate ports. The scheduler also evaluates after each stats cycle."
          />
        ) : (
          <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Interface</TableHead>
                    <TableHead>Device</TableHead>
                    <TableHead>Eligibility</TableHead>
                    <TableHead>Reason</TableHead>
                    <TableHead>Failed rule</TableHead>
                    <TableHead>Confidence</TableHead>
                    <TableHead>Classification</TableHead>
                    <TableHead>Evaluated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={`${row.deviceId}-${row.interface}-${row._id ?? ''}`}>
                      <TableCell className="font-medium">
                        {row.deviceId && row.interface ? (
                          <Link
                            to={`/interfaces/${row.deviceId}/${encodeURIComponent(row.interface)}`}
                            className="text-primary hover:underline"
                          >
                            {row.interface}
                          </Link>
                        ) : (
                          row.interface
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">
                            {row.hostname || '—'}
                          </p>
                          <p className="truncate text-xs text-muted-foreground">
                            {row.ipAddress || row.deviceId}
                          </p>
                        </div>
                      </TableCell>
                      <TableCell>
                        <EligibilityBadge eligible={row.eligible} />
                      </TableCell>
                      <TableCell className="max-w-[200px] truncate text-sm">
                        {row.reason}
                      </TableCell>
                      <TableCell>
                        {row.failedRule ? (
                          <Badge variant="outline" className="font-mono text-xs">
                            {row.failedRule}
                          </Badge>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell>{row.confidence}%</TableCell>
                      <TableCell>
                        <div className="flex max-w-[280px] flex-wrap gap-1">
                          <PortClassificationBadges
                            iface={classificationIface(row)}
                            includeMode
                          />
                        </div>
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                        <div title={formatDateTime(row.timestamp) || undefined}>
                          {formatRelative(row.timestamp) || '—'}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        )}

        {totalPages > 1 || total > limit ? (
          <PaginationControls
            page={page}
            totalPages={Math.max(totalPages, 1)}
            total={total}
            limit={limit}
            onPageChange={setPage}
            onLimitChange={setLimit}
          />
        ) : null}
      </section>
    </div>
  )
}

function MetricCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border/50 px-2.5 py-1.5">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mono text-sm font-medium">{value}</p>
    </div>
  )
}
