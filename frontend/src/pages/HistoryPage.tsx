import { useEffect, useState } from 'react'
import { useHistoryQuery } from '@/hooks/queries'
import { formatDateTime, formatMs } from '@/utils/format'
import { EmptyState } from '@/components/shared/EmptyState'
import { ErrorState } from '@/components/shared/ErrorState'
import { TableSkeleton } from '@/components/shared/LoadingState'
import { PageHeader } from '@/components/shared/PageHeader'
import { PaginationControls } from '@/components/shared/PaginationControls'
import { StatusBadge } from '@/components/shared/StatusBadge'
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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

const DEFAULT_LIMIT = 25

export function HistoryPage() {
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [page, setPage] = useState(1)
  const [limit, setLimit] = useState(DEFAULT_LIMIT)

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 300)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => {
    setPage(1)
  }, [debouncedQuery, statusFilter, typeFilter, startDate, endDate, limit])

  const historyQuery = useHistoryQuery({
    page,
    limit,
    q: debouncedQuery,
    status: statusFilter,
    scanType: typeFilter,
    startDate: startDate || undefined,
    endDate: endDate || undefined,
  })

  const history = historyQuery.data?.data ?? []
  const total = historyQuery.data?.total ?? historyQuery.data?.count ?? 0
  const totalPages = historyQuery.data?.totalPages ?? 1

  return (
    <div className="space-y-6">
      <PageHeader
        title="History"
        description="Manual and automatic ping results"
        actions={
          <Button type="button" variant="secondary" onClick={() => void historyQuery.refetch()}>
            Refresh
          </Button>
        }
      />

      <div className="flex flex-wrap gap-3">
        <Input
          type="search"
          className="max-w-sm"
          placeholder="Search hostname or IP…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="w-[180px]">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="Online">Online</SelectItem>
            <SelectItem value="Not Reachable">Not Reachable</SelectItem>
            <SelectItem value="Offline (Critical)">Offline (Critical)</SelectItem>
            <SelectItem value="Unknown">Unknown</SelectItem>
          </SelectContent>
        </Select>
        <Select value={typeFilter} onValueChange={setTypeFilter}>
          <SelectTrigger className="w-[160px]">
            <SelectValue placeholder="Scan type" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All scan types</SelectItem>
            <SelectItem value="Manual">Manual</SelectItem>
            <SelectItem value="Automatic">Automatic</SelectItem>
          </SelectContent>
        </Select>
        <Input
          type="date"
          className="w-auto"
          value={startDate}
          onChange={(e) => setStartDate(e.target.value)}
          aria-label="Start date"
        />
        <Input
          type="date"
          className="w-auto"
          value={endDate}
          onChange={(e) => setEndDate(e.target.value)}
          aria-label="End date"
        />
      </div>

      {historyQuery.isLoading && history.length === 0 ? (
        <TableSkeleton />
      ) : historyQuery.error && history.length === 0 ? (
        <ErrorState
          message={historyQuery.error instanceof Error ? historyQuery.error.message : 'Failed to load'}
          onRetry={() => void historyQuery.refetch()}
        />
      ) : history.length === 0 ? (
        <EmptyState
          title={
            total === 0 && !debouncedQuery && statusFilter === 'all' && typeFilter === 'all'
              ? 'No ping history yet'
              : 'No matching records'
          }
          description={
            total === 0 && !debouncedQuery && statusFilter === 'all' && typeFilter === 'all'
              ? 'History appears after manual pings or automatic monitoring cycles.'
              : 'Adjust filters to see more results.'
          }
        />
      ) : (
        <Card className="glass overflow-hidden">
          <CardContent className="p-0">
            <Table>
              <TableHeader className="sticky top-0 z-10 bg-card/95 backdrop-blur">
                <TableRow>
                  <TableHead>Timestamp</TableHead>
                  <TableHead>Hostname</TableHead>
                  <TableHead>IP</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>RTT</TableHead>
                  <TableHead>Scan type</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {history.map((row) => (
                  <TableRow key={row._id}>
                    <TableCell className="text-muted-foreground">{formatDateTime(row.timestamp)}</TableCell>
                    <TableCell className="font-semibold">{row.hostname}</TableCell>
                    <TableCell className="mono text-muted-foreground">{row.ipAddress}</TableCell>
                    <TableCell>
                      <StatusBadge status={row.status} />
                    </TableCell>
                    <TableCell className="mono">{formatMs(row.responseTime)}</TableCell>
                    <TableCell>{row.scanType}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <div className="border-t border-border px-4 pb-2">
              <PaginationControls
                page={page}
                totalPages={totalPages}
                total={total}
                limit={limit}
                onPageChange={setPage}
                onLimitChange={setLimit}
              />
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
