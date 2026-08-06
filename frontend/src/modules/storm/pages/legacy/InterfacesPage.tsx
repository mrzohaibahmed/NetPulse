import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQueries } from '@tanstack/react-query'
import { Network, RefreshCw, Radar } from 'lucide-react'
import { getDeviceInterfaceStats } from '@/api'
import { useAuth } from '@/shared/auth/AuthContext'
import {
  InterfaceStatusBadge,
  PortClassificationBadges,
  PortModeBadge,
  UtilizationBar,
  formatAllowedVlans,
  neighborRemotePort,
} from '@/modules/storm/components/InterfaceStatusBadge'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { TableSkeleton } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { Button } from '@/shared/ui/button'
import { Card, CardContent } from '@/shared/ui/card'
import { Input } from '@/shared/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import { queryKeys } from '@/hooks/queryKeys'
import { useInterfaceMutations, useInterfacesQuery } from '@/hooks/queries'
import type { InterfaceListRow, InterfaceStat } from '@/types'
import {
  formatDateTime,
  formatRelative,
} from '@/utils/format'
import { interfaceNamesMatch } from '@/utils/interfaceNames'

const DEFAULT_LIMIT = 25

function mergeInterfaceRows(
  interfaces: InterfaceListRow[],
  statsByDevice: Map<string, InterfaceStat[]>,
): InterfaceListRow[] {
  return interfaces.map((iface) => {
    const stats = statsByDevice.get(iface.deviceId) ?? []
    const match =
      stats.find((s) => s.interfaceName === iface.name) ||
      stats.find((s) => interfaceNamesMatch(s.interfaceName, iface.name))

    if (!match) return iface

    return {
      ...iface,
      utilization: match.utilization,
      rxUtilization: match.rxUtilization,
      txUtilization: match.txUtilization,
      broadcastPackets: match.broadcastPackets,
      multicastPackets: match.multicastPackets,
      inputErrors: match.inputErrors,
      outputErrors: match.outputErrors,
      discards: match.discards,
      statsTimestamp: match.timestamp,
      speedBps: match.speedBps,
    }
  })
}

