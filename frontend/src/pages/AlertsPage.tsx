import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Check, CloudLightning, Search, X } from 'lucide-react'
import { motion } from 'framer-motion'
import { useAuth } from '@/auth/AuthContext'
import { useAlertMutations, useAlertsQuery } from '@/hooks/queries'
import { useClientPagination } from '@/hooks/useClientPagination'
import { formatDateTime } from '@/utils/format'
import type { AlertItem } from '@/types'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { TableSkeleton } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent } from '@/shared/ui/card'
import { Input } from '@/shared/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select'
import { cn } from '@/lib/utils'

function isStormAlert(alert: AlertItem): boolean {
  const category = (alert.category || alert.alertType || alert.scanType || '').toLowerCase()
  return category.includes('storm')
}

function alertSeverityTone(alert: AlertItem): 'danger' | 'warning' | 'info' | 'default' {
  const severity = (alert.severity || '').toUpperCase()
  if (severity === 'CRITICAL') return 'danger'
  if (severity === 'WARNING') return 'warning'
  if (severity === 'INFO') return 'info'

  if (alert.status === 'Offline (Critical)' || alert.status === 'Offline') return 'danger'
  if (alert.status === 'Not Reachable') return 'warning'
  if (alert.status === 'MITIGATION_FAILED' || alert.status === 'RECOVERY_FAILED') return 'danger'
  if (alert.status === 'MITIGATED') return 'danger'
  if (alert.status === 'RECOVERED' || alert.status === 'MONITORING') return 'info'
  return 'default'
}

function SeverityBadge({ severity }: { severity: string }) {
  const value = severity.toUpperCase()
  const variant =
    value === 'CRITICAL' ? 'danger' : value === 'WARNING' ? 'warning' : value === 'INFO' ? 'success' : 'secondary'
  return (
    <Badge variant={variant} className="font-semibold uppercase tracking-wide">
      {value}
    </Badge>
  )
}

