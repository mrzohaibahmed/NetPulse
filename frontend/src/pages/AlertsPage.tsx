import { useMemo, useState } from 'react'
import { Check, Search, X } from 'lucide-react'
import { motion } from 'framer-motion'
import { useAlertMutations, useAlertsQuery } from '@/hooks/queries'
import { formatDateTime } from '@/utils/format'
import type { AlertItem } from '@/types'
import { EmptyState } from '@/components/shared/EmptyState'
import { ErrorState } from '@/components/shared/ErrorState'
import { TableSkeleton } from '@/components/shared/LoadingState'
import { PageHeader } from '@/components/shared/PageHeader'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

export function AlertsPage() {
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
        a.message.toLowerCase().includes(q),
    )
  }, [alerts, query])

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
        <div className="relative space-y-0 pl-4 before:absolute before:bottom-4 before:left-[21px] before:top-4 before:w-px before:bg-border">
          {filtered.map((alert, index) => (
            <AlertTimelineItem
              key={alert._id}
              alert={alert}
              index={index}
              onAck={() => acknowledge.mutate(alert._id)}
              onDismiss={() => dismiss.mutate(alert._id)}
              busy={acknowledge.isPending || dismiss.isPending}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function AlertTimelineItem({
  alert,
  index,
  onAck,
  onDismiss,
  busy,
}: {
  alert: AlertItem
  index: number
  onAck: () => void
  onDismiss: () => void
  busy: boolean
}) {
  const severity =
    alert.status === 'Offline (Critical)' || alert.status === 'Offline'
      ? 'danger'
      : alert.status === 'Not Reachable'
        ? 'warning'
        : 'default'

  const border =
    severity === 'danger'
      ? 'border-l-danger'
      : severity === 'warning'
        ? 'border-l-warning'
        : 'border-l-primary'

  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: Math.min(index * 0.03, 0.3) }}
      className="relative pb-4 pl-8"
    >
      <span
        className={`absolute left-0 top-5 h-3.5 w-3.5 rounded-full border-2 border-background ${
          severity === 'danger'
            ? 'bg-danger'
            : severity === 'warning'
              ? 'bg-warning'
              : 'bg-primary'
        }`}
      />
      <Card className={`glass border-l-[3px] ${border}`}>
        <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <p className="font-semibold">{alert.hostname}</p>
              <StatusBadge status={alert.status} pulse={false} />
              {alert.acknowledged ? <Badge variant="secondary">Acknowledged</Badge> : null}
              {alert.dismissed ? <Badge variant="muted">Dismissed</Badge> : null}
              {alert.emailSent ? <Badge variant="outline">Email sent</Badge> : null}
            </div>
            <p className="text-sm text-muted-foreground">{alert.message}</p>
            <p className="mono text-xs text-muted-foreground">
              {alert.ipAddress} · {alert.scanType} · {formatDateTime(alert.createdAt)}
            </p>
          </div>
          {!alert.dismissed ? (
            <div className="flex shrink-0 gap-2">
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
