import { type ReactNode } from 'react'
import { Badge } from '@/shared/ui/badge'
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

export const FETCH_LIMIT = 500
export const DEFAULT_SWITCHES_PER_PAGE = 10
export const DEFAULT_SECTION_ROWS_PER_PAGE = 10
export const COLLAPSE_KEY = 'netpulse.stormProtection.collapsedDevices'
export const NAV_SCROLL_OFFSET_PX = 88

export type SwitchStormSectionData = {
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

export function isManagedSwitch(deviceType: string | null | undefined): boolean {
  return (deviceType || '').trim().toLowerCase() === 'switch'
}

export function EligibilityBadge({ eligible }: { eligible: boolean }) {
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

export function severityTone(severity: string | undefined): {
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

export function SeverityBadge({ severity }: { severity: string }) {
  const tone = severityTone(severity)
  return (
    <Badge variant={tone.badge} className="font-semibold uppercase tracking-wide">
      <span className={cn('h-1.5 w-1.5 rounded-full', tone.bar)} aria-hidden />
      {severity}
    </Badge>
  )
}

export const SOURCE_LABELS: Record<string, string> = {
  LIKELY_SOURCE: 'Likely Source',
  POSSIBLE_SOURCE: 'Possible Source',
  LIKELY_FORWARDER: 'Likely Forwarder',
  LIKELY_RECEIVER: 'Likely Receiver',
  NORMAL: 'Normal',
  UNKNOWN: 'Unknown',
}

export function SourceBadge({
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

export function IncidentTypeBadge({ incidentType }: { incidentType?: string | null }) {
  const value = (incidentType || 'STORM').toUpperCase()
  if (value === 'EMERGENCY') {
    return (
      <Badge variant="critical" className="font-semibold uppercase tracking-wide">
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
    <Badge variant="storm" className="font-semibold uppercase tracking-wide">
      STORM
    </Badge>
  )
}

export function formatRate(value: number | null | undefined, suffix = '/s'): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  const n = Number(value)
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k${suffix}`
  return `${n.toFixed(n >= 10 ? 0 : 1)}${suffix}`
}

export function ConfirmationStateBadge({ state }: { state: string }) {
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

export function ConfirmationProgressBar({
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

export function SafetyStatusBadge({ status }: { status: string }) {
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

export function formatCooldown(seconds: number | null | undefined): string {
  const s = Math.max(0, Number(seconds) || 0)
  if (s <= 0) return 'Ready'
  const m = Math.floor(s / 60)
  const rem = s % 60
  if (m <= 0) return `${rem}s`
  return `${m}m ${rem}s`
}

export function IncidentStatusBadge({ status }: { status: string }) {
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

export function MitigationStatusBadge({ status }: { status: string }) {
  const value = (status || '').toUpperCase()
  if (value === 'SUCCESS') {
    return (
      <Badge variant="mitigation" className="font-semibold uppercase tracking-wide">
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
      <Badge variant="critical" className="font-semibold uppercase tracking-wide">
        Rollback Failed
      </Badge>
    )
  }
  return (
    <Badge variant="mitigation" className="font-semibold uppercase tracking-wide">
      {status}
    </Badge>
  )
}

export function RecoveryStatusBadge({ status }: { status: string }) {
  const value = (status || '').toUpperCase()
  if (value === 'RECONCILED') {
    return (
      <Badge variant="info" className="font-semibold uppercase tracking-wide">
        Reconciled
      </Badge>
    )
  }
  if (value === 'RECOVERED' || value === 'SUCCESS') {
    return (
      <Badge variant="recovery" className="font-semibold uppercase tracking-wide">
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
      <Badge variant="info" className="font-semibold uppercase tracking-wide">
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
      <Badge variant="mitigation" className="font-semibold uppercase tracking-wide">
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

export const RECOVERY_CHECK_LABELS: Record<string, string> = {
  stormCleared: 'Storm Cleared',
  riskBelowThreshold: 'Risk Below Threshold',
  cooldownExpired: 'Cooldown Complete',
  deviceReachable: 'Device Reachable',
  sshReachable: 'SSH Reachable',
  interfaceAdminDown: 'Interface Admin Down',
  noNewerActiveIncident: 'No Newer Active Incident',
  recoveryLockAvailable: 'Recovery Lock Available',
}

export function RecoveryChecksList({
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

export function JsonSection({
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

export function exportIncident(incident: StormIncident) {
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

export function classificationIface(row: EligibilityResult): Pick<
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

export function MetricCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border/50 px-2.5 py-1.5">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mono text-sm font-medium">{value}</p>
    </div>
  )
}

export function Kpi({
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

export function Subsection({
  title,
  description,
  children,
  id,
  loading = false,
}: {
  title: string
  description?: string
  children: ReactNode
  id?: string
  loading?: boolean
}) {
  return (
    <div id={id} className="scroll-mt-24 space-y-3 border-t border-border/60 px-4 py-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h4 className="text-sm font-semibold tracking-tight">{title}</h4>
          {description ? (
            <p className="text-xs text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {loading ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-border/60 bg-secondary/30 px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" aria-hidden="true" />
            Loading…
          </span>
        ) : null}
      </div>
      {children}
    </div>
  )
}

export function scrollToStormSection(targetId: string) {
  window.requestAnimationFrame(() => {
    window.setTimeout(() => {
      if (targetId === 'overview-section') {
        window.scrollTo({ top: 0, behavior: 'smooth' })
        return
      }
      const el = document.getElementById(targetId)
      if (!el) return
      const top = el.getBoundingClientRect().top + window.scrollY - NAV_SCROLL_OFFSET_PX
      window.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
    }, 80)
  })
}

export function summarizeSwitch(section: SwitchStormSectionData) {
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
