import { Link } from 'react-router-dom'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Badge } from '@/shared/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import { useClientPagination } from '@/hooks/useClientPagination'
import { cn } from '@/lib/utils'
import type { SafetyResult } from '@/types'
import { formatRelative } from '@/utils/format'
import {
  DEFAULT_SECTION_ROWS_PER_PAGE,
  formatCooldown,
  SafetyStatusBadge,
  Subsection,
} from './stormShared'

type StormSafetySectionProps = {
  rows: SafetyResult[]
  selectedSafety: SafetyResult | null
  onSelectSafety: (row: SafetyResult) => void
  isLoading?: boolean
}

export function StormSafetySection({
  rows,
  selectedSafety,
  onSelectSafety,
  isLoading = false,
}: StormSafetySectionProps) {
  const safetyPagination = useClientPagination(rows, DEFAULT_SECTION_ROWS_PER_PAGE)

  return (
    <Subsection
      title="Mitigation Safety"
      description="Final pre-mitigation gate for confirmed storms on this switch."
      loading={isLoading}
    >
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No safety results yet.</p>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(260px,1fr)]">
          <div className="space-y-3">
            <div className="overflow-x-auto rounded-md border border-border/60">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Interface</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Reason</TableHead>
                    <TableHead>Cooldown</TableHead>
                    <TableHead>Updated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {safetyPagination.pageItems.map((row) => {
                    const active =
                      selectedSafety?.deviceId === row.deviceId &&
                      selectedSafety?.interface === row.interface
                    return (
                      <TableRow
                        key={`${row.deviceId}-${row.interface}-${row._id ?? ''}`}
                        className={cn('cursor-pointer', active && 'bg-primary/10')}
                        onClick={() => onSelectSafety(row)}
                      >
                        <TableCell className="font-medium">
                          <Link
                            to={`/interfaces/${row.deviceId}/${encodeURIComponent(row.interface)}`}
                            className="text-primary hover:underline"
                            onClick={(e) => e.stopPropagation()}
                          >
                            {row.interface}
                          </Link>
                        </TableCell>
                        <TableCell>
                          <SafetyStatusBadge status={String(row.status)} />
                        </TableCell>
                        <TableCell className="max-w-[180px] truncate text-sm">
                          {row.reason}
                        </TableCell>
                        <TableCell className="mono text-xs">
                          {formatCooldown(row.cooldownRemainingSeconds)}
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
            {safetyPagination.totalPages > 1 ? (
              <PaginationControls
                page={safetyPagination.page}
                totalPages={Math.max(safetyPagination.totalPages, 1)}
                total={safetyPagination.total}
                limit={safetyPagination.limit}
                onPageChange={safetyPagination.setPage}
                onLimitChange={safetyPagination.setLimit}
                limitOptions={[5, 10, 25, 50]}
                unitLabel="Safety rows"
              />
            ) : null}
          </div>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">
                {selectedSafety ? `${selectedSafety.interface} checks` : 'Select an interface'}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {!selectedSafety ? (
                <p className="text-sm text-muted-foreground">
                  Click a row to inspect check results.
                </p>
              ) : (
                <>
                  <SafetyStatusBadge status={String(selectedSafety.status)} />
                  <div className="space-y-1.5">
                    {Object.entries(selectedSafety.checks || {}).map(([key, value]) => {
                      const hazardKeys = new Set([
                        'maintenanceMode',
                        'deviceLocked',
                        'interfaceLocked',
                        'mitigationRunning',
                      ])
                      const ok = hazardKeys.has(key) ? !value : Boolean(value)
                      return (
                        <div
                          key={key}
                          className="flex items-center justify-between rounded-md border border-border/50 px-2.5 py-1 text-sm"
                        >
                          <span className="text-muted-foreground">{key}</span>
                          <Badge variant={ok ? 'success' : 'danger'} className="capitalize">
                            {String(value)}
                          </Badge>
                        </div>
                      )
                    })}
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </Subsection>
  )
}
