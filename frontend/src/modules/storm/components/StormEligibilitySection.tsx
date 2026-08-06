import { Link } from 'react-router-dom'
import { PortClassificationBadges } from '@/modules/storm/components/InterfaceStatusBadge'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { Badge } from '@/shared/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import { useClientPagination } from '@/hooks/useClientPagination'
import type { EligibilityResult } from '@/types'
import { formatRelative } from '@/utils/format'
import {
  classificationIface,
  DEFAULT_SECTION_ROWS_PER_PAGE,
  EligibilityBadge,
  Subsection,
} from './stormShared'

type StormEligibilitySectionProps = {
  rows: EligibilityResult[]
  isLoading?: boolean
}

export function StormEligibilitySection({ rows, isLoading = false }: StormEligibilitySectionProps) {
  const eligibilityPagination = useClientPagination(rows, DEFAULT_SECTION_ROWS_PER_PAGE)

  return (
    <Subsection
      title="Port Eligibility"
      description="Which access ports on this switch may enter risk scoring."
      loading={isLoading}
    >
      {rows.length === 0 ? (
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
                    <TableCell className="max-w-[200px] truncate text-sm">{row.reason}</TableCell>
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
  )
}
