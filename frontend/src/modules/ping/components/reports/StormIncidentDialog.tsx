import type { ReactNode } from 'react'
import { useStormIncidentReportDetailQuery } from '@/hooks/queries'
import { formatDateTime } from '@/utils/format'
import { ErrorState } from '@/shared/components/ErrorState'
import { LoadingState } from '@/shared/components/LoadingState'
import { StatusBadge } from '@/shared/components/StatusBadge'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return null
}

function text(value: unknown): string {
  if (value == null || value === '') return '—'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

export function StormIncidentDialog({
  incidentId,
  onClose,
}: {
  incidentId: string | null
  onClose: () => void
}) {
  const detail = useStormIncidentReportDetailQuery(incidentId)
  const incident = detail.data?.incident
  const extra = asRecord(incident)

  return (
    <Dialog open={Boolean(incidentId)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Storm incident {incidentId}</DialogTitle>
          <DialogDescription>
            Timeline, risk evidence, mitigation, and recovery for this incident.
          </DialogDescription>
        </DialogHeader>

        {detail.isLoading ? <LoadingState label="Loading incident…" /> : null}
        {detail.error ? (
          <ErrorState
            message={detail.error instanceof Error ? detail.error.message : 'Failed to load incident'}
            onRetry={() => void detail.refetch()}
          />
        ) : null}

        {incident ? (
          <div className="space-y-5">
            <div className="grid gap-3 sm:grid-cols-2">
              <Meta label="Hostname" value={incident.hostname || 'Unknown'} />
              <Meta label="Interface" value={incident.interface} />
              <Meta label="Status" value={<StatusBadge status={incident.status} pulse={false} />} />
              <Meta label="Severity" value={incident.severity} />
              <Meta label="Started" value={formatDateTime(incident.createdAt)} />
              <Meta label="Recovered" value={formatDateTime(typeof extra?.recoveredAt === 'string' ? extra.recoveredAt : null)} />
            </div>

            <section className="space-y-2">
              <h4 className="text-sm font-semibold">Timeline</h4>
              {(detail.data?.timeline ?? []).length === 0 ? (
                <p className="text-sm text-muted-foreground">No timeline events stored for this incident.</p>
              ) : (
                <ol className="space-y-2 border-l border-border pl-4">
                  {(detail.data?.timeline ?? []).map((event, index) => (
                    <li key={`${event.event}-${index}`} className="text-sm">
                      <p className="font-medium">{event.event}</p>
                      <p className="text-muted-foreground">
                        {formatDateTime(event.time)} {event.detail ? `· ${event.detail}` : ''}
                      </p>
                    </li>
                  ))}
                </ol>
              )}
            </section>

            <section className="space-y-2">
              <h4 className="text-sm font-semibold">Risk evidence</h4>
              <div className="grid gap-2 sm:grid-cols-2 text-sm">
                <Meta label="Risk score" value={text(asRecord(detail.data?.riskEvidence)?.riskScore)} />
                <Meta label="Severity" value={text(asRecord(detail.data?.riskEvidence)?.severity)} />
              </div>
              {Array.isArray(detail.data?.contributors) && detail.data.contributors.length > 0 ? (
                <ul className="list-disc pl-5 text-sm text-muted-foreground">
                  {detail.data.contributors.map((item, index) => (
                    <li key={index}>{typeof item === 'string' ? item : JSON.stringify(item)}</li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">No contributor breakdown stored.</p>
              )}
            </section>

            <section className="space-y-2">
              <h4 className="text-sm font-semibold">Mitigation</h4>
              <EventTable
                rows={(detail.data?.mitigations ?? []).map((row) => ({
                  time: row.timestamp,
                  status: row.status,
                  detail: row.strategy || row.reason || '—',
                }))}
                empty="No mitigation history for this incident."
              />
            </section>

            <section className="space-y-2">
              <h4 className="text-sm font-semibold">Recovery</h4>
              <EventTable
                rows={(detail.data?.recoveries ?? []).map((row) => ({
                  time: row.timestamp,
                  status: row.recoveryStatus,
                  detail: row.reason || row.note || '—',
                }))}
                empty="No recovery history for this incident."
              />
            </section>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

function Meta({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <p className="np-caption">{label}</p>
      <div className="text-sm font-medium">{value}</div>
    </div>
  )
}

function EventTable({
  rows,
  empty,
}: {
  rows: Array<{ time: string; status: string; detail: string }>
  empty: string
}) {
  if (!rows.length) {
    return <p className="text-sm text-muted-foreground">{empty}</p>
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Time</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Detail</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, index) => (
          <TableRow key={`${row.time}-${index}`}>
            <TableCell>{formatDateTime(row.time)}</TableCell>
            <TableCell>
              <StatusBadge status={row.status} pulse={false} />
            </TableCell>
            <TableCell className="max-w-[20rem] truncate">{row.detail}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