export function InterfacesPage() {
  const navigate = useNavigate()
  const { isAdmin } = useAuth()
  const mutations = useInterfaceMutations()

  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [operFilter, setOperFilter] = useState('all')
  const [adminFilter, setAdminFilter] = useState('all')
  const [modeFilter, setModeFilter] = useState('all')
  const [page, setPage] = useState(1)
  const [limit, setLimit] = useState(DEFAULT_LIMIT)

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 300)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => {
    setPage(1)
  }, [debouncedQuery, operFilter, adminFilter, modeFilter, limit])

  const interfacesQuery = useInterfacesQuery({
    page,
    limit,
    q: debouncedQuery,
    operStatus: operFilter,
    adminStatus: adminFilter,
    mode: modeFilter,
  })

  const interfaces = interfacesQuery.data?.data ?? []
  const total = interfacesQuery.data?.total ?? interfacesQuery.data?.count ?? 0
  const totalPages = interfacesQuery.data?.totalPages ?? 1

  const deviceIds = useMemo(
    () => [...new Set(interfaces.map((row) => row.deviceId).filter(Boolean))],
    [interfaces],
  )

  const statsQueries = useQueries({
    queries: deviceIds.map((deviceId) => ({
      queryKey: queryKeys.interfaceStats(deviceId),
      queryFn: async () => (await getDeviceInterfaceStats(deviceId)).data,
      staleTime: 15_000,
      refetchInterval: 20_000,
    })),
  })

  const statsByDevice = useMemo(() => {
    const map = new Map<string, InterfaceStat[]>()
    deviceIds.forEach((deviceId, index) => {
      const data = statsQueries[index]?.data
      if (data) map.set(deviceId, data)
    })
    return map
  }, [deviceIds, statsQueries])

  const rows = useMemo(
    () => mergeInterfaceRows(interfaces, statsByDevice),
    [interfaces, statsByDevice],
  )

  const isBusy =
    mutations.discoverAll.isPending || mutations.collectAll.isPending

  const hasActiveFilters =
    Boolean(debouncedQuery) ||
    operFilter !== 'all' ||
    adminFilter !== 'all' ||
    modeFilter !== 'all'

  return (
    <div className="space-y-6">
      <PageHeader
        title="Interfaces"
        description="Switch port inventory, utilization, and error counters"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {isAdmin ? (
              <>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={isBusy}
                  onClick={() => mutations.discoverAll.mutate()}
                >
                  <Radar className="mr-2 h-4 w-4" />
                  {mutations.discoverAll.isPending ? 'Discovering…' : 'Discover all'}
                </Button>
                <Button
                  type="button"
                  disabled={isBusy}
                  onClick={() => mutations.collectAll.mutate()}
                >
                  <RefreshCw className="mr-2 h-4 w-4" />
                  {mutations.collectAll.isPending ? 'Collecting…' : 'Collect stats'}
                </Button>
              </>
            ) : null}
            <Button
              type="button"
              variant="secondary"
              onClick={() => void interfacesQuery.refetch()}
            >
              Refresh
            </Button>
          </div>
        }
      />

      <div className="flex flex-wrap gap-3">
        <Input
          type="search"
          className="max-w-sm"
          placeholder="Search interface, host, IP, VLAN…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Select value={operFilter} onValueChange={setOperFilter}>
          <SelectTrigger className="w-[160px]">
            <SelectValue placeholder="Oper status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All oper</SelectItem>
            <SelectItem value="up">Up</SelectItem>
            <SelectItem value="down">Down</SelectItem>
            <SelectItem value="unknown">Unknown</SelectItem>
          </SelectContent>
        </Select>
        <Select value={adminFilter} onValueChange={setAdminFilter}>
          <SelectTrigger className="w-[160px]">
            <SelectValue placeholder="Admin status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All admin</SelectItem>
            <SelectItem value="up">Admin up</SelectItem>
            <SelectItem value="down">Admin down</SelectItem>
            <SelectItem value="unknown">Unknown</SelectItem>
          </SelectContent>
        </Select>
        <Select value={modeFilter} onValueChange={setModeFilter}>
          <SelectTrigger className="w-[150px]">
            <SelectValue placeholder="Mode" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All modes</SelectItem>
            <SelectItem value="access">Access</SelectItem>
            <SelectItem value="trunk">Trunk</SelectItem>
            <SelectItem value="routed">Routed</SelectItem>
            <SelectItem value="unknown">Unknown</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {interfacesQuery.isLoading && rows.length === 0 ? (
        <TableSkeleton />
      ) : interfacesQuery.error && rows.length === 0 ? (
        <ErrorState
          message={
            interfacesQuery.error instanceof Error
              ? interfacesQuery.error.message
              : 'Failed to load interfaces'
          }
          onRetry={() => void interfacesQuery.refetch()}
        />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={Network}
          title={hasActiveFilters ? 'No matching interfaces' : 'No interfaces discovered yet'}
          description={
            hasActiveFilters
              ? 'Adjust filters to see more results.'
              : isAdmin
                ? 'Run Discover all on online switches with SSH credentials configured.'
                : 'Ask an administrator to discover interfaces on managed switches.'
          }
          actionLabel={isAdmin && !hasActiveFilters ? 'Discover all' : undefined}
          onAction={
            isAdmin && !hasActiveFilters
              ? () => mutations.discoverAll.mutate()
              : undefined
          }
        />
      ) : (
        <Card className="glass overflow-hidden">
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <Table>
                <TableHeader className="sticky top-0 z-10 bg-card/95 backdrop-blur">
                  <TableRow>
                    <TableHead>Interface</TableHead>
                    <TableHead className="hidden lg:table-cell">Device</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Port mode</TableHead>
                    <TableHead className="hidden xl:table-cell">Classification</TableHead>
                    <TableHead className="hidden md:table-cell">Access VLAN</TableHead>
                    <TableHead className="hidden md:table-cell">Voice VLAN</TableHead>
                    <TableHead className="hidden md:table-cell">Native VLAN</TableHead>
                    <TableHead className="hidden xl:table-cell">Allowed VLANs</TableHead>
                    <TableHead className="hidden xl:table-cell">Speed</TableHead>
                    <TableHead className="hidden xl:table-cell">Duplex</TableHead>
                    <TableHead className="hidden lg:table-cell">Neighbor</TableHead>
                    <TableHead>Utilization</TableHead>
                    <TableHead className="hidden sm:table-cell">Updated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => {
                    const detailPath = `/interfaces/${row.deviceId}/${encodeURIComponent(row.name)}`
                    const portMode = row.portMode || row.mode || 'unknown'
                    const remotePort = neighborRemotePort(row.neighbor)
                    const neighborLabel = row.neighbor?.hostname
                      ? `${row.neighbor.hostname}${remotePort ? ` (${remotePort})` : ''}${
                          row.neighbor.deviceType && row.neighbor.deviceType !== 'Unknown'
                            ? ` · ${row.neighbor.deviceType}`
                            : ''
                        }`
                      : '—'

                    return (
                      <TableRow
                        key={row._id}
                        className="cursor-pointer"
                        onClick={() => navigate(detailPath)}
                      >
                        <TableCell>
                          <Link
                            to={detailPath}
                            className="font-semibold text-primary hover:underline"
                            onClick={(e) => e.stopPropagation()}
                          >
                            {row.name}
                          </Link>
                          {row.description ? (
                            <p className="mt-0.5 max-w-[220px] truncate text-xs text-muted-foreground">
                              {row.description}
                            </p>
                          ) : null}
                          <p className="mt-0.5 text-xs text-muted-foreground lg:hidden">
                            {row.hostname || row.ipAddress || '—'}
                          </p>
                        </TableCell>
                        <TableCell className="hidden lg:table-cell">
                          <div className="font-medium">{row.hostname || '—'}</div>
                          <div className="mono text-xs text-muted-foreground">
                            {row.ipAddress || '—'}
                          </div>
                        </TableCell>
                        <TableCell>
                          <InterfaceStatusBadge status={row.operStatus} />
                        </TableCell>
                        <TableCell>
                          <PortModeBadge mode={portMode} />
                        </TableCell>
                        <TableCell className="hidden xl:table-cell">
                          <PortClassificationBadges iface={row} />
                        </TableCell>
                        <TableCell className="hidden mono text-muted-foreground md:table-cell">
                          {row.accessVlan ?? '—'}
                        </TableCell>
                        <TableCell className="hidden mono text-muted-foreground md:table-cell">
                          {row.voiceVlan ?? '—'}
                        </TableCell>
                        <TableCell className="hidden mono text-muted-foreground md:table-cell">
                          {row.nativeVlan ?? '—'}
                        </TableCell>
                        <TableCell className="hidden mono text-xs text-muted-foreground xl:table-cell">
                          {formatAllowedVlans(row.allowedVlans)}
                        </TableCell>
                        <TableCell className="hidden mono text-muted-foreground xl:table-cell">
                          {row.speedMbps != null ? row.speedMbps : row.speed || '—'}
                        </TableCell>
                        <TableCell className="hidden capitalize text-muted-foreground xl:table-cell">
                          {row.duplex || '—'}
                        </TableCell>
                        <TableCell className="hidden max-w-[200px] truncate text-sm text-muted-foreground lg:table-cell">
                          {neighborLabel}
                        </TableCell>
                        <TableCell>
                          <UtilizationBar value={row.utilization} />
                        </TableCell>
                        <TableCell className="hidden text-muted-foreground sm:table-cell">
                          <div className="text-xs">
                            {formatRelative(row.statsTimestamp || row.lastUpdated)}
                          </div>
                          <div className="text-[10px] text-muted-foreground/80">
                            {formatDateTime(row.statsTimestamp || row.lastUpdated)}
                          </div>
                        </TableCell>
                      </TableRow>
                    )
                  })}
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
    </div>
  )
}
