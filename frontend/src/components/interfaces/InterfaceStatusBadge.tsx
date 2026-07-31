import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { NetworkInterface } from '@/types'

type StatusKind = 'admin' | 'oper'

function toneFor(status: string | null | undefined): 'success' | 'danger' | 'muted' | 'warning' {
  const value = (status || '').toLowerCase()
  if (value === 'up' || value === 'connected') return 'success'
  if (value === 'down' || value === 'disabled' || value === 'notconnect') return 'danger'
  if (value === 'unknown' || !value) return 'muted'
  return 'warning'
}

interface InterfaceStatusBadgeProps {
  status: string | null | undefined
  kind?: StatusKind
  className?: string
}

export function InterfaceStatusBadge({
  status,
  kind = 'oper',
  className,
}: InterfaceStatusBadgeProps) {
  const label = (status || 'unknown').toLowerCase()
  const variant = toneFor(label)

  return (
    <Badge variant={variant} className={cn('capitalize', className)}>
      <span
        className={cn(
          'h-1.5 w-1.5 rounded-full',
          variant === 'success' && 'bg-success',
          variant === 'danger' && 'bg-danger',
          variant === 'warning' && 'bg-warning',
          variant === 'muted' && 'bg-slate-400',
        )}
        aria-hidden
      />
      {kind === 'admin' ? `admin ${label}` : label}
    </Badge>
  )
}

interface PortModeBadgeProps {
  mode: string | null | undefined
  className?: string
}

export function PortModeBadge({ mode, className }: PortModeBadgeProps) {
  const value = (mode || 'unknown').toLowerCase()

  const styles: Record<string, string> = {
    access: 'border-success/40 bg-success/15 text-success',
    trunk: 'border-primary/40 bg-primary/15 text-primary',
    routed: 'border-slate-400/40 bg-slate-500/15 text-slate-300',
    unknown: 'border-border bg-muted/40 text-muted-foreground',
  }

  const dots: Record<string, string> = {
    access: 'bg-success',
    trunk: 'bg-primary',
    routed: 'bg-slate-300',
    unknown: 'bg-slate-500',
  }

  return (
    <Badge
      variant="outline"
      className={cn('capitalize', styles[value] || styles.unknown, className)}
    >
      <span className={cn('h-1.5 w-1.5 rounded-full', dots[value] || dots.unknown)} aria-hidden />
      {value}
    </Badge>
  )
}

type ClassificationFlag =
  | 'access'
  | 'trunk'
  | 'uplink'
  | 'infrastructure'
  | 'management'
  | 'protected'

const CLASSIFICATION_STYLES: Record<ClassificationFlag, string> = {
  access: 'border-success/40 bg-success/15 text-success',
  trunk: 'border-primary/40 bg-primary/15 text-primary',
  uplink: 'border-amber-400/40 bg-amber-500/15 text-amber-200',
  infrastructure: 'border-sky-400/40 bg-sky-500/15 text-sky-200',
  management: 'border-violet-400/40 bg-violet-500/15 text-violet-200',
  protected: 'border-danger/40 bg-danger/15 text-danger',
}

interface ClassificationBadgeProps {
  flag: ClassificationFlag
  className?: string
}

export function ClassificationBadge({ flag, className }: ClassificationBadgeProps) {
  return (
    <Badge
      variant="outline"
      className={cn('capitalize', CLASSIFICATION_STYLES[flag], className)}
    >
      {flag}
    </Badge>
  )
}

interface PortClassificationBadgesProps {
  iface: Pick<
    NetworkInterface,
    | 'portMode'
    | 'mode'
    | 'isAccess'
    | 'isTrunk'
    | 'isUplink'
    | 'isInfrastructure'
    | 'isManagement'
    | 'isProtected'
  >
  className?: string
  /** When true, also render Access/Trunk (in addition to PortModeBadge elsewhere). */
  includeMode?: boolean
}

export function PortClassificationBadges({
  iface,
  className,
  includeMode = false,
}: PortClassificationBadgesProps) {
  const badges: ClassificationFlag[] = []
  if (includeMode) {
    if (iface.isAccess || iface.portMode === 'access' || iface.mode === 'access') {
      badges.push('access')
    }
    if (iface.isTrunk || iface.portMode === 'trunk' || iface.mode === 'trunk') {
      badges.push('trunk')
    }
  }
  if (iface.isUplink) badges.push('uplink')
  if (iface.isInfrastructure) badges.push('infrastructure')
  if (iface.isManagement) badges.push('management')
  if (iface.isProtected) badges.push('protected')

  if (badges.length === 0) return null

  return (
    <div className={cn('flex flex-wrap items-center gap-1.5', className)}>
      {badges.map((flag) => (
        <ClassificationBadge key={flag} flag={flag} />
      ))}
    </div>
  )
}

interface UtilizationBarProps {
  value: number | null | undefined
  className?: string
}

export function UtilizationBar({ value, className }: UtilizationBarProps) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return <span className={cn('mono text-muted-foreground', className)}>—</span>
  }

  const pct = Math.min(Math.max(Number(value), 0), 100)
  const tone =
    pct >= 85 ? 'bg-danger' : pct >= 60 ? 'bg-warning' : 'bg-success'
  const label =
    pct === 0
      ? '0%'
      : pct > 0 && pct < 0.1
        ? `${pct.toFixed(3)}%`
        : pct < 10
          ? `${pct.toFixed(2)}%`
          : `${pct.toFixed(1)}%`
  // Bar width: amplify tiny util so the meter is visible without lying about %.
  const barWidth = pct > 0 && pct < 1 ? Math.max(pct * 8, 2) : pct

  return (
    <div className={cn('flex min-w-[96px] items-center gap-2', className)}>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-secondary">
        <div
          className={cn('h-full rounded-full transition-all', tone)}
          style={{ width: `${Math.min(barWidth, 100)}%` }}
        />
      </div>
      <span className="mono w-14 text-right text-xs text-muted-foreground">{label}</span>
    </div>
  )
}

export function formatAllowedVlans(vlans: number[] | null | undefined, max = 6): string {
  if (!vlans || vlans.length === 0) return '—'
  if (vlans.length <= max) return vlans.join(', ')
  return `${vlans.slice(0, max).join(', ')} +${vlans.length - max}`
}

export function neighborRemotePort(
  neighbor: { interface?: string; port?: string } | null | undefined,
): string {
  return neighbor?.interface || neighbor?.port || ''
}
