import { PaginationControls } from '@/shared/components/PaginationControls'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import { useClientPagination } from '@/hooks/useClientPagination'
import { cn } from '@/lib/utils'
import type { RecoveryLog } from '@/types'
import { formatRelative } from '@/utils/format'
import {
  DEFAULT_SECTION_ROWS_PER_PAGE,
  RecoveryChecksList,
  RecoveryStatusBadge,
  Subsection,
} from './stormShared'

function isReconciledRow(row: RecoveryLog): boolean {
  return (row.recoveryStatus || '').toUpperCase() === 'RECONCILED'
}

function recoveryRuleFor(row: RecoveryLog): string | null {
  return (
    row.recoveryRule ||
    row.failedRule ||
    row.verificationResult?.recoveryRule ||
    row.verificationResult?.failedRule ||
    null
  )
}

function ReconciliationDetail({ row }: { row: RecoveryLog }) {
  const v = row.verificationResult
  const items: { label: string; value: string }[] = [
    {
      label: 'Recovery Rule',
      value: row.recoveryRule || v?.recoveryRule || row.failedRule || v?.failedRule || '—',
    },
    { label: 'Reason', value: row.reason || v?.reason || '—' },
    { label: 'Previous Status', value: row.previousStatus || v?.previousStatus || '—' },
    { label: 'New Status', value: row.newStatus || v?.newStatus || '—' },
    { label: 'Engine', value: row.engine || v?.engine || '—' },
    {
      label: 'Detected By',
      value: row.detectedBy || v?.detectedBy || '—',
    },
    {
      label: 'Reconciled',
      value: row.reconciled === true || v?.reconciled === true ? 'Yes' : 'No',
    },
  ]

  return (
    <dl className="space-y-2 rounded-md border border-sky-400/30 bg-sky-400/10 p-3 text-sm">
      {v?.note ? (
        <p className="mb-2 text-muted-foreground">{v.note}</p>
      ) : null}
      {items.map(({ label, value }) => (
        <div key={label} className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
          <dt className="min-w-[7.5rem] shrink-0 font-medium text-muted-foreground">{label}</dt>
          <dd className="font-mono text-xs text-foreground">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

type StormRecoverySectionProps = {
  rows: RecoveryLog[]
  selectedRecovery: RecoveryLog | null
  onSelectRecovery: (row: RecoveryLog) => void
}

export function StormRecoverySection({
  rows,
  selectedRecovery,
  onSelectRecovery,
}: StormRecoverySectionProps) {
  const recoveryPagination = useClientPagination(rows, DEFAULT_SECTION_ROWS_PER_PAGE)

  return (
    <Subsection
      title="Recovery History"
      description="Recovery Safety (R1–R8) outcomes and stabilization for this switch."
    >
      {rows.length === 0 ? (
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
                    <TableHead>Rule</TableHead>
                    <TableHead>Attempts</TableHead>
                    <TableHead>Time</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {recoveryPagination.pageItems.map((row) => {
                    const active = selectedRecovery?._id === row._id
                    const rule = recoveryRuleFor(row)
                    const blockReason = row.verificationResult?.error || null
                    return (
                      <TableRow
                        key={row._id || `${row.incidentId}-${row.timestamp}`}
                        className={cn('cursor-pointer', active && 'bg-primary/10')}
                        onClick={() => onSelectRecovery(row)}
                        title={blockReason || row.verificationResult?.note || undefined}
                      >
                        <TableCell className="mono text-xs font-medium">
                          {row.incidentId}
                        </TableCell>
                        <TableCell className="font-medium">{row.interface}</TableCell>
                        <TableCell>
                          <RecoveryStatusBadge status={row.recoveryStatus} />
                        </TableCell>
                        <TableCell className="mono text-xs text-muted-foreground">
                          {rule || '—'}
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
              ) : isReconciledRow(selectedRecovery) ? (
                <>
                  <RecoveryStatusBadge status={selectedRecovery.recoveryStatus} />
                  <ReconciliationDetail row={selectedRecovery} />
                </>
              ) : (
                <>
                  <RecoveryStatusBadge status={selectedRecovery.recoveryStatus} />
                  {selectedRecovery.verificationResult?.error ? (
                    <p className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning">
                      {selectedRecovery.verificationResult.error}
                    </p>
                  ) : null}
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
  )
}
