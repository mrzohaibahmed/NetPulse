import type { HardwareHealthStatus } from '@/api/switchHardwareService'
import { Badge } from '@/shared/ui/badge'
import { cn } from '@/lib/utils'

export function formatHardwareValue(
  value: number | string | null | undefined,
  suffix = '',
): string {
  if (value === null || value === undefined || value === '') return 'Not available'
  return `${value}${suffix}`
}

export function formatHealthLabel(status: HardwareHealthStatus | null | undefined): string {
  const value = (status || 'unknown').toLowerCase()
  if (value === 'healthy') return 'Healthy'
  if (value === 'warning') return 'Warning'
  if (value === 'critical') return 'Critical'
  if (value === 'not_available') return 'Not available'
  if (value === 'unknown') return 'Unknown'
  return status || 'Unknown'
}

export function healthBadgeVariant(
  status: HardwareHealthStatus | null | undefined,
): 'success' | 'warning' | 'danger' | 'muted' | 'secondary' {
  const value = (status || 'unknown').toLowerCase()
  if (value === 'healthy') return 'success'
  if (value === 'warning') return 'warning'
  if (value === 'critical') return 'danger'
  if (value === 'not_available') return 'secondary'
  return 'muted'
}

export function HardwareHealthBadge({
  status,
  className,
}: {
  status: HardwareHealthStatus | null | undefined
  className?: string
}) {
  const variant = healthBadgeVariant(status)
  return (
    <Badge variant={variant} className={cn('whitespace-nowrap font-semibold', className)}>
      <span
        className={cn(
          'h-1.5 w-1.5 rounded-full',
          variant === 'success' && 'bg-success',
          variant === 'warning' && 'bg-warning',
          variant === 'danger' && 'bg-danger',
          variant === 'secondary' && 'bg-muted-foreground',
          variant === 'muted' && 'bg-slate-400',
        )}
        aria-hidden
      />
      {formatHealthLabel(status)}
    </Badge>
  )
}

export function formatRootCause(rootCause: string | null | undefined): string {
  if (!rootCause || rootCause === 'unknown') return 'Unknown'
  return rootCause
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

export function formatConfidence(confidence: string | null | undefined): string {
  const value = (confidence || 'unknown').toLowerCase()
  if (value === 'confirmed') return 'Confirmed'
  if (value === 'suspected') return 'Suspected'
  return 'Unknown'
}

export function confidenceBadgeVariant(
  confidence: string | null | undefined,
): 'success' | 'warning' | 'muted' {
  const value = (confidence || 'unknown').toLowerCase()
  if (value === 'confirmed') return 'success'
  if (value === 'suspected') return 'warning'
  return 'muted'
}

export function formatDurationSeconds(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return 'Not available'
  if (seconds < 60) return `${seconds}s`
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  if (mins < 60) return `${mins}m ${secs}s`
  const hours = Math.floor(mins / 60)
  const remMins = mins % 60
  return `${hours}h ${remMins}m`
}

export function eventSeverityVariant(
  severity: string | null | undefined,
): 'danger' | 'warning' | 'success' | 'secondary' | 'muted' {
  const value = (severity || '').toLowerCase()
  if (value === 'critical' || value === 'failure') return 'danger'
  if (value === 'warning') return 'warning'
  if (value === 'info' || value === 'recovery') return 'success'
  return 'muted'
}

export function isRecoveryEvent(eventType: string): boolean {
  return eventType.toLowerCase().includes('recover')
}
