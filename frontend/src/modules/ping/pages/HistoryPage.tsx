import { useEffect, useState } from 'react'
import { Clock, Filter, History, RefreshCw, Search } from 'lucide-react'
import { useHistoryQuery } from '@/hooks/queries'
import { statusTone } from '@/lib/status'
import { cn } from '@/lib/utils'
import { formatDateTime, formatMs } from '@/utils/format'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { TableSkeleton } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { SectionHeading } from '@/shared/components/SectionHeading'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/ui/card'
import { Input } from '@/shared/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'

const DEFAULT_LIMIT = 25

function statusBorderClass(status: string | null | undefined): string {
  const tone = statusTone(status || 'Unknown')
  if (tone === 'online') return 'border-l-success'
  if (tone === 'warn') return 'border-l-warning'
  if (tone === 'offline') return 'border-l-danger'
  return 'border-l-slate-400'
}

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

  const isEmptyFleet =
    total === 0 && !debouncedQuery && statusFilter === 'all' && typeFilter === 'all'

  return (
    <div className="np-page min-w-0 max-w-full">
      <PageHeader
        title="Ping Monitoring · History"
        description="Manual and automatic ping results across the monitored fleet."
        actions={
          <Button type="button" variant="secondary" onClick={() => void historyQuery.refetch()}>
            <RefreshCw className="h-4 w-4" />
            Refresh
          </Button>
        }
      />

      {/* Sticky filter bar */}
      <div className="sticky top-16 z-20 -mx-1 px-1">
        <Card className="glass rounded-xl border-border/80 shadow-lg shadow-black/5 backdrop-blur-md">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Filter className="h-4 w-4 text-primary" />
              Filters
            </CardTitle>
            <CardDescription>Search and narrow the ping timeline.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap items-end gap-3">
              <div className="relative min-w-[220px] flex-1 space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground" htmlFor="history-search">
                  Search
                </label>
                <div className="relative">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    id="history-search"
                    type="search"
                    className="pl-9"
                    placeholder="Search hostname or IP…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </div>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Status</label>
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
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Scan type</label>
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
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">From</label>
                <Input
                  type="date"
                  className="w-auto"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                  aria-label="Start date"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">To</label>
                <Input
                  type="date"
                  className="w-auto"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                  aria-label="End date"
                />
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <section className="space-y-4" aria-label="Ping timeline">
        <SectionHeading
          title="Ping Timeline"
          description="Chronological reachability events with status accents."
        />

        {historyQuery.isLoading && history.length === 0 ? (
          <Card className="glass rounded-xl">
            <CardContent className="py-6">
              <TableSkeleton />
            </CardContent>
          </Card>
        ) : historyQuery.error && history.length === 0 ? (
          <ErrorState
            message={
              historyQuery.error instanceof Error ? historyQuery.error.message : 'Failed to load'
            }
            onRetry={() => void historyQuery.refetch()}
          />
        ) : history.length === 0 ? (
          <Card className="glass rounded-xl">
            <CardContent className="flex flex-col items-center py-14 text-center">
              <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                <History className="h-7 w-7" />
              </div>
              <EmptyState
                title={isEmptyFleet ? 'No ping history yet' : 'No matching records'}
                description={
                  isEmptyFleet
                    ? 'History appears after manual pings or automatic monitoring cycles.'
                    : 'Adjust filters to see more results.'
                }
                className="py-0"
              />
            </CardContent>
          </Card>
        ) : (
          <Card className="glass overflow-hidden rounded-xl">
            <CardContent className="p-0">
              <div className="w-full max-w-full overflow-x-auto">
                <Table className="min-w-[720px]">
                  <TableHeader className="sticky top-0 z-10 bg-card/95 backdrop-blur">
                    <TableRow>
                      <TableHead className="pl-5">
                        <span className="inline-flex items-center gap-1.5">
                          <Clock className="h-3.5 w-3.5 text-muted-foreground" />
                          Timestamp
                        </span>
                      </TableHead>
                      <TableHead>Hostname</TableHead>
                      <TableHead>IP</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>RTT</TableHead>
                      <TableHead>Scan type</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {history.map((row) => (
                      <TableRow
                        key={row._id}
                        className={cn(
                          'border-l-[3px] transition-colors hover:bg-muted/30',
                          statusBorderClass(row.status),
                        )}
                      >
                        <TableCell className="pl-5 text-muted-foreground">
                          {formatDateTime(row.timestamp)}
                        </TableCell>
                        <TableCell className="font-semibold tracking-tight">{row.hostname}</TableCell>
                        <TableCell className="mono text-muted-foreground">{row.ipAddress}</TableCell>
                        <TableCell>
                          <StatusBadge status={row.status} />
                        </TableCell>
                        <TableCell className="mono font-medium">{formatMs(row.responseTime)}</TableCell>
                        <TableCell className="text-muted-foreground">{row.scanType}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
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
      </section>
    </div>
  )
}