export function AlertsPage() {
  const { isOperator } = useAuth()
  const navigate = useNavigate()
  const [status, setStatus] = useState('active')
  const [query, setQuery] = useState('')
  const alertsQuery = useAlertsQuery(status, 100)
  const { acknowledge, dismiss } = useAlertMutations()

  const alerts = alertsQuery.data?.data ?? []

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return alerts
    return alerts.filter(
      (a) =>
        a.hostname.toLowerCase().includes(q) ||
        a.ipAddress.toLowerCase().includes(q) ||
        a.message.toLowerCase().includes(q) ||
        (a.title || '').toLowerCase().includes(q) ||
        (a.interface || '').toLowerCase().includes(q) ||
        (a.incidentId || '').toLowerCase().includes(q) ||
        (a.category || '').toLowerCase().includes(q),
    )
  }, [alerts, query])

  const pagination = useClientPagination(filtered, 25)

  useEffect(() => {
    pagination.reset()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset on filter/status change
  }, [query, status])

  return (
    <div className="space-y-6">
      <PageHeader
        title="Alert Center"
        description="Track, acknowledge, and dismiss network alerts"
        actions={
          <Button type="button" variant="secondary" onClick={() => void alertsQuery.refetch()}>
            Refresh
          </Button>
        }
      />

      <div className="flex flex-wrap gap-3">
        <div className="relative max-w-sm flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-9"
            placeholder="Search alerts…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="w-[180px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="active">Active</SelectItem>
            <SelectItem value="acknowledged">Acknowledged</SelectItem>
            <SelectItem value="dismissed">Dismissed</SelectItem>
            <SelectItem value="all">All</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {alertsQuery.isLoading && alerts.length === 0 ? (
        <TableSkeleton rows={5} />
      ) : alertsQuery.error && alerts.length === 0 ? (
        <ErrorState
          message={alertsQuery.error instanceof Error ? alertsQuery.error.message : 'Failed to load alerts'}
          onRetry={() => void alertsQuery.refetch()}
        />
      ) : filtered.length === 0 ? (
        <EmptyState title="No alerts" description="Nothing matches the current filters." />
      ) : (
        <>
          <div className="relative space-y-0 pl-4 before:absolute before:bottom-4 before:left-[21px] before:top-4 before:w-px before:bg-border">
            {pagination.pageItems.map((alert, index) => (
              <AlertTimelineItem
                key={alert._id}
                alert={alert}
                index={index}
                canAct={isOperator}
                onAck={() => acknowledge.mutate(alert._id)}
                onDismiss={() => dismiss.mutate(alert._id)}
                busy={acknowledge.isPending || dismiss.isPending}
                onOpenStorm={
                  isStormAlert(alert) && alert.incidentId
                    ? () => navigate(`/storm?incident=${encodeURIComponent(alert.incidentId!)}`)
                    : isStormAlert(alert)
                      ? () => navigate('/storm')
                      : undefined
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
    </div>
  )
}

function AlertTimelineItem({
  alert,
  index,
  canAct,
  onAck,
  onDismiss,
  busy,
  onOpenStorm,
}: {
  alert: AlertItem
  index: number
  canAct: boolean
  onAck: () => void
  onDismiss: () => void
  busy: boolean
  onOpenStorm?: () => void
}) {
  const storm = isStormAlert(alert)
  const severity = alertSeverityTone(alert)

  const border =
    severity === 'danger'
      ? 'border-l-danger'
      : severity === 'warning'
        ? 'border-l-warning'
        : severity === 'info'
          ? 'border-l-success'
          : 'border-l-primary'

  const displayName = alert.deviceName || alert.hostname
  const category = alert.category || alert.alertType || alert.scanType

  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: Math.min(index * 0.03, 0.3) }}
      className="relative pb-4 pl-8"
    >
      <span
        className={cn(
          'absolute left-0 top-5 flex h-3.5 w-3.5 items-center justify-center rounded-full border-2 border-background',
          severity === 'danger'
            ? 'bg-danger'
            : severity === 'warning'
              ? 'bg-warning'
              : severity === 'info'
                ? 'bg-success'
                : 'bg-primary',
        )}
      />
      <Card
        className={cn(
          'glass border-l-[3px]',
          border,
          onOpenStorm && 'cursor-pointer transition-colors hover:bg-muted/40',
        )}
        onClick={onOpenStorm}
        role={onOpenStorm ? 'link' : undefined}
        tabIndex={onOpenStorm ? 0 : undefined}
        onKeyDown={
          onOpenStorm
            ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onOpenStorm()
                }
              }
            : undefined
        }
      >
        <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              {storm ? (
                <span className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-primary/10 text-primary">
                  <CloudLightning className="h-4 w-4" aria-hidden />
                </span>
              ) : null}
              <p className="font-semibold">{alert.title || displayName}</p>
              {alert.severity ? (
                <SeverityBadge severity={alert.severity} />
              ) : (
                <StatusBadge status={alert.status} pulse={false} />
              )}
              {storm ? <Badge variant="outline">Storm Protection</Badge> : null}
              {alert.acknowledged ? <Badge variant="secondary">Acknowledged</Badge> : null}
              {alert.dismissed ? <Badge variant="muted">Dismissed</Badge> : null}
              {alert.emailSent ? <Badge variant="outline">Email sent</Badge> : null}
            </div>
            <p className="whitespace-pre-line text-sm text-muted-foreground">{alert.message}</p>
            <div className="flex flex-wrap gap-x-3 gap-y-1 mono text-xs text-muted-foreground">
              <span>{displayName}</span>
              <span>{alert.ipAddress}</span>
              {alert.interface ? <span>if {alert.interface}</span> : null}
              {alert.incidentId ? <span>{alert.incidentId}</span> : null}
              {alert.riskScore != null ? <span>risk {Number(alert.riskScore).toFixed(0)}</span> : null}
              {alert.action ? <span>{alert.action}</span> : null}
              <span>{alert.status}</span>
              {alert.recoveryDuration ? <span>{alert.recoveryDuration}</span> : null}
              <span>{category}</span>
              <span>{formatDateTime(alert.createdAt)}</span>
            </div>
          </div>
          {canAct && !alert.dismissed ? (
            <div
              className="flex shrink-0 gap-2"
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => e.stopPropagation()}
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
            </div>
          ) : null}
        </CardContent>
      </Card>
    </motion.div>
  )
}
