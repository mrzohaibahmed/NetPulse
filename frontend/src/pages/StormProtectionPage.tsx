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
import { Activity, CheckCircle2, FileJson, RefreshCw, Shield, ShieldCheck } from 'lucide-react'
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
  useOrchestratorMutations,
  useRiskMutations,
  useRiskQuery,
  useSafetyMutations,
  useSafetyQuery,
  useStormConfigQuery,
  useStormIncidentsQuery,
  useMitigationHistoryQuery,
  useMitigationMutations,
  useRecoveryHistoryQuery,
  useRecoveryMutations,
  useSettingsQuery,
  useSettingsMutation,
} from '@/hooks/queries'
import { cn } from '@/lib/utils'
import type {
  ConfirmationResult,
  EligibilityResult,
  NetworkInterface,
  RiskResult,
  SafetyResult,
  StormIncident,
  MitigationLog,
  RecoveryLog,
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

function IncidentTypeBadge({ incidentType }: { incidentType?: string | null }) {
  const value = (incidentType || 'STORM').toUpperCase()
  if (value === 'EMERGENCY') {
    return (
      <Badge variant="danger" className="font-semibold uppercase tracking-wide">
        EMERGENCY
      </Badge>
    )
  }
  if (value === 'MANUAL') {
    return (
      <Badge variant="warning" className="font-semibold uppercase tracking-wide">
        MANUAL
      </Badge>
    )
  }
  return (
    <Badge variant="secondary" className="font-semibold uppercase tracking-wide">
      STORM
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

function SafetyStatusBadge({ status }: { status: string }) {
  const value = (status || 'UNSAFE').toUpperCase()
  if (value === 'SAFE') {
    return (
      <Badge variant="success" className="font-semibold uppercase tracking-wide">
        <span className="h-1.5 w-1.5 rounded-full bg-success" aria-hidden />
        Safe
      </Badge>
    )
  }
  if (value === 'WAITING') {
    return (
      <Badge variant="warning" className="font-semibold uppercase tracking-wide">
        <span className="h-1.5 w-1.5 rounded-full bg-warning" aria-hidden />
        Waiting
      </Badge>
    )
  }
  return (
    <Badge variant="danger" className="font-semibold uppercase tracking-wide">
      <span className="h-1.5 w-1.5 rounded-full bg-danger" aria-hidden />
      Unsafe
    </Badge>
  )
}

function formatCooldown(seconds: number | null | undefined): string {
  const s = Math.max(0, Number(seconds) || 0)
  if (s <= 0) return 'Ready'
  const m = Math.floor(s / 60)
  const rem = s % 60
  if (m <= 0) return `${rem}s`
  return `${m}m ${rem}s`
}

function IncidentStatusBadge({ status }: { status: string }) {
  const value = (status || 'OPEN').toUpperCase()
  if (value === 'READY_FOR_MITIGATION' || value === 'PREPARED') {
    return (
      <Badge variant="success" className="font-semibold uppercase tracking-wide">
        Ready
      </Badge>
    )
  }
  if (value === 'OPEN') {
    return (
      <Badge variant="warning" className="font-semibold uppercase tracking-wide">
        Open
      </Badge>
    )
  }
  return (
    <Badge variant="secondary" className="font-semibold uppercase tracking-wide">
      {status}
    </Badge>
  )
}

function MitigationStatusBadge({ status }: { status: string }) {
  const value = (status || '').toUpperCase()
  if (value === 'SUCCESS') {
    return (
      <Badge variant="success" className="font-semibold uppercase tracking-wide">
        Success
      </Badge>
    )
  }
  if (value === 'ROLLBACK_SUCCESS') {
    return (
      <Badge variant="warning" className="font-semibold uppercase tracking-wide">
        Rolled Back
      </Badge>
    )
  }
  if (value === 'ROLLBACK_FAILURE') {
    return (
      <Badge variant="danger" className="font-semibold uppercase tracking-wide">
        Rollback Failed
      </Badge>
    )
  }
  return (
    <Badge variant="danger" className="font-semibold uppercase tracking-wide">
      {status}
    </Badge>
  )
}

function RecoveryStatusBadge({ status }: { status: string }) {
  const value = (status || '').toUpperCase()
  if (value === 'RECOVERED' || value === 'SUCCESS') {
    return (
      <Badge variant="success" className="font-semibold uppercase tracking-wide">
        Recovered
      </Badge>
    )
  }
  if (value === 'MONITORING') {
    return (
      <Badge variant="warning" className="font-semibold uppercase tracking-wide">
        Stabilizing
      </Badge>
    )
  }
  if (value === 'WAITING') {
    return (
      <Badge variant="secondary" className="font-semibold uppercase tracking-wide">
        Waiting
      </Badge>
    )
  }
  if (value === 'REMITIGATED') {
    return (
      <Badge variant="danger" className="font-semibold uppercase tracking-wide">
        Re-Mitigated
      </Badge>
    )
  }
  return (
    <Badge variant="danger" className="font-semibold uppercase tracking-wide">
      {status}
    </Badge>
  )
}


function JsonSection({
  title,
  open,
  onToggle,
  data,
}: {
  title: string
  open: boolean
  onToggle: () => void
  data: unknown
}) {
  return (
    <div className="rounded-md border border-border/50">
      <button
        type="button"
        className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium"
        onClick={onToggle}
      >
        <span>{title}</span>
        <span className="text-xs text-muted-foreground">{open ? 'Hide' : 'Show'}</span>
      </button>
      {open ? (
        <pre className="max-h-64 overflow-auto border-t border-border/50 bg-secondary/20 p-3 text-xs">
          {JSON.stringify(data ?? {}, null, 2)}
        </pre>
      ) : null}
    </div>
  )
}

function exportIncident(incident: StormIncident) {
  const blob = new Blob([JSON.stringify(incident, null, 2)], {
    type: 'application/json',
  })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${incident.incidentId || 'storm-incident'}.json`
  anchor.click()
  URL.revokeObjectURL(url)
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
  const safetyMutations = useSafetyMutations()
  const orchestratorMutations = useOrchestratorMutations()
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

  const [safetyQueryText, setSafetyQueryText] = useState('')
  const [debouncedSafetyQuery, setDebouncedSafetyQuery] = useState('')
  const [safetyStatusFilter, setSafetyStatusFilter] = useState('all')
  const [safetyPage, setSafetyPage] = useState(1)
  const [safetyLimit, setSafetyLimit] = useState(DEFAULT_LIMIT)
  const [selectedSafety, setSelectedSafety] = useState<SafetyResult | null>(null)

  const [incidentQuery, setIncidentQuery] = useState('')
  const [debouncedIncidentQuery, setDebouncedIncidentQuery] = useState('')
  const [incidentStatusFilter, setIncidentStatusFilter] = useState('all')
  const [incidentPage, setIncidentPage] = useState(1)
  const [incidentLimit, setIncidentLimit] = useState(DEFAULT_LIMIT)
  const [selectedIncident, setSelectedIncident] = useState<StormIncident | null>(null)
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({})

  // Mitigation Engine Hooks and States
  const [mitQuery, setMitQuery] = useState('')
  const [debouncedMitQuery, setDebouncedMitQuery] = useState('')
  const [mitPage, setMitPage] = useState(1)
  const [mitLimit, setMitLimit] = useState(DEFAULT_LIMIT)
  const [selectedMitigation, setSelectedMitigation] = useState<MitigationLog | null>(null)

  const [recQuery, setRecQuery] = useState('')
  const [debouncedRecQuery, setDebouncedRecQuery] = useState('')
  const [recPage, setRecPage] = useState(1)
  const [recLimit, setRecLimit] = useState(DEFAULT_LIMIT)
  const [selectedRecovery, setSelectedRecovery] = useState<RecoveryLog | null>(null)

  const settingsQuery = useSettingsQuery()
  const settingsMutation = useSettingsMutation()
  const mitigationMutations = useMitigationMutations()
  const recoveryMutations = useRecoveryMutations()

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
    const timer = window.setTimeout(() => setDebouncedSafetyQuery(safetyQueryText), 300)
    return () => window.clearTimeout(timer)
  }, [safetyQueryText])

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedIncidentQuery(incidentQuery), 300)
    return () => window.clearTimeout(timer)
  }, [incidentQuery])

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedMitQuery(mitQuery), 300)
    return () => window.clearTimeout(timer)
  }, [mitQuery])

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedRecQuery(recQuery), 300)
    return () => window.clearTimeout(timer)
  }, [recQuery])

  useEffect(() => {
    setPage(1)
  }, [debouncedQuery, eligibleFilter, limit])

  useEffect(() => {
    setRiskPage(1)
  }, [debouncedRiskQuery, severityFilter, riskLimit])

  useEffect(() => {
    setConfirmPage(1)
  }, [debouncedConfirmQuery, confirmStateFilter, confirmLimit])

  useEffect(() => {
    setRecPage(1)
  }, [debouncedRecQuery, recLimit])

  useEffect(() => {
    setSafetyPage(1)
  }, [debouncedSafetyQuery, safetyStatusFilter, safetyLimit])

  useEffect(() => {
    setIncidentPage(1)
  }, [debouncedIncidentQuery, incidentStatusFilter, incidentLimit])

  useEffect(() => {
    setMitPage(1)
  }, [debouncedMitQuery, mitLimit])

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

  const safetyListQuery = useSafetyQuery({
    page: safetyPage,
    limit: safetyLimit,
    q: debouncedSafetyQuery,
    safetyStatus: safetyStatusFilter === 'all' ? undefined : safetyStatusFilter,
  })

  const incidentsQuery = useStormIncidentsQuery({
    page: incidentPage,
    limit: incidentLimit,
    q: debouncedIncidentQuery,
    status: incidentStatusFilter === 'all' ? undefined : incidentStatusFilter,
  })

  const mitigationHistoryQuery = useMitigationHistoryQuery({
    page: mitPage,
    limit: mitLimit,
    q: debouncedMitQuery,
  })

  const recoveryHistoryQuery = useRecoveryHistoryQuery({
    page: recPage,
    limit: recLimit,
    q: debouncedRecQuery,
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

  const safetyRows = safetyListQuery.data?.data ?? []
  const safetyTotal = safetyListQuery.data?.total ?? safetyListQuery.data?.count ?? 0
  const safetyTotalPages = safetyListQuery.data?.totalPages ?? 1

  const incidentRows = incidentsQuery.data?.data ?? []
  const incidentTotal = incidentsQuery.data?.total ?? incidentsQuery.data?.count ?? 0
  const incidentTotalPages = incidentsQuery.data?.totalPages ?? 1

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

  const safeCount = useMemo(
    () => safetyRows.filter((r) => String(r.status).toUpperCase() === 'SAFE').length,
    [safetyRows],
  )
  const unsafeCount = useMemo(
    () => safetyRows.filter((r) => String(r.status).toUpperCase() === 'UNSAFE').length,
    [safetyRows],
  )
  const waitingCount = useMemo(
    () => safetyRows.filter((r) => String(r.status).toUpperCase() === 'WAITING').length,
    [safetyRows],
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
    confirmationMutations.evaluateAll.isPending ||
    safetyMutations.evaluateAll.isPending ||
    orchestratorMutations.prepareAll.isPending ||
    mitigationMutations.execute.isPending ||
    mitigationMutations.rollback.isPending ||
    recoveryMutations.execute.isPending ||
    recoveryMutations.retry.isPending

  const refreshAll = () => {
    void eligibilityQuery.refetch()
    void riskListQuery.refetch()
    void confirmationQuery.refetch()
    void safetyListQuery.refetch()
    void incidentsQuery.refetch()
    void mitigationHistoryQuery.refetch()
    void recoveryHistoryQuery.refetch()
    if (selectedRisk) void selectedHistoryQuery.refetch()
  }

  const toggleSection = (key: string) => {
    setExpandedSections((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title="Storm Protection"
        description="Eligibility → risk → confirmation → safety → diagnostics → incident prepare. No interface shutdown is performed here."
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
                <Button
                  type="button"
                  disabled={
                    isBusy || stormConfig.data?.safety?.safetyEnabled === false
                  }
                  onClick={() => safetyMutations.evaluateAll.mutate()}
                >
                  <ShieldCheck className="mr-2 h-4 w-4" />
                  {safetyMutations.evaluateAll.isPending
                    ? 'Checking…'
                    : 'Evaluate safety'}
                </Button>
                <Button
                  type="button"
                  disabled={isBusy}
                  onClick={() => orchestratorMutations.prepareAll.mutate()}
                >
                  <FileJson className="mr-2 h-4 w-4" />
                  {orchestratorMutations.prepareAll.isPending
                    ? 'Preparing…'
                    : 'Prepare incidents'}
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

      {/* ── Safety ─────────────────────────────────────────────────── */}
      <section className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Safety</h2>
          <p className="text-sm text-muted-foreground">
            Final pre-mitigation gate for confirmed storms. Validates device,
            SSH, automation, locks, cooldown, and health — never executes mitigation.
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Safe (page)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-success">{safeCount}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Waiting (page)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-warning">{waitingCount}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Unsafe (page)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-danger">{unsafeCount}</p>
            </CardContent>
          </Card>
        </div>

        <div className="flex flex-wrap gap-3">
          <Input
            type="search"
            className="max-w-sm"
            placeholder="Search interface, host, reason, rule…"
            value={safetyQueryText}
            onChange={(e) => setSafetyQueryText(e.target.value)}
          />
          <Select value={safetyStatusFilter} onValueChange={setSafetyStatusFilter}>
            <SelectTrigger className="w-[180px]">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="SAFE">Safe</SelectItem>
              <SelectItem value="WAITING">Waiting</SelectItem>
              <SelectItem value="UNSAFE">Unsafe</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {safetyListQuery.isLoading ? (
          <TableSkeleton rows={8} />
        ) : safetyListQuery.isError ? (
          <ErrorState
            title="Unable to load safety results"
            message={
              safetyListQuery.error instanceof Error
                ? safetyListQuery.error.message
                : 'Unexpected error'
            }
            onRetry={() => void safetyListQuery.refetch()}
          />
        ) : safetyRows.length === 0 ? (
          <EmptyState
            icon={ShieldCheck}
            title="No safety results"
            description="Confirm a storm first, then evaluate safety. The scheduler runs safety after confirmation."
          />
        ) : (
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(300px,1fr)]">
            <Card>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Interface</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Reason</TableHead>
                      <TableHead>Failed rule</TableHead>
                      <TableHead>Confidence</TableHead>
                      <TableHead>Cooldown</TableHead>
                      <TableHead>Attempts</TableHead>
                      <TableHead>Updated</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {safetyRows.map((row) => {
                      const active =
                        selectedSafety?.deviceId === row.deviceId &&
                        selectedSafety?.interface === row.interface
                      return (
                        <TableRow
                          key={`${row.deviceId}-${row.interface}-${row._id ?? ''}`}
                          className={cn('cursor-pointer', active && 'bg-primary/10')}
                          onClick={() => setSelectedSafety(row)}
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
                            <SafetyStatusBadge status={String(row.status)} />
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
                          <TableCell>{Number(row.confidence).toFixed(0)}%</TableCell>
                          <TableCell className="mono text-xs">
                            {formatCooldown(row.cooldownRemainingSeconds)}
                          </TableCell>
                          <TableCell className="mono text-xs">
                            {row.mitigationAttempts ?? 0}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                            {formatRelative(row.timestamp) || '—'}
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">
                  {selectedSafety
                    ? `${selectedSafety.interface} checks`
                    : 'Select an interface'}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {!selectedSafety ? (
                  <p className="text-sm text-muted-foreground">
                    Click a row to inspect check results, health, and automation.
                  </p>
                ) : (
                  <>
                    <div className="flex flex-wrap items-center gap-2">
                      <SafetyStatusBadge status={String(selectedSafety.status)} />
                      <span className="text-sm text-muted-foreground">
                        Confidence {Number(selectedSafety.confidence).toFixed(0)}%
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-sm">
                      <div className="rounded-md border border-border/50 px-2.5 py-1.5">
                        <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
                          CPU
                        </p>
                        <p className="mono font-medium">
                          {selectedSafety.cpuPercent == null
                            ? '—'
                            : `${Number(selectedSafety.cpuPercent).toFixed(1)}%`}
                        </p>
                      </div>
                      <div className="rounded-md border border-border/50 px-2.5 py-1.5">
                        <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
                          Memory
                        </p>
                        <p className="mono font-medium">
                          {selectedSafety.memoryPercent == null
                            ? '—'
                            : `${Number(selectedSafety.memoryPercent).toFixed(1)}%`}
                        </p>
                      </div>
                      <div className="rounded-md border border-border/50 px-2.5 py-1.5">
                        <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
                          Cooldown
                        </p>
                        <p className="mono font-medium">
                          {formatCooldown(selectedSafety.cooldownRemainingSeconds)}
                        </p>
                      </div>
                      <div className="rounded-md border border-border/50 px-2.5 py-1.5">
                        <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
                          Attempts
                        </p>
                        <p className="mono font-medium">
                          {selectedSafety.mitigationAttempts ?? 0}
                        </p>
                      </div>
                    </div>
                    <div className="space-y-1.5">
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        Check results
                      </p>
                      {Object.entries(selectedSafety.checks || {}).map(([key, value]) => {
                        const hazardKeys = new Set([
                          'maintenanceMode',
                          'deviceLocked',
                          'interfaceLocked',
                          'mitigationRunning',
                        ])
                        const ok = hazardKeys.has(key) ? !value : Boolean(value)
                        return (
                          <div
                            key={key}
                            className="flex items-center justify-between rounded-md border border-border/50 px-2.5 py-1 text-sm"
                          >
                            <span className="text-muted-foreground">{key}</span>
                            <Badge
                              variant={ok ? 'success' : 'danger'}
                              className="capitalize"
                            >
                              {String(value)}
                            </Badge>
                          </div>
                        )
                      })}
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
          </div>
        )}

        {safetyTotalPages > 1 || safetyTotal > safetyLimit ? (
          <PaginationControls
            page={safetyPage}
            totalPages={Math.max(safetyTotalPages, 1)}
            total={safetyTotal}
            limit={safetyLimit}
            onPageChange={setSafetyPage}
            onLimitChange={setSafetyLimit}
          />
        ) : null}
      </section>

      {/* ── Storm Incidents ────────────────────────────────────────── */}
      <section className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Storm Incidents</h2>
          <p className="text-sm text-muted-foreground">
            Immutable pre-mitigation evidence packages. Diagnostics are captured
            before every prepare — shutdown is not executed here.
          </p>
        </div>

        <div className="flex flex-wrap gap-3">
          <Input
            type="search"
            className="max-w-sm"
            placeholder="Search incident, interface, host…"
            value={incidentQuery}
            onChange={(e) => setIncidentQuery(e.target.value)}
          />
          <Select value={incidentStatusFilter} onValueChange={setIncidentStatusFilter}>
            <SelectTrigger className="w-[220px]">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="OPEN">Open</SelectItem>
              <SelectItem value="READY_FOR_MITIGATION">Ready for mitigation</SelectItem>
              <SelectItem value="PREPARED">Prepared</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {incidentsQuery.isLoading ? (
          <TableSkeleton rows={6} />
        ) : incidentsQuery.isError ? (
          <ErrorState
            title="Unable to load storm incidents"
            message={
              incidentsQuery.error instanceof Error
                ? incidentsQuery.error.message
                : 'Unexpected error'
            }
            onRetry={() => void incidentsQuery.refetch()}
          />
        ) : incidentRows.length === 0 ? (
          <EmptyState
            icon={FileJson}
            title="No storm incidents"
            description="When safety passes, the orchestrator captures diagnostics and creates one incident per storm."
          />
        ) : (
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(320px,1fr)]">
            <Card>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Incident ID</TableHead>
                      <TableHead>Device</TableHead>
                      <TableHead>Interface</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead>Severity</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Risk</TableHead>
                      <TableHead>Confirm</TableHead>
                      <TableHead>Safety</TableHead>
                      <TableHead>Created</TableHead>
                      <TableHead>Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {incidentRows.map((row) => {
                      const active = selectedIncident?.incidentId === row.incidentId
                      return (
                        <TableRow
                          key={row.incidentId}
                          className={cn('cursor-pointer', active && 'bg-primary/10')}
                          onClick={() => {
                            setSelectedIncident(row)
                            setExpandedSections({})
                          }}
                        >
                          <TableCell className="mono text-xs font-medium">
                            {row.incidentId}
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
                          <TableCell className="font-medium">{row.interface}</TableCell>
                          <TableCell>
                            <IncidentTypeBadge incidentType={row.incidentType || row.type} />
                          </TableCell>
                          <TableCell>
                            <SeverityBadge severity={row.severity} />
                          </TableCell>
                          <TableCell>
                            <IncidentStatusBadge status={row.status} />
                          </TableCell>
                          <TableCell className="mono text-xs">
                            {row.trigger?.risk == null ? '—' : Number(row.trigger.risk).toFixed(0)}
                          </TableCell>
                          <TableCell>
                            {row.trigger?.confirmation ? (
                              <Badge variant="success">Yes</Badge>
                            ) : (
                              <Badge variant="muted">No</Badge>
                            )}
                          </TableCell>
                          <TableCell>
                            {row.trigger?.safety ? (
                              <Badge variant="success">Yes</Badge>
                            ) : (
                              <Badge variant="danger">No</Badge>
                            )}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                            {formatRelative(row.createdAt) || '—'}
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-wrap gap-1">
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  setSelectedIncident(row)
                                  setExpandedSections({
                                    interface: true,
                                    switchport: true,
                                    mac: true,
                                    neighbor: true,
                                    timeline: true,
                                  })
                                }}
                              >
                                View
                              </Button>
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  setSelectedIncident(row)
                                  setExpandedSections({
                                    interface: true,
                                    switchport: true,
                                    mac: true,
                                  })
                                }}
                              >
                                Diagnostics
                              </Button>
                              <Button
                                type="button"
                                size="sm"
                                variant="secondary"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  exportIncident(row)
                                }}
                              >
                                Export
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">
                  {selectedIncident
                    ? selectedIncident.incidentId
                    : 'Select an incident'}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {!selectedIncident ? (
                  <p className="text-sm text-muted-foreground">
                    Click a row to inspect evidence snapshots and timeline.
                  </p>
                ) : (
                  <>
                    <div className="flex flex-wrap items-center gap-2">
                      <SeverityBadge severity={selectedIncident.severity} />
                      <IncidentStatusBadge status={selectedIncident.status} />
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {selectedIncident.interface} ·{' '}
                      {selectedIncident.hostname || selectedIncident.deviceId}
                    </p>
                    <JsonSection
                      title="Interface Snapshot"
                      open={Boolean(expandedSections.interface)}
                      onToggle={() => toggleSection('interface')}
                      data={selectedIncident.interfaceSnapshot}
                    />
                    <JsonSection
                      title="Switchport Snapshot"
                      open={Boolean(expandedSections.switchport)}
                      onToggle={() => toggleSection('switchport')}
                      data={selectedIncident.switchportSnapshot}
                    />
                    <JsonSection
                      title="MAC Table"
                      open={Boolean(expandedSections.mac)}
                      onToggle={() => toggleSection('mac')}
                      data={selectedIncident.macTable}
                    />
                    <JsonSection
                      title="Neighbor"
                      open={Boolean(expandedSections.neighbor)}
                      onToggle={() => toggleSection('neighbor')}
                      data={selectedIncident.neighbor}
                    />
                    <JsonSection
                      title="Timeline"
                      open={Boolean(expandedSections.timeline)}
                      onToggle={() => toggleSection('timeline')}
                      data={selectedIncident.timeline}
                    />
                    {isAdmin && (
                      <div className="border-t border-border/50 pt-3 mt-3 space-y-2">
                        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                          Mitigation Controls
                        </p>
                        {['READY_FOR_MITIGATION', 'PREPARED', 'OPEN', 'MITIGATION_FAILED'].includes(selectedIncident.status) && (
                          <Button
                            type="button"
                            className="w-full bg-destructive text-destructive-foreground hover:bg-destructive/90"
                            disabled={mitigationMutations.execute.isPending}
                            onClick={() =>
                              mitigationMutations.execute.mutate({
                                incidentId: selectedIncident.incidentId || '',
                                strategy: 'SHUTDOWN',
                              })
                            }
                          >
                            {mitigationMutations.execute.isPending ? 'Executing Shutdown…' : 'Execute Shutdown'}
                          </Button>
                        )}
                        {selectedIncident.status === 'MITIGATED' && (
                          <div className="flex gap-2">
                            <Button
                              type="button"
                              variant="outline"
                              className="flex-1 border-destructive text-destructive hover:bg-destructive/10"
                              disabled={mitigationMutations.rollback.isPending}
                              onClick={() =>
                                mitigationMutations.rollback.mutate({
                                  incidentId: selectedIncident.incidentId || '',
                                })
                              }
                            >
                              {mitigationMutations.rollback.isPending ? 'Rolling back…' : 'Rollback'}
                            </Button>
                            <Button
                              type="button"
                              variant="secondary"
                              className="flex-1"
                              disabled={mitigationMutations.execute.isPending}
                              onClick={() =>
                                mitigationMutations.execute.mutate({
                                  incidentId: selectedIncident.incidentId || '',
                                  strategy: 'NO_SHUTDOWN',
                                })
                              }
                            >
                              {mitigationMutations.execute.isPending ? 'Recovering…' : 'Recover Port'}
                            </Button>
                          </div>
                        )}
                      </div>
                    )}
                    {isAdmin && ['MITIGATED', 'RECOVERY_FAILED', 'MITIGATION_FAILED'].includes(selectedIncident.status) && (
                      <div className="border-t border-border/50 pt-3 mt-3 space-y-2">
                        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                          Recovery Controls
                        </p>
                        <div className="flex gap-2">
                          <Button
                            type="button"
                            variant="secondary"
                            className="flex-1"
                            disabled={recoveryMutations.retry.isPending}
                            onClick={() =>
                              recoveryMutations.retry.mutate({
                                incidentId: selectedIncident.incidentId || '',
                              })
                            }
                          >
                            {recoveryMutations.retry.isPending ? 'Retrying…' : 'Retry Recovery'}
                          </Button>
                          <Button
                            type="button"
                            variant="outline"
                            className="flex-1 border-primary text-primary hover:bg-primary/10"
                            disabled={recoveryMutations.execute.isPending}
                            onClick={() =>
                              recoveryMutations.execute.mutate({
                                incidentId: selectedIncident.incidentId || '',
                                force: true,
                              })
                            }
                          >
                            {recoveryMutations.execute.isPending ? 'Recovering…' : 'Force Recovery'}
                          </Button>
                        </div>
                      </div>
                    )}
                    <Button
                      type="button"
                      variant="outline"
                      className="w-full"
                      onClick={() => exportIncident(selectedIncident)}
                    >
                      <FileJson className="mr-2 h-4 w-4" />
                      Export Incident
                    </Button>
                  </>
                )}
              </CardContent>
            </Card>
          </div>
        )}

        {incidentTotalPages > 1 || incidentTotal > incidentLimit ? (
          <PaginationControls
            page={incidentPage}
            totalPages={Math.max(incidentTotalPages, 1)}
            total={incidentTotal}
            limit={incidentLimit}
            onPageChange={setIncidentPage}
            onLimitChange={setIncidentLimit}
          />
        ) : null}
      </section>

      {/* ── Mitigation History ──────────────────────────────────────── */}
      <section className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Mitigation Orchestrator & Execution</h2>
          <p className="text-sm text-muted-foreground">
            Execute manual or automatic port shutdown mitigations and rollback recovery.
          </p>
        </div>

        {/* Runtime Mitigation Mode Configuration */}
        <Card className="border-primary/20 bg-secondary/10">
          <CardContent className="flex flex-wrap items-center justify-between gap-4 p-4">
            <div className="space-y-1">
              <p className="text-sm font-semibold">Mitigation Automation Mode</p>
              <p className="text-xs text-muted-foreground">
                Current active mode:{' '}
                <span className="font-semibold text-primary uppercase">
                  {settingsQuery.data?.mitigationMode || 'manual'}
                </span>
              </p>
            </div>
            {isAdmin ? (
              <div className="flex gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant={settingsQuery.data?.mitigationMode === 'automatic' ? 'default' : 'outline'}
                  disabled={settingsMutation.isPending}
                  onClick={() => settingsMutation.mutate({ mitigationMode: 'automatic' })}
                >
                  Automatic Mode
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={settingsQuery.data?.mitigationMode === 'manual' ? 'default' : 'outline'}
                  disabled={settingsMutation.isPending}
                  onClick={() => settingsMutation.mutate({ mitigationMode: 'manual' })}
                >
                  Manual Approval
                </Button>
              </div>
            ) : (
              <Badge variant="outline" className="uppercase">
                Admin Configurable Only
              </Badge>
            )}
          </CardContent>
        </Card>

        <div className="flex flex-wrap gap-3">
          <Input
            type="search"
            className="max-w-sm"
            placeholder="Search mitigation log by incident..."
            value={mitQuery}
            onChange={(e) => setMitQuery(e.target.value)}
          />
        </div>

        {mitigationHistoryQuery.isLoading ? (
          <TableSkeleton rows={6} />
        ) : mitigationHistoryQuery.isError ? (
          <ErrorState
            title="Unable to load mitigation history"
            message={
              mitigationHistoryQuery.error instanceof Error
                ? mitigationHistoryQuery.error.message
                : 'Unexpected error'
            }
            onRetry={() => void mitigationHistoryQuery.refetch()}
          />
        ) : (mitigationHistoryQuery.data?.data ?? []).length === 0 ? (
          <EmptyState
            icon={Activity}
            title="No mitigation logs found"
            description="Run a port mitigation strategy manually from the Storm Incidents panel, or enable Automatic Mode for automated executions."
          />
        ) : (
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(320px,1fr)]">
            <Card>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Incident ID</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead>Interface</TableHead>
                      <TableHead>Device</TableHead>
                      <TableHead>Strategy</TableHead>
                      <TableHead>Execution Status</TableHead>
                      <TableHead>Rollback Status</TableHead>
                      <TableHead>Operator</TableHead>
                      <TableHead>Execution Time</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(mitigationHistoryQuery.data?.data ?? []).map((row) => {
                      const active = selectedMitigation?._id === row._id
                      return (
                        <TableRow
                          key={row._id || row.incidentId}
                          className={cn('cursor-pointer', active && 'bg-primary/10')}
                          onClick={() => setSelectedMitigation(row)}
                        >
                          <TableCell className="mono text-xs font-medium">
                            {row.incidentId}
                          </TableCell>
                          <TableCell>
                            {row.emergency ? (
                              <Badge variant="danger" className="font-semibold uppercase tracking-wide">
                                EMERGENCY
                              </Badge>
                            ) : (
                              <Badge variant="secondary" className="font-semibold uppercase tracking-wide">
                                STORM
                              </Badge>
                            )}
                          </TableCell>
                          <TableCell className="font-medium">{row.interface}</TableCell>
                          <TableCell>
                            <span className="truncate text-xs font-mono text-muted-foreground block max-w-[120px]">
                              {row.deviceId}
                            </span>
                          </TableCell>
                          <TableCell className="text-xs uppercase font-semibold text-sky-400">
                            {row.strategy}
                          </TableCell>
                          <TableCell>
                            <MitigationStatusBadge status={row.status} />
                          </TableCell>
                          <TableCell>
                            {row.rollbackPerformed ? (
                              <Badge variant="warning">Performed</Badge>
                            ) : (
                              <span className="text-muted-foreground text-xs">—</span>
                            )}
                          </TableCell>
                          <TableCell className="text-sm font-mono text-muted-foreground">
                            {row.operator}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                            {formatRelative(row.timestamp) || '—'}
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">
                  {selectedMitigation
                    ? `Mitigation for ${selectedMitigation.incidentId}`
                    : 'Select a log row'}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {!selectedMitigation ? (
                  <p className="text-sm text-muted-foreground">
                    Click a row to inspect execution command logs and verification outputs.
                  </p>
                ) : (
                  <>
                    <div className="space-y-1">
                      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        Mitigation Context
                      </p>
                      <div className="text-sm space-y-1">
                        <p>Interface: <span className="font-semibold text-primary">{selectedMitigation.interface}</span></p>
                        <p>Operator: <span className="font-mono text-muted-foreground">{selectedMitigation.operator}</span></p>
                        {selectedMitigation.emergency ? (
                          <p>
                            Type:{' '}
                            <Badge variant="danger" className="font-semibold uppercase tracking-wide">
                              EMERGENCY
                            </Badge>
                          </p>
                        ) : null}
                        {selectedMitigation.reason ? (
                          <p>Reason: <span className="text-muted-foreground">{selectedMitigation.reason}</span></p>
                        ) : null}
                        {selectedMitigation.executionTimeMs != null ? (
                          <p>
                            Duration:{' '}
                            <span className="font-mono text-muted-foreground">
                              {selectedMitigation.executionTimeMs} ms
                            </span>
                          </p>
                        ) : null}
                        <p>Status: <MitigationStatusBadge status={selectedMitigation.status} /></p>
                        <p>Rollback Triggered: <span className="font-semibold">{selectedMitigation.rollbackPerformed ? 'Yes' : 'No'}</span></p>
                      </div>
                    </div>

                    <div className="space-y-1">
                      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        Executed Command Log (Sanitized)
                      </p>
                      <div className="rounded-md bg-zinc-950 p-3 font-mono text-xs text-green-400 overflow-auto max-h-48 border border-border">
                        {selectedMitigation.commandsExecuted.map((cmd, idx) => (
                          <div key={idx} className="leading-relaxed">
                            <span className="text-zinc-600 mr-2">$</span>
                            {cmd}
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="space-y-1">
                      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        Verification / Output Detail
                      </p>
                      <pre className="rounded-md bg-zinc-950/80 p-3 font-mono text-xs text-zinc-300 overflow-auto max-h-48 border border-border">
                        {JSON.stringify(selectedMitigation.verificationResult, null, 2)}
                      </pre>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
          </div>
        )}

        {(mitigationHistoryQuery.data?.totalPages ?? 1) > 1 ||
        (mitigationHistoryQuery.data?.total ?? 0) > mitLimit ? (
          <PaginationControls
            page={mitPage}
            totalPages={Math.max(mitigationHistoryQuery.data?.totalPages ?? 1, 1)}
            total={mitigationHistoryQuery.data?.total ?? 0}
            limit={mitLimit}
            onPageChange={setMitPage}
            onLimitChange={setMitLimit}
          />
        ) : null}
      </section>

      {/* ── Recovery Lifecycle & History ────────────────────────────── */}
      <section className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Enterprise Recovery Engine</h2>
          <p className="text-sm text-muted-foreground">
            Automatic recovery and traffic stabilization checking for mitigated interfaces.
          </p>
        </div>

        {/* Runtime Auto Recovery Settings Toggle */}
        <Card className="border-primary/20 bg-secondary/10">
          <CardContent className="flex flex-wrap items-center justify-between gap-4 p-4">
            <div className="space-y-1">
              <p className="text-sm font-semibold">Auto Recovery Automation</p>
              <p className="text-xs text-muted-foreground">
                Automatic port recovery is currently{' '}
                <span className="font-semibold text-primary uppercase">
                  {settingsQuery.data?.autoRecovery ? 'enabled' : 'disabled'}
                </span>
                .
              </p>
            </div>
            {isAdmin ? (
              <div className="flex flex-wrap items-center gap-4">
                <div className="flex gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant={settingsQuery.data?.autoRecovery ? 'default' : 'outline'}
                    disabled={settingsMutation.isPending}
                    onClick={() => settingsMutation.mutate({ autoRecovery: true })}
                  >
                    Enable Auto Recovery
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant={!settingsQuery.data?.autoRecovery ? 'default' : 'outline'}
                    disabled={settingsMutation.isPending}
                    onClick={() => settingsMutation.mutate({ autoRecovery: false })}
                  >
                    Disable Auto Recovery
                  </Button>
                </div>
                <div className="text-xs text-muted-foreground border-l border-border/80 pl-4 space-y-0.5">
                  <p>Cooldown: <span className="font-semibold">{settingsQuery.data?.cooldownMinutes || 5} min</span></p>
                  <p>Stabilization: <span className="font-semibold">{settingsQuery.data?.stabilizationSeconds || 60} sec</span></p>
                  <p>Max Retries: <span className="font-semibold">{settingsQuery.data?.maximumRecoveryAttempts || 3}</span></p>
                </div>
              </div>
            ) : (
              <Badge variant="outline" className="uppercase">
                Admin Configurable Only
              </Badge>
            )}
          </CardContent>
        </Card>

        <div className="flex flex-wrap gap-3">
          <Input
            type="search"
            className="max-w-sm"
            placeholder="Search recovery log by incident..."
            value={recQuery}
            onChange={(e) => setRecQuery(e.target.value)}
          />
        </div>

        {recoveryHistoryQuery.isLoading ? (
          <TableSkeleton rows={6} />
        ) : recoveryHistoryQuery.isError ? (
          <ErrorState
            title="Unable to load recovery history"
            message={
              recoveryHistoryQuery.error instanceof Error
                ? recoveryHistoryQuery.error.message
                : 'Unexpected error'
            }
            onRetry={() => void recoveryHistoryQuery.refetch()}
          />
        ) : (recoveryHistoryQuery.data?.data ?? []).length === 0 ? (
          <EmptyState
            icon={Activity}
            title="No recovery logs found"
            description="Ports in MITIGATED status will automatically trigger recovery attempts when their cooldown and safety policies pass."
          />
        ) : (
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(320px,1fr)]">
            <Card>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Incident ID</TableHead>
                      <TableHead>Interface</TableHead>
                      <TableHead>Device ID</TableHead>
                      <TableHead>Recovery Status</TableHead>
                      <TableHead>Attempts</TableHead>
                      <TableHead>Timestamp</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(recoveryHistoryQuery.data?.data ?? []).map((row) => {
                      const active = selectedRecovery?._id === row._id
                      return (
                        <TableRow
                          key={row._id || row.incidentId}
                          className={cn('cursor-pointer', active && 'bg-primary/10')}
                          onClick={() => setSelectedRecovery(row)}
                        >
                          <TableCell className="mono text-xs font-medium">
                            {row.incidentId}
                          </TableCell>
                          <TableCell className="font-medium">{row.interface}</TableCell>
                          <TableCell>
                            <span className="truncate text-xs font-mono text-muted-foreground block max-w-[120px]">
                              {row.deviceId}
                            </span>
                          </TableCell>
                          <TableCell>
                            <RecoveryStatusBadge status={row.recoveryStatus} />
                          </TableCell>
                          <TableCell className="text-sm">
                            {row.retryCount} attempts
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                            {formatRelative(row.timestamp) || '—'}
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">
                  {selectedRecovery
                    ? `Recovery details for ${selectedRecovery.incidentId}`
                    : 'Select a recovery log row'}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {!selectedRecovery ? (
                  <p className="text-sm text-muted-foreground">
                    Click a row to inspect recovery verification outputs and traffic metrics.
                  </p>
                ) : (
                  <>
                    <div className="space-y-1">
                      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        Recovery Context
                      </p>
                      <div className="text-sm space-y-1">
                        <p>Interface: <span className="font-semibold text-primary">{selectedRecovery.interface}</span></p>
                        <p>Attempts Run: <span className="font-semibold">{selectedRecovery.retryCount}</span></p>
                        <p>Status: <RecoveryStatusBadge status={selectedRecovery.recoveryStatus} /></p>
                      </div>
                    </div>

                    <div className="space-y-1">
                      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        Verification Output / CLI Log
                      </p>
                      {selectedRecovery.verificationResult?.output ? (
                        <pre className="rounded-md bg-zinc-950 p-3 font-mono text-xs text-zinc-300 overflow-auto max-h-48 border border-border">
                          {selectedRecovery.verificationResult.output}
                        </pre>
                      ) : selectedRecovery.verificationResult?.error ? (
                        <div className="rounded-md bg-destructive/10 p-3 text-xs text-destructive border border-destructive/20 font-mono">
                          {selectedRecovery.verificationResult.error}
                        </div>
                      ) : (
                        <div className="text-xs text-muted-foreground italic">No output captured.</div>
                      )}
                    </div>

                    {selectedRecovery.verificationResult?.stats && (
                      <div className="space-y-2">
                        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                          Post-Recovery Traffic Statistics
                        </p>
                        <div className="grid grid-cols-2 gap-2 text-xs border border-border/80 rounded-md p-3 bg-secondary/5">
                          <p>Admin Status: <span className="font-semibold">{selectedRecovery.verificationResult.stats.adminStatus || '—'}</span></p>
                          <p>Oper Status: <span className="font-semibold">{selectedRecovery.verificationResult.stats.operStatus || '—'}</span></p>
                          <p>Broadcast Rate: <span className="font-mono text-muted-foreground">{selectedRecovery.verificationResult.stats.broadcastRate || 0} pps</span></p>
                          <p>Multicast Rate: <span className="font-mono text-muted-foreground">{selectedRecovery.verificationResult.stats.multicastRate || 0} pps</span></p>
                          <p>Utilization: <span className="font-mono text-muted-foreground">{(selectedRecovery.verificationResult.stats.utilization || 0.0).toFixed(2)}%</span></p>
                          <p>Errors: <span className="font-mono text-muted-foreground">In: {selectedRecovery.verificationResult.stats.inputErrors || 0} / Out: {selectedRecovery.verificationResult.stats.outputErrors || 0}</span></p>
                          <p>CRC: <span className="font-mono text-muted-foreground">{selectedRecovery.verificationResult.stats.crc || 0}</span></p>
                          <p>Discards: <span className="font-mono text-muted-foreground">{selectedRecovery.verificationResult.stats.discards || 0}</span></p>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </CardContent>
            </Card>
          </div>
        )}

        {(recoveryHistoryQuery.data?.totalPages ?? 1) > 1 ||
        (recoveryHistoryQuery.data?.total ?? 0) > recLimit ? (
          <PaginationControls
            page={recPage}
            totalPages={Math.max(recoveryHistoryQuery.data?.totalPages ?? 1, 1)}
            total={recoveryHistoryQuery.data?.total ?? 0}
            limit={recLimit}
            onPageChange={setRecPage}
            onLimitChange={setRecLimit}
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
