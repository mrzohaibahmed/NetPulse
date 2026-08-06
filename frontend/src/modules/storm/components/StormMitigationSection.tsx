import { PaginationControls } from '@/shared/components/PaginationControls'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import { useClientPagination } from '@/hooks/useClientPagination'
import { cn } from '@/lib/utils'
import type { MitigationLog } from '@/types'
import { formatRelative } from '@/utils/format'
import {
  DEFAULT_SECTION_ROWS_PER_PAGE,
  MitigationStatusBadge,
  Subsection,
} from './stormShared'

type StormMitigationSectionProps = {
  rows: MitigationLog[]
  selectedMitigation: MitigationLog | null
  onSelectMitigation: (row: MitigationLog) => void
}

export function StormMitigationSection({
  rows,
  selectedMitigation,
  onSelectMitigation,
}: StormMitigationSectionProps) {
  const mitigationPagination = useClientPagination(rows, DEFAULT_SECTION_ROWS_PER_PAGE)

  return (
    <Subsection
      title="Mitigation History"
      description="Shutdown and rollback execution logs for this switch."
    >
      {rows.length === 0 ? (
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
  )
}
