import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  Activity,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  FileJson,
  Network,
  RefreshCw,
  Shield,
  ShieldCheck,
} from 'lucide-react'
import { PortClassificationBadges } from '@/components/interfaces/InterfaceStatusBadge'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { TableSkeleton } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Input } from '@/shared/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import { useAuth } from '@/shared/auth/AuthContext'
import { getStormIncident } from '@/api'
import { useClientPagination } from '@/hooks/useClientPagination'
import {
  useConfirmationMutations,
  useConfirmationQuery,
  useDevicesQuery,
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

const FETCH_LIMIT = 500
const DEFAULT_SWITCHES_PER_PAGE = 10
const DEFAULT_SECTION_ROWS_PER_PAGE = 10
const COLLAPSE_KEY = 'netpulse.stormProtection.collapsedDevices'

type SwitchStormSectionData = {
  deviceId: string
  hostname: string
  ipAddress: string
  vendor: string
  status: string
  monitor: boolean
  eligibility: EligibilityResult[]
  risk: RiskResult[]
  confirmation: ConfirmationResult[]
  safety: SafetyResult[]
  incidents: StormIncident[]
  mitigation: MitigationLog[]
  recovery: RecoveryLog[]
}

function isManagedSwitch(deviceType: string | null | undefined): boolean {
  return (deviceType || '').trim().toLowerCase() === 'switch'
}

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

const SOURCE_LABELS: Record<string, string> = {
  LIKELY_SOURCE: 'Likely Source',
  POSSIBLE_SOURCE: 'Possible Source',
  LIKELY_FORWARDER: 'Likely Forwarder',
  LIKELY_RECEIVER: 'Likely Receiver',
  NORMAL: 'Normal',
  UNKNOWN: 'Unknown',
}

function SourceBadge({
  classification,
  confidence,
}: {
  classification?: string | null
  confidence?: number | null
}) {
  if (!classification) return null
  const variant =
    classification === 'LIKELY_SOURCE'
      ? 'danger'
      : classification === 'POSSIBLE_SOURCE'
        ? 'warning'
        : classification === 'LIKELY_RECEIVER'
          ? 'outline'
          : classification === 'NORMAL'
            ? 'success'
            : 'secondary'
  const label = SOURCE_LABELS[classification] || classification
  return (
    <Badge variant={variant} className="font-semibold tracking-wide">
      {label}
      {confidence != null ? ` · ${Number(confidence).toFixed(0)}%` : ''}
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
            className={cn('h-2 flex-1 rounded-sm', index < filled ? tone : 'bg-secondary')}
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
  if (value === 'BLOCKED') {
    return (
      <Badge variant="warning" className="font-semibold uppercase tracking-wide">
        Blocked
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

const RECOVERY_CHECK_LABELS: Record<string, string> = {
  stormCleared: 'Storm Cleared',
  riskBelowThreshold: 'Risk Below Threshold',
  cooldownExpired: 'Cooldown Complete',
  deviceReachable: 'Device Reachable',
  sshReachable: 'SSH Reachable',
  interfaceAdminDown: 'Interface Admin Down',
  noNewerActiveIncident: 'No Newer Active Incident',
  recoveryLockAvailable: 'Recovery Lock Available',
}

function RecoveryChecksList({
  checks,
  failedRule,
}: {
  checks?: Record<string, boolean | null> | null
  failedRule?: string | null
}) {
  const entries = Object.entries(checks || {})
  if (entries.length === 0) {
    return (
      <p className="text-xs italic text-muted-foreground">No recovery safety checks recorded.</p>
    )
  }
  return (
    <div className="space-y-1.5">
      {failedRule ? (
        <p className="text-xs text-muted-foreground">
          Failed rule: <span className="font-semibold text-destructive">{failedRule}</span>
        </p>
      ) : null}
      {entries.map(([key, value]) => {
        const label = RECOVERY_CHECK_LABELS[key] || key
        const pending = value === null || value === undefined
        const ok = value === true
        return (
          <div
            key={key}
            className="flex items-center justify-between rounded-md border border-border/50 px-2.5 py-1 text-sm"
          >
            <span className="text-muted-foreground">
              {pending ? '–' : ok ? '✔' : '✖'} {label}
            </span>
            <Badge
              variant={pending ? 'secondary' : ok ? 'success' : 'danger'}
              className="capitalize"
            >
              {pending ? 'skipped' : ok ? 'pass' : 'fail'}
            </Badge>
          </div>
        )
      })}
    </div>
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

function MetricCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border/50 px-2.5 py-1.5">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mono text-sm font-medium">{value}</p>
    </div>
  )
}

function Kpi({
  label,
  value,
  tone = 'default',
}: {
  label: string
  value: string
  tone?: 'default' | 'success' | 'danger' | 'warning'
}) {
  const className =
    tone === 'success'
      ? 'border-success/30 bg-success/10 text-success'
      : tone === 'danger'
        ? 'border-danger/30 bg-danger/10 text-danger'
        : tone === 'warning'
          ? 'border-warning/30 bg-warning/10 text-warning'
          : 'border-border/60 bg-secondary/30 text-foreground'
  return (
    <div className={`rounded-lg border px-2.5 py-2 ${className}`}>
      <p className="text-[10px] uppercase tracking-wide opacity-80">{label}</p>
      <p className="mono mt-1 text-sm font-semibold">{value}</p>
    </div>
  )
}

function Subsection({
  title,
  description,
  children,
}: {
  title: string
  description?: string
  children: ReactNode
}) {
  return (
    <div className="space-y-3 border-t border-border/60 px-4 py-4">
      <div>
        <h4 className="text-sm font-semibold tracking-tight">{title}</h4>
        {description ? (
          <p className="text-xs text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {children}
    </div>
  )
}

function matchesText(haystack: Array<string | null | undefined>, needle: string): boolean {
  if (!needle) return true
  const q = needle.toLowerCase()
  return haystack.some((part) => String(part || '').toLowerCase().includes(q))
}

function summarizeSwitch(section: SwitchStormSectionData) {
  return {
    eligible: section.eligibility.filter((r) => r.eligible).length,
    critical: section.risk.filter((r) => String(r.severity).toUpperCase() === 'CRITICAL').length,
    confirmed: section.confirmation.filter((r) => String(r.state).toUpperCase() === 'CONFIRMED')
      .length,
    safe: section.safety.filter((r) => String(r.status).toUpperCase() === 'SAFE').length,
    openIncidents: section.incidents.filter((r) => {
      const s = String(r.status).toUpperCase()
      return s !== 'RESOLVED' && s !== 'CLOSED'
    }).length,
  }
}

export function StormProtectionPage() {
  const { isAdmin } = useAuth()
  const [searchParams] = useSearchParams()
  const eligibilityMutations = useEligibilityMutations()
  const riskMutations = useRiskMutations()
  const confirmationMutations = useConfirmationMutations()
  const safetyMutations = useSafetyMutations()
  const orchestratorMutations = useOrchestratorMutations()
  const stormConfig = useStormConfigQuery()
  const settingsQuery = useSettingsQuery()
  const settingsMutation = useSettingsMutation()
  const mitigationMutations = useMitigationMutations()
  const recoveryMutations = useRecoveryMutations()

  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [switchFilter, setSwitchFilter] = useState('all')
  const [severityFilter, setSeverityFilter] = useState('all')
  const [eligibleFilter, setEligibleFilter] = useState('all')
  const [confirmStateFilter, setConfirmStateFilter] = useState('all')
  const [safetyStatusFilter, setSafetyStatusFilter] = useState('all')
  const [incidentStatusFilter, setIncidentStatusFilter] = useState('all')

  const [selectedRisk, setSelectedRisk] = useState<RiskResult | null>(null)
  const [selectedSafety, setSelectedSafety] = useState<SafetyResult | null>(null)
  const [selectedIncident, setSelectedIncident] = useState<StormIncident | null>(null)
  const [selectedMitigation, setSelectedMitigation] = useState<MitigationLog | null>(null)
  const [selectedRecovery, setSelectedRecovery] = useState<RecoveryLog | null>(null)
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({})

  const [collapsedDeviceIds, setCollapsedDeviceIds] = useState<Set<string>>(() => {
    try {
      const raw = sessionStorage.getItem(COLLAPSE_KEY)
      if (!raw) return new Set<string>()
      const parsed = JSON.parse(raw)
      return Array.isArray(parsed)
        ? new Set(parsed.filter((v) => typeof v === 'string'))
        : new Set<string>()
    } catch {
      return new Set<string>()
    }
  })

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 300)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => {
    sessionStorage.setItem(COLLAPSE_KEY, JSON.stringify([...collapsedDeviceIds]))
  }, [collapsedDeviceIds])

  const devicesQuery = useDevicesQuery({ page: 1, limit: FETCH_LIMIT })
  const devices = devicesQuery.data?.data ?? []
  const switchDevices = useMemo(
    () => devices.filter((device) => isManagedSwitch(device.deviceType)),
    [devices],
  )

  const eligibilityQuery = useEligibilityQuery({ page: 1, limit: FETCH_LIMIT })
  const riskListQuery = useRiskQuery({ page: 1, limit: FETCH_LIMIT })
  const confirmationQuery = useConfirmationQuery({ page: 1, limit: FETCH_LIMIT })
  const safetyListQuery = useSafetyQuery({ page: 1, limit: FETCH_LIMIT })
  const incidentsQuery = useStormIncidentsQuery({ page: 1, limit: FETCH_LIMIT })
  const mitigationHistoryQuery = useMitigationHistoryQuery({ page: 1, limit: FETCH_LIMIT })
  const recoveryHistoryQuery = useRecoveryHistoryQuery({ page: 1, limit: FETCH_LIMIT })

  const selectedHistoryQuery = useInterfaceRiskQuery(
    selectedRisk?.deviceId || '',
    selectedRisk?.interface || '',
    Boolean(selectedRisk),
  )

  const requiredConfirmations =
    stormConfig.data?.confirmation?.requiredConfirmations ?? 2

  const eligibilityRows = eligibilityQuery.data?.data ?? []
  const riskRows = riskListQuery.data?.data ?? []
  const confirmRows = confirmationQuery.data?.data ?? []
  const safetyRows = safetyListQuery.data?.data ?? []
  const incidentRows = incidentsQuery.data?.data ?? []
  const mitigationRows = mitigationHistoryQuery.data?.data ?? []
  const recoveryRows = recoveryHistoryQuery.data?.data ?? []

  // Deep-link from Alerts: /storm?incident=<incidentId>
  useEffect(() => {
    const incidentParam = (searchParams.get('incident') || '').trim()
    if (!incidentParam) return
    if (selectedIncident?.incidentId === incidentParam) return

    const fromList = incidentRows.find((row) => row.incidentId === incidentParam)
    if (fromList) {
      setSelectedIncident(fromList)
      setExpandedSections({})
      setCollapsedDeviceIds((prev) => {
        if (!fromList.deviceId || !prev.has(fromList.deviceId)) return prev
        const next = new Set(prev)
        next.delete(fromList.deviceId)
        return next
      })
      if (fromList.deviceId) setSwitchFilter(fromList.deviceId)
      return
    }

    let cancelled = false
    void getStormIncident(incidentParam)
      .then((res) => {
        if (cancelled || !res?.data) return
        setSelectedIncident(res.data)
        setExpandedSections({})
        if (res.data.deviceId) {
          setSwitchFilter(res.data.deviceId)
          setCollapsedDeviceIds((prev) => {
            if (!prev.has(res.data.deviceId)) return prev
            const next = new Set(prev)
            next.delete(res.data.deviceId)
            return next
          })
        }
      })
      .catch(() => {
        /* ignore missing incident */
      })
    return () => {
      cancelled = true
    }
  }, [searchParams, incidentRows, selectedIncident?.incidentId])

  const filteredEligibility = useMemo(() => {
    return eligibilityRows.filter((row) => {
      if (eligibleFilter === 'eligible' && !row.eligible) return false
      if (eligibleFilter === 'ineligible' && row.eligible) return false
      return matchesText(
        [row.interface, row.hostname, row.ipAddress, row.reason, row.failedRule, row.deviceId],
        debouncedQuery,
      )
    })
  }, [eligibilityRows, eligibleFilter, debouncedQuery])

  const filteredRisk = useMemo(() => {
    return riskRows.filter((row) => {
      if (
        severityFilter !== 'all' &&
        String(row.severity).toUpperCase() !== severityFilter.toUpperCase()
      ) {
        return false
      }
      return matchesText(
        [
          row.interface,
          row.hostname,
          row.ipAddress,
          row.severity,
          row.sourceClassification,
          row.deviceId,
        ],
        debouncedQuery,
      )
    })
  }, [riskRows, severityFilter, debouncedQuery])

  const filteredConfirm = useMemo(() => {
    return confirmRows.filter((row) => {
      if (
        confirmStateFilter !== 'all' &&
        String(row.state).toUpperCase() !== confirmStateFilter.toUpperCase()
      ) {
        return false
      }
      return matchesText(
        [row.interface, row.hostname, row.ipAddress, row.reason, row.state, row.deviceId],
        debouncedQuery,
      )
    })
  }, [confirmRows, confirmStateFilter, debouncedQuery])

  const filteredSafety = useMemo(() => {
    return safetyRows.filter((row) => {
      if (
        safetyStatusFilter !== 'all' &&
        String(row.status).toUpperCase() !== safetyStatusFilter.toUpperCase()
      ) {
        return false
      }
      return matchesText(
        [row.interface, row.hostname, row.ipAddress, row.reason, row.failedRule, row.deviceId],
        debouncedQuery,
      )
    })
  }, [safetyRows, safetyStatusFilter, debouncedQuery])

  const filteredIncidents = useMemo(() => {
    return incidentRows.filter((row) => {
      if (
        incidentStatusFilter !== 'all' &&
        String(row.status).toUpperCase() !== incidentStatusFilter.toUpperCase()
      ) {
        return false
      }
      return matchesText(
        [
          row.incidentId,
          row.interface,
          row.hostname,
          row.ipAddress,
          row.status,
          row.severity,
          row.deviceId,
        ],
        debouncedQuery,
      )
    })
  }, [incidentRows, incidentStatusFilter, debouncedQuery])

  const filteredMitigation = useMemo(() => {
    return mitigationRows.filter((row) =>
      matchesText(
        [row.incidentId, row.interface, row.deviceId, row.strategy, row.status, row.operator],
        debouncedQuery,
      ),
    )
  }, [mitigationRows, debouncedQuery])

  const filteredRecovery = useMemo(() => {
    return recoveryRows.filter((row) =>
      matchesText(
        [row.incidentId, row.interface, row.deviceId, row.recoveryStatus],
        debouncedQuery,
      ),
    )
  }, [recoveryRows, debouncedQuery])

  const rowFiltersActive =
    Boolean(debouncedQuery.trim()) ||
    severityFilter !== 'all' ||
    eligibleFilter !== 'all' ||
    confirmStateFilter !== 'all' ||
    safetyStatusFilter !== 'all' ||
    incidentStatusFilter !== 'all'

  const hasActiveFilters = rowFiltersActive || switchFilter !== 'all'

  const allGroupedSwitches = useMemo<SwitchStormSectionData[]>(() => {
    const groups = new Map<string, SwitchStormSectionData>()

    for (const device of switchDevices) {
      groups.set(device._id, {
        deviceId: device._id,
        hostname: device.hostname || 'Unknown switch',
        ipAddress: device.ipAddress || '—',
        vendor: device.credentials?.sshVendor || 'Unknown',
        status: device.status || 'Unknown',
        monitor: device.monitor ?? true,
        eligibility: [],
        risk: [],
        confirmation: [],
        safety: [],
        incidents: [],
        mitigation: [],
        recovery: [],
      })
    }

    const ensure = (
      deviceId: string,
      hint?: { hostname?: string | null; ipAddress?: string | null },
    ) => {
      if (!deviceId || groups.has(deviceId)) return groups.get(deviceId)
      const device = switchDevices.find((d) => d._id === deviceId)
      if (!device) return undefined
      const section: SwitchStormSectionData = {
        deviceId,
        hostname: hint?.hostname || device.hostname || 'Unknown switch',
        ipAddress: hint?.ipAddress || device.ipAddress || '—',
        vendor: device.credentials?.sshVendor || 'Unknown',
        status: device.status || 'Unknown',
        monitor: device.monitor ?? true,
        eligibility: [],
        risk: [],
        confirmation: [],
        safety: [],
        incidents: [],
        mitigation: [],
        recovery: [],
      }
      groups.set(deviceId, section)
      return section
    }

    for (const row of filteredEligibility) {
      ensure(row.deviceId, row)?.eligibility.push(row)
    }
    for (const row of filteredRisk) {
      ensure(row.deviceId, row)?.risk.push(row)
    }
    for (const row of filteredConfirm) {
      ensure(row.deviceId, row)?.confirmation.push(row)
    }
    for (const row of filteredSafety) {
      ensure(row.deviceId, row)?.safety.push(row)
    }
    for (const row of filteredIncidents) {
      ensure(row.deviceId, row)?.incidents.push(row)
    }
    for (const row of filteredMitigation) {
      if (row.deviceId) ensure(row.deviceId)?.mitigation.push(row)
    }
    for (const row of filteredRecovery) {
      if (row.deviceId) ensure(row.deviceId)?.recovery.push(row)
    }

    return [...groups.values()].sort((a, b) => a.hostname.localeCompare(b.hostname))
  }, [
    switchDevices,
    filteredEligibility,
    filteredRisk,
    filteredConfirm,
    filteredSafety,
    filteredIncidents,
    filteredMitigation,
    filteredRecovery,
  ])

  const groupedSwitches = useMemo(() => {
    return allGroupedSwitches.filter((section) => {
      if (switchFilter !== 'all' && section.deviceId !== switchFilter) return false
      if (!rowFiltersActive) return true
      return (
        section.eligibility.length > 0 ||
        section.risk.length > 0 ||
        section.confirmation.length > 0 ||
        section.safety.length > 0 ||
        section.incidents.length > 0 ||
        section.mitigation.length > 0 ||
        section.recovery.length > 0
      )
    })
  }, [allGroupedSwitches, switchFilter, rowFiltersActive])

  const switchChips = useMemo(() => {
    return allGroupedSwitches.map((device) => {
      const summary = summarizeSwitch(device)
      return {
        deviceId: device.deviceId,
        hostname: device.hostname,
        ipAddress: device.ipAddress,
        critical: summary.critical,
        confirmed: summary.confirmed,
        openIncidents: summary.openIncidents,
      }
    })
  }, [allGroupedSwitches])

  const switchPagination = useClientPagination(groupedSwitches, DEFAULT_SWITCHES_PER_PAGE)
  const pagedSwitches = switchPagination.pageItems

  useEffect(() => {
    switchPagination.reset()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset when filter set changes
  }, [
    debouncedQuery,
    switchFilter,
    severityFilter,
    eligibleFilter,
    confirmStateFilter,
    safetyStatusFilter,
    incidentStatusFilter,
  ])

  const fleetKpis = useMemo(() => {
    return {
      switches: allGroupedSwitches.length,
      eligible: filteredEligibility.filter((r) => r.eligible).length,
      critical: filteredRisk.filter((r) => String(r.severity).toUpperCase() === 'CRITICAL').length,
      confirmed: filteredConfirm.filter((r) => String(r.state).toUpperCase() === 'CONFIRMED')
        .length,
      safe: filteredSafety.filter((r) => String(r.status).toUpperCase() === 'SAFE').length,
      incidents: filteredIncidents.length,
    }
  }, [
    allGroupedSwitches.length,
    filteredEligibility,
    filteredRisk,
    filteredConfirm,
    filteredSafety,
    filteredIncidents,
  ])

  const trendData = useMemo(() => {
    const history = selectedHistoryQuery.data?.history ?? []
    return [...history].reverse().map((point) => ({
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
    void devicesQuery.refetch()
    void eligibilityQuery.refetch()
    void riskListQuery.refetch()
    void confirmationQuery.refetch()
    void safetyListQuery.refetch()
    void incidentsQuery.refetch()
    void mitigationHistoryQuery.refetch()
    void recoveryHistoryQuery.refetch()
    if (selectedRisk) void selectedHistoryQuery.refetch()
  }

  const toggleDevice = (deviceId: string) => {
    setCollapsedDeviceIds((prev) => {
      const next = new Set(prev)
      if (next.has(deviceId)) next.delete(deviceId)
      else next.add(deviceId)
      return next
    })
  }

  const toggleSection = (key: string) => {
    setExpandedSections((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  const isLoading =
    devicesQuery.isLoading ||
    eligibilityQuery.isLoading ||
    riskListQuery.isLoading ||
    confirmationQuery.isLoading ||
    safetyListQuery.isLoading ||
    incidentsQuery.isLoading

  const isError =
    devicesQuery.isError ||
    eligibilityQuery.isError ||
    riskListQuery.isError ||
    confirmationQuery.isError ||
    safetyListQuery.isError ||
    incidentsQuery.isError

  const noSwitchesConfigured = !devicesQuery.isLoading && switchDevices.length === 0

  return (
    <div className="space-y-8">
      <PageHeader
        title="Storm Protection"
        description="Per-switch eligibility → risk → confirmation → safety → diagnostics → mitigation → recovery."
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
                  {riskMutations.calculateAll.isPending ? 'Scoring…' : 'Calculate risk'}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={
                    isBusy || stormConfig.data?.confirmation?.confirmationEnabled === false
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
                  variant="secondary"
                  disabled={isBusy || stormConfig.data?.safety?.safetyEnabled === false}
                  onClick={() => safetyMutations.evaluateAll.mutate()}
                >
                  <ShieldCheck className="mr-2 h-4 w-4" />
                  {safetyMutations.evaluateAll.isPending ? 'Checking…' : 'Evaluate safety'}
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

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Switches</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{fleetKpis.switches}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Eligible</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-success">{fleetKpis.eligible}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Critical risk</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-danger">{fleetKpis.critical}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Confirmed</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-success">{fleetKpis.confirmed}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Safe</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-success">{fleetKpis.safe}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Incidents</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{fleetKpis.incidents}</p>
          </CardContent>
        </Card>
      </div>

      <Card className="border-primary/20 bg-secondary/10">
        <CardContent className="flex flex-wrap items-center justify-between gap-4 p-4">
          <div className="space-y-1">
            <p className="text-sm font-semibold">Automation</p>
            <p className="text-xs text-muted-foreground">
              Mitigation:{' '}
              <span className="font-semibold uppercase text-primary">
                {settingsQuery.data?.mitigationMode || 'manual'}
              </span>
              {' · '}
              Auto recovery:{' '}
              <span className="font-semibold uppercase text-primary">
                {settingsQuery.data?.autoRecovery ? 'enabled' : 'disabled'}
              </span>
            </p>
          </div>
          {isAdmin ? (
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant={
                  settingsQuery.data?.mitigationMode === 'automatic' ? 'default' : 'outline'
                }
                disabled={settingsMutation.isPending}
                onClick={() => settingsMutation.mutate({ mitigationMode: 'automatic' })}
              >
                Automatic mitigation
              </Button>
              <Button
                type="button"
                size="sm"
                variant={settingsQuery.data?.mitigationMode === 'manual' ? 'default' : 'outline'}
                disabled={settingsMutation.isPending}
                onClick={() => settingsMutation.mutate({ mitigationMode: 'manual' })}
              >
                Manual mitigation
              </Button>
              <Button
                type="button"
                size="sm"
                variant={settingsQuery.data?.autoRecovery ? 'default' : 'outline'}
                disabled={settingsMutation.isPending}
                onClick={() => settingsMutation.mutate({ autoRecovery: true })}
              >
                Enable recovery
              </Button>
              <Button
                type="button"
                size="sm"
                variant={!settingsQuery.data?.autoRecovery ? 'default' : 'outline'}
                disabled={settingsMutation.isPending}
                onClick={() => settingsMutation.mutate({ autoRecovery: false })}
              >
                Disable recovery
              </Button>
            </div>
          ) : (
            <Badge variant="outline" className="uppercase">
              Admin configurable only
            </Badge>
          )}
        </CardContent>
      </Card>

      <div className="flex flex-wrap gap-3">
        <Input
          type="search"
          className="max-w-sm"
          placeholder="Search interface, host, incident…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Select value={severityFilter} onValueChange={setSeverityFilter}>
          <SelectTrigger className="w-[160px]">
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
        <Select value={eligibleFilter} onValueChange={setEligibleFilter}>
          <SelectTrigger className="w-[160px]">
            <SelectValue placeholder="Eligibility" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All eligibility</SelectItem>
            <SelectItem value="eligible">Eligible</SelectItem>
            <SelectItem value="ineligible">Not eligible</SelectItem>
          </SelectContent>
        </Select>
        <Select value={confirmStateFilter} onValueChange={setConfirmStateFilter}>
          <SelectTrigger className="w-[180px]">
            <SelectValue placeholder="Confirmation" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All confirmation</SelectItem>
            <SelectItem value="NOT_CONFIRMED">Not confirmed</SelectItem>
            <SelectItem value="PENDING">Pending</SelectItem>
            <SelectItem value="CONFIRMED">Confirmed</SelectItem>
          </SelectContent>
        </Select>
        <Select value={safetyStatusFilter} onValueChange={setSafetyStatusFilter}>
          <SelectTrigger className="w-[150px]">
            <SelectValue placeholder="Safety" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All safety</SelectItem>
            <SelectItem value="SAFE">Safe</SelectItem>
            <SelectItem value="WAITING">Waiting</SelectItem>
            <SelectItem value="UNSAFE">Unsafe</SelectItem>
          </SelectContent>
        </Select>
        <Select value={incidentStatusFilter} onValueChange={setIncidentStatusFilter}>
          <SelectTrigger className="w-[200px]">
            <SelectValue placeholder="Incidents" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All incidents</SelectItem>
            <SelectItem value="OPEN">Open</SelectItem>
            <SelectItem value="READY_FOR_MITIGATION">Ready for mitigation</SelectItem>
            <SelectItem value="PREPARED">Prepared</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {noSwitchesConfigured ? (
        <EmptyState
          icon={Network}
          title="No managed switches"
          description="Add a managed switch from Device Inventory to view per-switch storm protection details."
        />
      ) : isLoading ? (
        <TableSkeleton rows={8} />
      ) : isError ? (
        <ErrorState
          title="Unable to load storm protection data"
          message="One or more storm datasets failed to load."
          onRetry={refreshAll}
        />
      ) : groupedSwitches.length === 0 ? (
        <EmptyState
          icon={Shield}
          title={hasActiveFilters ? 'No matching switch storm data' : 'No storm data yet'}
          description={
            hasActiveFilters
              ? 'Adjust filters to see more switches.'
              : 'Run eligibility and risk evaluation after interface discovery and stats collection.'
          }
        />
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant={switchFilter === 'all' ? 'default' : 'secondary'}
              onClick={() => setSwitchFilter('all')}
            >
              All switches ({switchChips.length})
            </Button>
            {switchChips.map((device) => {
              const selected = switchFilter === device.deviceId
              return (
                <button
                  key={device.deviceId}
                  type="button"
                  className={cn(
                    'min-w-[180px] rounded-lg border px-3 py-2 text-left transition-colors',
                    selected
                      ? 'border-primary bg-primary/10'
                      : 'border-border/70 bg-card hover:bg-muted/40',
                  )}
                  onClick={() =>
                    setSwitchFilter((current) =>
                      current === device.deviceId ? 'all' : device.deviceId,
                    )
                  }
                >
                  <p className="truncate text-sm font-semibold">{device.hostname}</p>
                  <p className="mono truncate text-xs text-muted-foreground">
                    {device.ipAddress}
                  </p>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    Crit {device.critical} · Conf {device.confirmed} · Inc{' '}
                    {device.openIncidents}
                  </p>
                </button>
              )
            })}
          </div>

          {switchPagination.totalPages > 1 ||
          switchPagination.total > switchPagination.limit ? (
            <PaginationControls
              page={switchPagination.page}
              totalPages={Math.max(switchPagination.totalPages, 1)}
              total={switchPagination.total}
              limit={switchPagination.limit}
              onPageChange={switchPagination.setPage}
              onLimitChange={switchPagination.setLimit}
              limitOptions={[5, 10, 25, 50]}
              unitLabel="Switches"
            />
          ) : null}

          <div className="space-y-4">
            {pagedSwitches.map((device) => (
              <SwitchStormSection
                key={device.deviceId}
                device={device}
                collapsed={collapsedDeviceIds.has(device.deviceId)}
                isAdmin={isAdmin}
                requiredConfirmations={requiredConfirmations}
                selectedRisk={
                  selectedRisk?.deviceId === device.deviceId ? selectedRisk : null
                }
                selectedSafety={
                  selectedSafety?.deviceId === device.deviceId ? selectedSafety : null
                }
                selectedIncident={
                  selectedIncident?.deviceId === device.deviceId ? selectedIncident : null
                }
                selectedMitigation={
                  selectedMitigation?.deviceId === device.deviceId
                    ? selectedMitigation
                    : null
                }
                selectedRecovery={
                  selectedRecovery?.deviceId === device.deviceId ? selectedRecovery : null
                }
                expandedSections={expandedSections}
                trendData={
                  selectedRisk?.deviceId === device.deviceId ? trendData : []
                }
                trendLoading={
                  selectedRisk?.deviceId === device.deviceId &&
                  selectedHistoryQuery.isLoading
                }
                mitigationPending={mitigationMutations.execute.isPending}
                rollbackPending={mitigationMutations.rollback.isPending}
                recoveryPending={recoveryMutations.execute.isPending}
                retryPending={recoveryMutations.retry.isPending}
                onToggle={() => toggleDevice(device.deviceId)}
                onSelectRisk={setSelectedRisk}
                onSelectSafety={setSelectedSafety}
                onSelectIncident={(row) => {
                  setSelectedIncident(row)
                  setExpandedSections({})
                }}
                onViewIncident={(row) => {
                  setSelectedIncident(row)
                  setExpandedSections({
                    interface: true,
                    switchport: true,
                    mac: true,
                    neighbor: true,
                    timeline: true,
                  })
                }}
                onSelectMitigation={setSelectedMitigation}
                onSelectRecovery={setSelectedRecovery}
                onToggleJsonSection={toggleSection}
                onExportIncident={exportIncident}
                onExecuteMitigation={(incidentId, strategy) =>
                  mitigationMutations.execute.mutate({ incidentId, strategy })
                }
                onRollback={(incidentId) =>
                  mitigationMutations.rollback.mutate({ incidentId })
                }
                onRetryRecovery={(incidentId) =>
                  recoveryMutations.retry.mutate({ incidentId })
                }
                onForceRecovery={(incidentId) =>
                  recoveryMutations.execute.mutate({ incidentId, force: true })
                }
              />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function SwitchStormSection({
  device,
  collapsed,
  isAdmin,
  requiredConfirmations,
  selectedRisk,
  selectedSafety,
  selectedIncident,
  selectedMitigation,
  selectedRecovery,
  expandedSections,
  trendData,
  trendLoading,
  mitigationPending,
  rollbackPending,
  recoveryPending,
  retryPending,
  onToggle,
  onSelectRisk,
  onSelectSafety,
  onSelectIncident,
  onViewIncident,
  onSelectMitigation,
  onSelectRecovery,
  onToggleJsonSection,
  onExportIncident,
  onExecuteMitigation,
  onRollback,
  onRetryRecovery,
  onForceRecovery,
}: {
  device: SwitchStormSectionData
  collapsed: boolean
  isAdmin: boolean
  requiredConfirmations: number
  selectedRisk: RiskResult | null
  selectedSafety: SafetyResult | null
  selectedIncident: StormIncident | null
  selectedMitigation: MitigationLog | null
  selectedRecovery: RecoveryLog | null
  expandedSections: Record<string, boolean>
  trendData: Array<{ time: string; label: string; riskScore: number; severity: string }>
  trendLoading: boolean
  mitigationPending: boolean
  rollbackPending: boolean
  recoveryPending: boolean
  retryPending: boolean
  onToggle: () => void
  onSelectRisk: (row: RiskResult) => void
  onSelectSafety: (row: SafetyResult) => void
  onSelectIncident: (row: StormIncident) => void
  onViewIncident: (row: StormIncident) => void
  onSelectMitigation: (row: MitigationLog) => void
  onSelectRecovery: (row: RecoveryLog) => void
  onToggleJsonSection: (key: string) => void
  onExportIncident: (incident: StormIncident) => void
  onExecuteMitigation: (incidentId: string, strategy: string) => void
  onRollback: (incidentId: string) => void
  onRetryRecovery: (incidentId: string) => void
  onForceRecovery: (incidentId: string) => void
}) {
  const summary = summarizeSwitch(device)
  const riskPagination = useClientPagination(device.risk, DEFAULT_SECTION_ROWS_PER_PAGE)
  const eligibilityPagination = useClientPagination(device.eligibility, DEFAULT_SECTION_ROWS_PER_PAGE)
  const confirmationPagination = useClientPagination(device.confirmation, DEFAULT_SECTION_ROWS_PER_PAGE)
  const safetyPagination = useClientPagination(device.safety, DEFAULT_SECTION_ROWS_PER_PAGE)
  const incidentsPagination = useClientPagination(device.incidents, DEFAULT_SECTION_ROWS_PER_PAGE)
  const mitigationPagination = useClientPagination(device.mitigation, DEFAULT_SECTION_ROWS_PER_PAGE)
  const recoveryPagination = useClientPagination(device.recovery, DEFAULT_SECTION_ROWS_PER_PAGE)

  return (
    <Card className="border-border/70 shadow-sm">
      <div className="border-b border-border/60 bg-card">
        <div className="space-y-3 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-md bg-primary/10 p-1 text-primary">
                  <Network className="h-4 w-4" />
                </span>
                <h3 className="truncate text-base font-semibold">{device.hostname}</h3>
                <Badge variant={device.monitor ? 'success' : 'muted'}>
                  {device.monitor ? 'Monitored' : 'Not monitored'}
                </Badge>
                <StatusBadge status={device.status} />
              </div>
              <p className="mono mt-1 text-xs text-muted-foreground">
                {device.ipAddress} · {device.vendor} · {device.risk.length} scored ports
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button type="button" size="sm" variant="secondary" asChild>
                <Link to={`/devices/${device.deviceId}`}>
                  <ExternalLink className="h-3.5 w-3.5" />
                  Device
                </Link>
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={onToggle}>
                {collapsed ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronUp className="h-4 w-4" />
                )}
                {collapsed ? 'Expand' : 'Collapse'}
              </Button>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">
            <Kpi label="Eligible" value={String(summary.eligible)} tone="success" />
            <Kpi
              label="Critical risk"
              value={String(summary.critical)}
              tone={summary.critical > 0 ? 'danger' : 'default'}
            />
            <Kpi
              label="Confirmed"
              value={String(summary.confirmed)}
              tone={summary.confirmed > 0 ? 'warning' : 'default'}
            />
            <Kpi label="Safe" value={String(summary.safe)} tone="success" />
            <Kpi
              label="Open incidents"
              value={String(summary.openIncidents)}
              tone={summary.openIncidents > 0 ? 'danger' : 'default'}
            />
          </div>
        </div>
      </div>

      {!collapsed ? (
        <CardContent className="space-y-0 p-0">
          {/* Risk */}
          <Subsection
            title="Risk Score"
            description="Rate-based storm probability for eligible access ports on this switch."
          >
            {device.risk.length === 0 ? (
              <p className="text-sm text-muted-foreground">No risk scores for this switch yet.</p>
            ) : (
              <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(280px,1fr)]">
                <div className="space-y-3">
                <div className="overflow-x-auto rounded-md border border-border/60">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Interface</TableHead>
                        <TableHead>Risk</TableHead>
                        <TableHead>Severity</TableHead>
                        <TableHead>Source</TableHead>
                        <TableHead>Broadcast</TableHead>
                        <TableHead>Util</TableHead>
                        <TableHead>Updated</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {riskPagination.pageItems.map((row) => {
                        const active =
                          selectedRisk?.deviceId === row.deviceId &&
                          selectedRisk?.interface === row.interface
                        const tone = severityTone(row.severity)
                        return (
                          <TableRow
                            key={`${row.deviceId}-${row.interface}-${row._id ?? ''}`}
                            className={cn('cursor-pointer', active && 'bg-primary/10')}
                            onClick={() => onSelectRisk(row)}
                          >
                            <TableCell className="font-medium">
                              <Link
                                to={`/interfaces/${row.deviceId}/${encodeURIComponent(row.interface)}`}
                                className="text-primary hover:underline"
                                onClick={(e) => e.stopPropagation()}
                              >
                                {row.interface}
                              </Link>
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
                                <span
                                  className={cn(
                                    'mono w-10 text-right text-xs font-semibold',
                                    tone.text,
                                  )}
                                >
                                  {row.riskScore.toFixed(0)}
                                </span>
                              </div>
                            </TableCell>
                            <TableCell>
                              <SeverityBadge severity={String(row.severity)} />
                            </TableCell>
                            <TableCell>
                              <SourceBadge
                                classification={row.sourceClassification}
                                confidence={row.sourceConfidence}
                              />
                            </TableCell>
                            <TableCell className="mono text-xs">
                              {formatRate(row.broadcastRate)}
                            </TableCell>
                            <TableCell className="mono text-xs">
                              {row.utilization == null
                                ? '—'
                                : `${Number(row.utilization).toFixed(1)}%`}
                            </TableCell>
                            <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                              {formatRelative(row.timestamp) || '—'}
                            </TableCell>
                          </TableRow>
                        )
                      })}
                    </TableBody>
                  </Table>
                </div>
                {riskPagination.totalPages > 1 ? (
                  <PaginationControls
                    page={riskPagination.page}
                    totalPages={Math.max(riskPagination.totalPages, 1)}
                    total={riskPagination.total}
                    limit={riskPagination.limit}
                    onPageChange={riskPagination.setPage}
                    onLimitChange={riskPagination.setLimit}
                    limitOptions={[5, 10, 25, 50]}
                    unitLabel="Risk rows"
                  />
                ) : null}
                </div>
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">
                      {selectedRisk
                        ? `${selectedRisk.interface} detail`
                        : 'Select an interface'}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {!selectedRisk ? (
                      <p className="text-sm text-muted-foreground">
                        Click a row to inspect contributors and risk trend.
                      </p>
                    ) : (
                      <>
                        <div className="flex flex-wrap items-center gap-2">
                          <span
                            className={cn(
                              'text-2xl font-bold',
                              severityTone(selectedRisk.severity).text,
                            )}
                          >
                            {selectedRisk.riskScore.toFixed(1)}
                          </span>
                          <SeverityBadge severity={String(selectedRisk.severity)} />
                          <SourceBadge
                            classification={selectedRisk.sourceClassification}
                            confidence={selectedRisk.sourceConfidence}
                          />
                        </div>
                        <div className="grid grid-cols-2 gap-2 text-sm">
                          <MetricCell
                            label="Broadcast"
                            value={formatRate(selectedRisk.broadcastRate)}
                          />
                          <MetricCell
                            label="Multicast"
                            value={formatRate(selectedRisk.multicastRate)}
                          />
                          <MetricCell
                            label="Utilization"
                            value={
                              selectedRisk.utilization == null
                                ? '—'
                                : `${Number(selectedRisk.utilization).toFixed(1)}%`
                            }
                          />
                          <MetricCell
                            label="Errors"
                            value={formatRate(selectedRisk.errorRate)}
                          />
                        </div>
                        <div className="h-40">
                          {trendLoading ? (
                            <p className="text-sm text-muted-foreground">Loading history…</p>
                          ) : trendData.length < 2 ? (
                            <p className="text-sm text-muted-foreground">
                              Not enough history points yet.
                            </p>
                          ) : (
                            <ResponsiveContainer width="100%" height="100%">
                              <AreaChart data={trendData}>
                                <CartesianGrid
                                  strokeDasharray="3 3"
                                  className="stroke-border"
                                />
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
                        </div>
                      </>
                    )}
                  </CardContent>
                </Card>
              </div>
            )}
          </Subsection>

          {/* Eligibility */}
          <Subsection
            title="Port Eligibility"
            description="Which access ports on this switch may enter risk scoring."
          >
            {device.eligibility.length === 0 ? (
              <p className="text-sm text-muted-foreground">No eligibility results yet.</p>
            ) : (
              <div className="space-y-3">
              <div className="overflow-x-auto rounded-md border border-border/60">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Interface</TableHead>
                      <TableHead>Eligibility</TableHead>
                      <TableHead>Reason</TableHead>
                      <TableHead>Failed rule</TableHead>
                      <TableHead>Classification</TableHead>
                      <TableHead>Evaluated</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {eligibilityPagination.pageItems.map((row) => (
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
                        <TableCell>
                          <div className="flex max-w-[280px] flex-wrap gap-1">
                            <PortClassificationBadges
                              iface={classificationIface(row)}
                              includeMode
                            />
                          </div>
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                          {formatRelative(row.timestamp) || '—'}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              {eligibilityPagination.totalPages > 1 ? (
                <PaginationControls
                  page={eligibilityPagination.page}
                  totalPages={Math.max(eligibilityPagination.totalPages, 1)}
                  total={eligibilityPagination.total}
                  limit={eligibilityPagination.limit}
                  onPageChange={eligibilityPagination.setPage}
                  onLimitChange={eligibilityPagination.setLimit}
                  limitOptions={[5, 10, 25, 50]}
                  unitLabel="Eligibility rows"
                />
              ) : null}
              </div>
            )}
          </Subsection>

          {/* Confirmation */}
          <Subsection
            title="Confirmation"
            description={`High risk must persist across ${requiredConfirmations} consecutive polls.`}
          >
            {device.confirmation.length === 0 ? (
              <p className="text-sm text-muted-foreground">No confirmation results yet.</p>
            ) : (
              <div className="space-y-3">
              <div className="overflow-x-auto rounded-md border border-border/60">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Interface</TableHead>
                      <TableHead>State</TableHead>
                      <TableHead>Current</TableHead>
                      <TableHead>Progress</TableHead>
                      <TableHead>Reason</TableHead>
                      <TableHead>Updated</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {confirmationPagination.pageItems.map((row) => (
                      <TableRow key={`${row.deviceId}-${row.interface}-${row._id ?? ''}`}>
                        <TableCell className="font-medium">
                          <Link
                            to={`/interfaces/${row.deviceId}/${encodeURIComponent(row.interface)}`}
                            className="text-primary hover:underline"
                          >
                            {row.interface}
                          </Link>
                        </TableCell>
                        <TableCell>
                          <ConfirmationStateBadge state={String(row.state)} />
                        </TableCell>
                        <TableCell className="mono text-sm">
                          {Number(row.currentRisk).toFixed(1)}
                        </TableCell>
                        <TableCell>
                          <ConfirmationProgressBar
                            consecutive={row.consecutiveHighSamples}
                            required={row.requiredSamples || requiredConfirmations}
                            state={String(row.state)}
                          />
                        </TableCell>
                        <TableCell className="max-w-[220px] truncate text-sm text-muted-foreground">
                          {row.reason}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                          {formatRelative(row.timestamp) || '—'}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              {confirmationPagination.totalPages > 1 ? (
                <PaginationControls
                  page={confirmationPagination.page}
                  totalPages={Math.max(confirmationPagination.totalPages, 1)}
                  total={confirmationPagination.total}
                  limit={confirmationPagination.limit}
                  onPageChange={confirmationPagination.setPage}
                  onLimitChange={confirmationPagination.setLimit}
                  limitOptions={[5, 10, 25, 50]}
                  unitLabel="Confirmation rows"
                />
              ) : null}
              </div>
            )}
          </Subsection>

          {/* Safety */}
          <Subsection
            title="Mitigation Safety"
            description="Final pre-mitigation gate for confirmed storms on this switch."
          >
            {device.safety.length === 0 ? (
              <p className="text-sm text-muted-foreground">No safety results yet.</p>
            ) : (
              <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(260px,1fr)]">
                <div className="space-y-3">
                <div className="overflow-x-auto rounded-md border border-border/60">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Interface</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Reason</TableHead>
                        <TableHead>Cooldown</TableHead>
                        <TableHead>Updated</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {safetyPagination.pageItems.map((row) => {
                        const active =
                          selectedSafety?.deviceId === row.deviceId &&
                          selectedSafety?.interface === row.interface
                        return (
                          <TableRow
                            key={`${row.deviceId}-${row.interface}-${row._id ?? ''}`}
                            className={cn('cursor-pointer', active && 'bg-primary/10')}
                            onClick={() => onSelectSafety(row)}
                          >
                            <TableCell className="font-medium">
                              <Link
                                to={`/interfaces/${row.deviceId}/${encodeURIComponent(row.interface)}`}
                                className="text-primary hover:underline"
                                onClick={(e) => e.stopPropagation()}
                              >
                                {row.interface}
                              </Link>
                            </TableCell>
                            <TableCell>
                              <SafetyStatusBadge status={String(row.status)} />
                            </TableCell>
                            <TableCell className="max-w-[180px] truncate text-sm">
                              {row.reason}
                            </TableCell>
                            <TableCell className="mono text-xs">
                              {formatCooldown(row.cooldownRemainingSeconds)}
                            </TableCell>
                            <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                              {formatRelative(row.timestamp) || '—'}
                            </TableCell>
                          </TableRow>
                        )
                      })}
                    </TableBody>
                  </Table>
                </div>
                {safetyPagination.totalPages > 1 ? (
                  <PaginationControls
                    page={safetyPagination.page}
                    totalPages={Math.max(safetyPagination.totalPages, 1)}
                    total={safetyPagination.total}
                    limit={safetyPagination.limit}
                    onPageChange={safetyPagination.setPage}
                    onLimitChange={safetyPagination.setLimit}
                    limitOptions={[5, 10, 25, 50]}
                    unitLabel="Safety rows"
                  />
                ) : null}
                </div>
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">
                      {selectedSafety
                        ? `${selectedSafety.interface} checks`
                        : 'Select an interface'}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2">
                    {!selectedSafety ? (
                      <p className="text-sm text-muted-foreground">
                        Click a row to inspect check results.
                      </p>
                    ) : (
                      <>
                        <SafetyStatusBadge status={String(selectedSafety.status)} />
                        <div className="space-y-1.5">
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
          </Subsection>

          {/* Incidents */}
          <Subsection
            title="Storm Incidents"
            description="Pre-mitigation evidence packages for this switch."
          >
            {device.incidents.length === 0 ? (
              <p className="text-sm text-muted-foreground">No storm incidents for this switch.</p>
            ) : (
              <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(280px,1fr)]">
                <div className="space-y-3">
                <div className="overflow-x-auto rounded-md border border-border/60">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Incident</TableHead>
                        <TableHead>Interface</TableHead>
                        <TableHead>Type</TableHead>
                        <TableHead>Severity</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Created</TableHead>
                        <TableHead>Actions</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {incidentsPagination.pageItems.map((row) => {
                        const active = selectedIncident?.incidentId === row.incidentId
                        return (
                          <TableRow
                            key={row.incidentId}
                            className={cn('cursor-pointer', active && 'bg-primary/10')}
                            onClick={() => onSelectIncident(row)}
                          >
                            <TableCell className="mono text-xs font-medium">
                              {row.incidentId}
                            </TableCell>
                            <TableCell className="font-medium">{row.interface}</TableCell>
                            <TableCell>
                              <IncidentTypeBadge
                                incidentType={row.incidentType || row.type}
                              />
                            </TableCell>
                            <TableCell>
                              <SeverityBadge severity={row.severity} />
                            </TableCell>
                            <TableCell>
                              <IncidentStatusBadge status={row.status} />
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
                                    onViewIncident(row)
                                  }}
                                >
                                  View
                                </Button>
                                <Button
                                  type="button"
                                  size="sm"
                                  variant="secondary"
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    onExportIncident(row)
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
                </div>
                {incidentsPagination.totalPages > 1 ? (
                  <PaginationControls
                    page={incidentsPagination.page}
                    totalPages={Math.max(incidentsPagination.totalPages, 1)}
                    total={incidentsPagination.total}
                    limit={incidentsPagination.limit}
                    onPageChange={incidentsPagination.setPage}
                    onLimitChange={incidentsPagination.setLimit}
                    limitOptions={[5, 10, 25, 50]}
                    unitLabel="Incident rows"
                  />
                ) : null}
                </div>
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">
                      {selectedIncident
                        ? selectedIncident.incidentId
                        : 'Select an incident'}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {!selectedIncident ? (
                      <p className="text-sm text-muted-foreground">
                        Click a row to inspect evidence snapshots.
                      </p>
                    ) : (
                      <>
                        <div className="flex flex-wrap items-center gap-2">
                          <SeverityBadge severity={selectedIncident.severity} />
                          <IncidentStatusBadge status={selectedIncident.status} />
                        </div>
                        <p className="text-sm text-muted-foreground">
                          {selectedIncident.interface}
                        </p>
                        <JsonSection
                          title="Interface Snapshot"
                          open={Boolean(expandedSections.interface)}
                          onToggle={() => onToggleJsonSection('interface')}
                          data={selectedIncident.interfaceSnapshot}
                        />
                        <JsonSection
                          title="Switchport Snapshot"
                          open={Boolean(expandedSections.switchport)}
                          onToggle={() => onToggleJsonSection('switchport')}
                          data={selectedIncident.switchportSnapshot}
                        />
                        <JsonSection
                          title="MAC Table"
                          open={Boolean(expandedSections.mac)}
                          onToggle={() => onToggleJsonSection('mac')}
                          data={selectedIncident.macTable}
                        />
                        <JsonSection
                          title="Neighbor"
                          open={Boolean(expandedSections.neighbor)}
                          onToggle={() => onToggleJsonSection('neighbor')}
                          data={selectedIncident.neighbor}
                        />
                        <JsonSection
                          title="Timeline"
                          open={Boolean(expandedSections.timeline)}
                          onToggle={() => onToggleJsonSection('timeline')}
                          data={selectedIncident.timeline}
                        />
                        {isAdmin ? (
                          <div className="space-y-2 border-t border-border/50 pt-3">
                            {[
                              'READY_FOR_MITIGATION',
                              'PREPARED',
                              'OPEN',
                              'MITIGATION_FAILED',
                            ].includes(selectedIncident.status) ? (
                              <Button
                                type="button"
                                className="w-full bg-destructive text-destructive-foreground hover:bg-destructive/90"
                                disabled={mitigationPending}
                                onClick={() =>
                                  onExecuteMitigation(
                                    selectedIncident.incidentId || '',
                                    'SHUTDOWN',
                                  )
                                }
                              >
                                {mitigationPending
                                  ? 'Executing Shutdown…'
                                  : 'Execute Shutdown'}
                              </Button>
                            ) : null}
                            {selectedIncident.status === 'MITIGATED' ? (
                              <div className="flex gap-2">
                                <Button
                                  type="button"
                                  variant="outline"
                                  className="flex-1 border-destructive text-destructive hover:bg-destructive/10"
                                  disabled={rollbackPending}
                                  onClick={() =>
                                    onRollback(selectedIncident.incidentId || '')
                                  }
                                >
                                  {rollbackPending ? 'Rolling back…' : 'Rollback'}
                                </Button>
                                <Button
                                  type="button"
                                  variant="secondary"
                                  className="flex-1"
                                  disabled={mitigationPending}
                                  onClick={() =>
                                    onExecuteMitigation(
                                      selectedIncident.incidentId || '',
                                      'NO_SHUTDOWN',
                                    )
                                  }
                                >
                                  {mitigationPending ? 'Recovering…' : 'Recover Port'}
                                </Button>
                              </div>
                            ) : null}
                            {[
                              'MITIGATED',
                              'RECOVERY_FAILED',
                              'MITIGATION_FAILED',
                            ].includes(selectedIncident.status) ? (
                              <div className="flex gap-2">
                                <Button
                                  type="button"
                                  variant="secondary"
                                  className="flex-1"
                                  disabled={retryPending}
                                  onClick={() =>
                                    onRetryRecovery(selectedIncident.incidentId || '')
                                  }
                                >
                                  {retryPending ? 'Retrying…' : 'Retry Recovery'}
                                </Button>
                                <Button
                                  type="button"
                                  variant="outline"
                                  className="flex-1"
                                  disabled={recoveryPending}
                                  onClick={() =>
                                    onForceRecovery(selectedIncident.incidentId || '')
                                  }
                                >
                                  {recoveryPending ? 'Recovering…' : 'Force Recovery'}
                                </Button>
                              </div>
                            ) : null}
                          </div>
                        ) : null}
                        <Button
                          type="button"
                          variant="outline"
                          className="w-full"
                          onClick={() => onExportIncident(selectedIncident)}
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
          </Subsection>

          {/* Mitigation history */}
          <Subsection
            title="Mitigation History"
            description="Shutdown and rollback execution logs for this switch."
          >
            {device.mitigation.length === 0 ? (
              <p className="text-sm text-muted-foreground">No mitigation logs yet.</p>
            ) : (
              <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(260px,1fr)]">
                <div className="space-y-3">
                <div className="overflow-x-auto rounded-md border border-border/60">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Incident</TableHead>
                        <TableHead>Interface</TableHead>
                        <TableHead>Strategy</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Operator</TableHead>
                        <TableHead>Time</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {mitigationPagination.pageItems.map((row) => {
                        const active = selectedMitigation?._id === row._id
                        return (
                          <TableRow
                            key={row._id || row.incidentId}
                            className={cn('cursor-pointer', active && 'bg-primary/10')}
                            onClick={() => onSelectMitigation(row)}
                          >
                            <TableCell className="mono text-xs font-medium">
                              {row.incidentId}
                            </TableCell>
                            <TableCell className="font-medium">{row.interface}</TableCell>
                            <TableCell className="text-xs font-semibold uppercase text-sky-400">
                              {row.strategy}
                            </TableCell>
                            <TableCell>
                              <MitigationStatusBadge status={row.status} />
                            </TableCell>
                            <TableCell className="font-mono text-sm text-muted-foreground">
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
                </div>
                {mitigationPagination.totalPages > 1 ? (
                  <PaginationControls
                    page={mitigationPagination.page}
                    totalPages={Math.max(mitigationPagination.totalPages, 1)}
                    total={mitigationPagination.total}
                    limit={mitigationPagination.limit}
                    onPageChange={mitigationPagination.setPage}
                    onLimitChange={mitigationPagination.setLimit}
                    limitOptions={[5, 10, 25, 50]}
                    unitLabel="Mitigation rows"
                  />
                ) : null}
                </div>
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">
                      {selectedMitigation
                        ? `Mitigation ${selectedMitigation.incidentId}`
                        : 'Select a log'}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2">
                    {!selectedMitigation ? (
                      <p className="text-sm text-muted-foreground">
                        Click a row for command and verification detail.
                      </p>
                    ) : (
                      <>
                        <p className="text-sm">
                          Interface:{' '}
                          <span className="font-semibold text-primary">
                            {selectedMitigation.interface}
                          </span>
                        </p>
                        <MitigationStatusBadge status={selectedMitigation.status} />
                        <div className="max-h-40 overflow-auto rounded-md border border-border bg-zinc-950 p-3 font-mono text-xs text-green-400">
                          {(selectedMitigation.commandsExecuted || []).map((cmd, idx) => (
                            <div key={idx} className="leading-relaxed">
                              <span className="mr-2 text-zinc-600">$</span>
                              {cmd}
                            </div>
                          ))}
                        </div>
                        <pre className="max-h-32 overflow-auto rounded-md border border-border bg-zinc-950/80 p-3 font-mono text-xs text-zinc-300">
                          {JSON.stringify(selectedMitigation.verificationResult, null, 2)}
                        </pre>
                      </>
                    )}
                  </CardContent>
                </Card>
              </div>
            )}
          </Subsection>

          {/* Recovery history */}
          <Subsection
            title="Recovery History"
            description="Recovery Safety (R1–R8) outcomes and stabilization for this switch."
          >
            {device.recovery.length === 0 ? (
              <p className="text-sm text-muted-foreground">No recovery logs yet.</p>
            ) : (
              <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(260px,1fr)]">
                <div className="space-y-3">
                <div className="overflow-x-auto rounded-md border border-border/60">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Incident</TableHead>
                        <TableHead>Interface</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Attempts</TableHead>
                        <TableHead>Time</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {recoveryPagination.pageItems.map((row) => {
                        const active = selectedRecovery?._id === row._id
                        return (
                          <TableRow
                            key={row._id || row.incidentId}
                            className={cn('cursor-pointer', active && 'bg-primary/10')}
                            onClick={() => onSelectRecovery(row)}
                          >
                            <TableCell className="mono text-xs font-medium">
                              {row.incidentId}
                            </TableCell>
                            <TableCell className="font-medium">{row.interface}</TableCell>
                            <TableCell>
                              <RecoveryStatusBadge status={row.recoveryStatus} />
                            </TableCell>
                            <TableCell className="text-sm">{row.retryCount} attempts</TableCell>
                            <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                              {formatRelative(row.timestamp) || '—'}
                            </TableCell>
                          </TableRow>
                        )
                      })}
                    </TableBody>
                  </Table>
                </div>
                {recoveryPagination.totalPages > 1 ? (
                  <PaginationControls
                    page={recoveryPagination.page}
                    totalPages={Math.max(recoveryPagination.totalPages, 1)}
                    total={recoveryPagination.total}
                    limit={recoveryPagination.limit}
                    onPageChange={recoveryPagination.setPage}
                    onLimitChange={recoveryPagination.setLimit}
                    limitOptions={[5, 10, 25, 50]}
                    unitLabel="Recovery rows"
                  />
                ) : null}
                </div>
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">
                      {selectedRecovery
                        ? `Recovery ${selectedRecovery.incidentId}`
                        : 'Select a log'}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {!selectedRecovery ? (
                      <p className="text-sm text-muted-foreground">
                        Click a row for recovery verification detail.
                      </p>
                    ) : (
                      <>
                        <RecoveryStatusBadge status={selectedRecovery.recoveryStatus} />
                        <RecoveryChecksList
                          checks={
                            selectedRecovery.checks &&
                            Object.keys(selectedRecovery.checks).length > 0
                              ? selectedRecovery.checks
                              : selectedRecovery.verificationResult?.checks
                          }
                          failedRule={
                            selectedRecovery.failedRule ||
                            selectedRecovery.verificationResult?.failedRule
                          }
                        />
                        {selectedRecovery.verificationResult?.output ? (
                          <pre className="max-h-32 overflow-auto rounded-md border border-border bg-zinc-950 p-3 font-mono text-xs text-zinc-300">
                            {selectedRecovery.verificationResult.output}
                          </pre>
                        ) : null}
                      </>
                    )}
                  </CardContent>
                </Card>
              </div>
            )}
          </Subsection>
        </CardContent>
      ) : null}
    </Card>
  )
}
