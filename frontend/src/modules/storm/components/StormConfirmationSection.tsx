import { Link } from 'react-router-dom'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import { useClientPagination } from '@/hooks/useClientPagination'
import type { ConfirmationResult } from '@/types'
import { formatRelative } from '@/utils/format'
import {
  ConfirmationProgressBar,
  ConfirmationStateBadge,
  DEFAULT_SECTION_ROWS_PER_PAGE,
  Subsection,
} from './stormShared'

type StormConfirmationSectionProps = {
  rows: ConfirmationResult[]
  requiredConfirmations: number
  isLoading?: boolean
}

export function StormConfirmationSection({
  rows,
  requiredConfirmations,
  isLoading = false,
}: StormConfirmationSectionProps) {
  const confirmationPagination = useClientPagination(rows, DEFAULT_SECTION_ROWS_PER_PAGE)

  return (
    <Subsection
      title="Confirmation"
      description={`High risk must persist across ${requiredConfirmations} consecutive polls.`}
      loading={isLoading}
    >
      {rows.length === 0 ? (
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
  )
}
