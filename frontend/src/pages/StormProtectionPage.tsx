import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { RefreshCw, Shield } from 'lucide-react'
import {
  PortClassificationBadges,
} from '@/components/interfaces/InterfaceStatusBadge'
import { EmptyState } from '@/components/shared/EmptyState'
import { ErrorState } from '@/components/shared/ErrorState'
import { TableSkeleton } from '@/components/shared/LoadingState'
import { PageHeader } from '@/components/shared/PageHeader'
import { PaginationControls } from '@/components/shared/PaginationControls'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useAuth } from '@/auth/AuthContext'
import {
  useEligibilityMutations,
  useEligibilityQuery,
  useStormConfigQuery,
} from '@/hooks/queries'
import type { EligibilityResult, NetworkInterface } from '@/types'
import { formatDateTime, formatRelative } from '@/utils/format'

const DEFAULT_LIMIT = 25

function EligibilityBadge({ eligible }: { eligible: boolean }) {
  return (
    <Badge variant={eligible ? 'success' : 'danger'} className="font-semibold">
      <span
        className={`h-1.5 w-1.5 rounded-full ${eligible ? 'bg-success' : 'bg-danger'}`}
        aria-hidden
      />
      {eligible ? 'Eligible' : 'Not Eligible'}
    </Badge>
  )
}

function classificationIface(row: EligibilityResult): Pick<
  NetworkInterface,
  | 'portMode'
  | 'mode'
  | 'isAccess'
  | 'isTrunk'
  | 'isUplink'
  | 'isInfrastructure'
  | 'isManagement'
  | 'isProtected'
> {
  return {
    portMode: row.portMode || 'unknown',
    mode: row.portMode || 'unknown',
    isAccess: Boolean(row.isAccess),
    isTrunk: Boolean(row.isTrunk),
    isUplink: Boolean(row.isUplink),
    isInfrastructure: Boolean(row.isInfrastructure),
    isManagement: Boolean(row.isManagement),
    isProtected: Boolean(row.isProtected),
  }
}

export function StormProtectionPage() {
  const { isAdmin } = useAuth()
  const mutations = useEligibilityMutations()
  const stormConfig = useStormConfigQuery()

  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [eligibleFilter, setEligibleFilter] = useState('all')
  const [page, setPage] = useState(1)
  const [limit, setLimit] = useState(DEFAULT_LIMIT)

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 300)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => {
    setPage(1)
  }, [debouncedQuery, eligibleFilter, limit])

  const eligibilityQuery = useEligibilityQuery({
    page,
    limit,
    q: debouncedQuery,
    eligible:
      eligibleFilter === 'eligible'
        ? true
        : eligibleFilter === 'ineligible'
          ? false
          : undefined,
  })

  const rows = eligibilityQuery.data?.data ?? []
  const total = eligibilityQuery.data?.total ?? eligibilityQuery.data?.count ?? 0
  const totalPages = eligibilityQuery.data?.totalPages ?? 1

  const eligibleCount = useMemo(
    () => rows.filter((r) => r.eligible).length,
    [rows],
  )
  const ineligibleCount = rows.length - eligibleCount

  const isBusy = mutations.evaluateAll.isPending

  return (
    <div className="space-y-6">
      <PageHeader
        title="Storm Protection"
        description="Port eligibility decisions for automated storm analysis and mitigation"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {isAdmin ? (
              <Button
                type="button"
                disabled={isBusy || stormConfig.data?.enableEligibility === false}
                onClick={() => mutations.evaluateAll.mutate()}
              >
                <Shield className="mr-2 h-4 w-4" />
                {mutations.evaluateAll.isPending ? 'Evaluating…' : 'Evaluate all'}
              </Button>
            ) : null}
            <Button
              type="button"
              variant="secondary"
              onClick={() => void eligibilityQuery.refetch()}
            >
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
          </div>
        }
      />

      <section className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Port Eligibility</h2>
          <p className="text-sm text-muted-foreground">
            Deterministic gate that decides which access ports may enter future storm
            engines. No mitigation is performed here.
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Latest evaluations
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold">{total}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Eligible (page)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-success">{eligibleCount}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Not eligible (page)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-danger">{ineligibleCount}</p>
            </CardContent>
          </Card>
        </div>

        <div className="flex flex-wrap gap-3">
          <Input
            type="search"
            className="max-w-sm"
            placeholder="Search interface, host, reason, rule…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <Select value={eligibleFilter} onValueChange={setEligibleFilter}>
            <SelectTrigger className="w-[180px]">
              <SelectValue placeholder="Eligibility" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="eligible">Eligible</SelectItem>
              <SelectItem value="ineligible">Not eligible</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {eligibilityQuery.isLoading ? (
          <TableSkeleton rows={8} />
        ) : eligibilityQuery.isError ? (
          <ErrorState
            title="Unable to load eligibility results"
            message={
              eligibilityQuery.error instanceof Error
                ? eligibilityQuery.error.message
                : 'Unexpected error'
            }
            onRetry={() => void eligibilityQuery.refetch()}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={Shield}
            title="No eligibility results"
            description="Run interface discovery and stats collection, then evaluate ports. The scheduler also evaluates after each stats cycle."
          />
        ) : (
          <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Interface</TableHead>
                    <TableHead>Device</TableHead>
                    <TableHead>Eligibility</TableHead>
                    <TableHead>Reason</TableHead>
                    <TableHead>Failed rule</TableHead>
                    <TableHead>Confidence</TableHead>
                    <TableHead>Classification</TableHead>
                    <TableHead>Evaluated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
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
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">
                            {row.hostname || '—'}
                          </p>
                          <p className="truncate text-xs text-muted-foreground">
                            {row.ipAddress || row.deviceId}
                          </p>
                        </div>
                      </TableCell>
                      <TableCell>
                        <EligibilityBadge eligible={row.eligible} />
                      </TableCell>
                      <TableCell className="max-w-[200px] truncate text-sm">
                        {row.reason}
                      </TableCell>
                      <TableCell>
                        {row.failedRule ? (
                          <Badge variant="outline" className="font-mono text-xs">
                            {row.failedRule}
                          </Badge>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell>{row.confidence}%</TableCell>
                      <TableCell>
                        <div className="flex max-w-[280px] flex-wrap gap-1">
                          <PortClassificationBadges
                            iface={classificationIface(row)}
                            includeMode
                          />
                          {!row.isAccess &&
                          !row.isTrunk &&
                          !row.isUplink &&
                          !row.isInfrastructure &&
                          !row.isManagement &&
                          !row.isProtected ? (
                            <span className="text-xs text-muted-foreground">—</span>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                        <div title={formatDateTime(row.timestamp) || undefined}>
                          {formatRelative(row.timestamp) || '—'}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        )}

        {totalPages > 1 || total > limit ? (
          <PaginationControls
            page={page}
            totalPages={Math.max(totalPages, 1)}
            total={total}
            limit={limit}
            onPageChange={setPage}
            onLimitChange={setLimit}
          />
        ) : null}
      </section>
    </div>
  )
}
