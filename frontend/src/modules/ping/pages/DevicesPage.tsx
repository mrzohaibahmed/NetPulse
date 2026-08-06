import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table'
import {
  AlertTriangle,
  Filter,
  MoreHorizontal,
  Pencil,
  Plus,
  Radar,
  RefreshCw,
  Server,
  Timer,
  Trash2,
  Upload,
  Eye,
  ArrowUpDown,
  Wifi,
  WifiOff,
} from 'lucide-react'
import { useAuth } from '@/shared/auth/AuthContext'
import { useDashboardQuery, useDeviceMutations, useDevicesQuery } from '@/hooks/queries'
import { deviceTypeIcon } from '@/lib/device-icons'
import { formatMs, formatRelative } from '@/utils/format'
import type { Device } from '@/types'
import { DeviceDrawer } from '@/modules/ping/components/DeviceDrawer'
import { DeviceFormDialog } from '@/modules/ping/components/DeviceFormDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { KpiCard } from '@/shared/components/KpiCard'
import { TableSkeleton } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { StatusBadge } from '@/shared/components/StatusBadge'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/ui/alert-dialog'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/ui/card'
import { Checkbox } from '@/shared/ui/checkbox'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu'
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

export function DevicesPage() {
  const { isAdmin } = useAuth()
  const navigate = useNavigate()
  const { deviceId: routeDeviceId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const fileRef = useRef<HTMLInputElement>(null)

  const [query, setQuery] = useState(searchParams.get('q') || '')
  const [debouncedQuery, setDebouncedQuery] = useState(query)
  const [statusFilter, setStatusFilter] = useState('all')
  const [page, setPage] = useState(1)
  const [limit, setLimit] = useState(DEFAULT_LIMIT)
  const [sorting, setSorting] = useState<SortingState>([])
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Device | null>(null)
  const [drawerId, setDrawerId] = useState<string | null>(routeDeviceId ?? null)
  const [deleteTarget, setDeleteTarget] = useState<Device | null>(null)

  const { update, remove, scan, importCsv } = useDeviceMutations()
  const dash = useDashboardQuery()

  useEffect(() => {
    setDrawerId(routeDeviceId ?? null)
  }, [routeDeviceId])

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 300)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => {
    setPage(1)
  }, [debouncedQuery, statusFilter, limit])

  useEffect(() => {
    const q = searchParams.get('q')
    if (q != null && q !== query) setQuery(q)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  const devicesQuery = useDevicesQuery({
    page,
    limit,
    q: debouncedQuery,
    status: statusFilter,
  })

  const devices = devicesQuery.data?.data ?? []
  const total = devicesQuery.data?.total ?? devicesQuery.data?.count ?? 0
  const totalPages = devicesQuery.data?.totalPages ?? 1

  const offlineCount =
    (dash.summary?.notReachableDevices ?? 0) + (dash.summary?.criticalOfflineDevices ?? 0)
  const warningCount = dash.summary?.notReachableDevices ?? 0

  useEffect(() => {
    if ((devicesQuery.data?.totalPages ?? 0) > 0 && page > (devicesQuery.data?.totalPages ?? 1)) {
      setPage(devicesQuery.data?.totalPages ?? 1)
    }
  }, [devicesQuery.data?.totalPages, page])

  const openDrawer = (id: string) => {
    setDrawerId(id)
    navigate(`/devices/${id}`, { replace: true })
  }

  const closeDrawer = (open: boolean) => {
    if (!open) {
      setDrawerId(null)
      navigate('/devices', { replace: true })
    }
  }

  const columns = useMemo<ColumnDef<Device>[]>(
    () => [
      {
        accessorKey: 'hostname',
        header: ({ column }) => (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="-ml-3 h-8"
            onClick={() => column.toggleSorting(column.getIsSorted() === 'asc')}
          >
            Hostname
            <ArrowUpDown className="h-3.5 w-3.5" />
          </Button>
        ),
        cell: ({ row }) => {
          const Icon = deviceTypeIcon(row.original.deviceType)
          return (
            <button
              type="button"
              className="flex items-center gap-2 text-left font-semibold text-primary hover:underline"
              onClick={(e) => {
                e.stopPropagation()
                openDrawer(row.original._id)
              }}
            >
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <Icon className="h-4 w-4" />
              </span>
              {row.original.hostname}
            </button>
          )
        },
      },
      {
        accessorKey: 'ipAddress',
        header: 'IP',
        cell: ({ row }) => <span className="mono text-muted-foreground">{row.original.ipAddress}</span>,
      },
      {
        accessorKey: 'deviceType',
        header: 'Type',
      },
      {
        id: 'vendor',
        header: 'Vendor',
        cell: ({ row }) => (
          <span className="text-muted-foreground">{row.original.credentials?.sshVendor || '—'}</span>
        ),
      },
      {
        id: 'monitor',
        header: 'Monitor',
        cell: ({ row }) => (
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Checkbox
              checked={row.original.monitor}
              disabled={!isAdmin}
              onCheckedChange={() =>
                isAdmin &&
                update.mutate({ id: row.original._id, payload: { monitor: !row.original.monitor } })
              }
            />
            {row.original.monitor ? 'Enabled' : 'Disabled'}
          </label>
        ),
      },
      {
        accessorKey: 'status',
        header: 'Status',
        cell: ({ row }) => <StatusBadge status={row.original.status} />,
      },
      {
        accessorKey: 'responseTime',
        header: 'Latency',
        cell: ({ row }) => (
          <div className="flex items-center gap-2">
            <span className="mono text-sm">{formatMs(row.original.responseTime)}</span>
            <LatencyBar value={row.original.responseTime} />
          </div>
        ),
      },
      {
        id: 'flags',
        header: 'Flags',
        cell: ({ row }) => (
          <div className="flex flex-wrap items-center gap-2">
            {row.original.critical ? <Badge variant="danger">Critical</Badge> : null}
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Checkbox
                checked={row.original.critical}
                disabled={!isAdmin}
                onCheckedChange={() =>
                  isAdmin &&
                  update.mutate({ id: row.original._id, payload: { critical: !row.original.critical } })
                }
              />
              Critical
            </label>
          </div>
        ),
      },
      {
        accessorKey: 'lastSeen',
        header: 'Last seen',
        cell: ({ row }) => (
          <span className="text-muted-foreground">{formatRelative(row.original.lastSeen)}</span>
        ),
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button type="button" variant="ghost" size="icon" aria-label="Device actions">
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => openDrawer(row.original._id)}>
                <Eye className="h-4 w-4" />
                View details
              </DropdownMenuItem>
              <DropdownMenuItem disabled={scan.isPending} onClick={() => scan.mutate(row.original._id)}>
                <Radar className="h-4 w-4" />
                Ping
              </DropdownMenuItem>
              {isAdmin ? (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onClick={() => {
                      setEditing(row.original)
                      setFormOpen(true)
                    }}
                  >
                    <Pencil className="h-4 w-4" />
                    Edit
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    className="text-danger focus:text-danger"
                    onClick={() => setDeleteTarget(row.original)}
                  >
                    <Trash2 className="h-4 w-4" />
                    Delete
                  </DropdownMenuItem>
                </>
              ) : null}
            </DropdownMenuContent>
          </DropdownMenu>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isAdmin, scan.isPending],
  )

  const table = useReactTable({
    data: devices,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  const onSearchChange = (value: string) => {
    setQuery(value)
    const next = new URLSearchParams(searchParams)
    if (value.trim()) next.set('q', value.trim())
    else next.delete('q')
    setSearchParams(next, { replace: true })
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title="Ping Monitoring · Devices"
        description="Enterprise inventory of monitored hosts — reachability, latency, and critical flags at a glance."
        actions={
          isAdmin ? (
            <>
              <input
                ref={fileRef}
                type="file"
                accept=".csv,text/csv"
                hidden
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) importCsv.mutate(file)
                  if (fileRef.current) fileRef.current.value = ''
                }}
              />
              <Button
                type="button"
                variant="secondary"
                disabled={importCsv.isPending}
                onClick={() => fileRef.current?.click()}
              >
                <Upload className="h-4 w-4" />
                {importCsv.isPending ? 'Importing…' : 'Import CSV'}
              </Button>
              <Button
                type="button"
                onClick={() => {
                  setEditing(null)
                  setFormOpen(true)
                }}
              >
                <Plus className="h-4 w-4" />
                Add device
              </Button>
            </>
          ) : null
        }
      />

      {/* 1. Summary */}
      <section className="space-y-4" aria-label="Summary">
        <SectionHeading
          title="Summary"
          description="Live fleet posture from the shared dashboard cache."
        />
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <KpiCard
            label="Total Devices"
            value={dash.summary?.totalDevices ?? '—'}
            icon={Server}
            tone="accent"
          />
          <KpiCard
            label="Online"
            value={dash.summary?.onlineDevices ?? '—'}
            icon={Wifi}
            tone="success"
          />
          <KpiCard
            label="Offline"
            value={dash.summary ? offlineCount : '—'}
            icon={WifiOff}
            tone={offlineCount > 0 ? 'danger' : 'default'}
          />
          <KpiCard
            label="Warning"
            value={dash.summary ? warningCount : '—'}
            icon={AlertTriangle}
            tone={warningCount > 0 ? 'warning' : 'default'}
            hint="Not reachable"
          />
          <KpiCard
            label="Avg Response"
            value={formatMs(dash.statistics?.averageResponseTime)}
            icon={Timer}
            tone="accent"
          />
        </div>
      </section>

      {/* 2. Device Inventory */}
      <section className="space-y-4" aria-label="Device inventory">
        <SectionHeading
          title="Device Inventory"
          description="Search, filter, and manage monitored hosts."
        />

        {isAdmin ? (
          <p className="text-sm text-muted-foreground">
            CSV columns: hostname, ipAddress, deviceType, critical (optional), monitor (optional)
          </p>
        ) : null}

        <Card className="glass rounded-xl">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Filter className="h-4 w-4 text-primary" />
              Filters
            </CardTitle>
            <CardDescription>Narrow the inventory by hostname, IP, type, or status.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap items-end gap-3">
              <div className="min-w-[220px] flex-1 space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground" htmlFor="device-search">
                  Search
                </label>
                <Input
                  id="device-search"
                  type="search"
                  placeholder="Search hostname, IP, or type…"
                  value={query}
                  onChange={(e) => onSearchChange(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Status</label>
                <Select value={statusFilter} onValueChange={setStatusFilter}>
                  <SelectTrigger className="w-[200px]">
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
              <Button type="button" variant="secondary" onClick={() => void devicesQuery.refetch()}>
                <RefreshCw className="h-4 w-4" />
                Refresh
              </Button>
            </div>
          </CardContent>
        </Card>

        {devicesQuery.isLoading && devices.length === 0 ? (
          <TableSkeleton />
        ) : devicesQuery.error && devices.length === 0 ? (
          <ErrorState
            message={devicesQuery.error instanceof Error ? devicesQuery.error.message : 'Failed to load'}
            onRetry={() => void devicesQuery.refetch()}
          />
        ) : devices.length === 0 ? (
          <EmptyState
            title={total === 0 && !debouncedQuery && statusFilter === 'all' ? 'No devices yet' : 'No matches'}
            description={
              total === 0 && !debouncedQuery && statusFilter === 'all'
                ? 'Add a device manually, import CSV, or discover hosts on your network.'
                : 'Try a different search or status filter.'
            }
          />
        ) : (
          <Card className="glass overflow-hidden rounded-xl">
            <CardContent className="p-0">
              <div className="overflow-auto p-1 sm:p-2">
                <Table>
                  <TableHeader className="sticky top-0 z-10 bg-card/95 backdrop-blur">
                    {table.getHeaderGroups().map((headerGroup) => (
                      <TableRow key={headerGroup.id}>
                        {headerGroup.headers.map((header) => (
                          <TableHead key={header.id}>
                            {header.isPlaceholder
                              ? null
                              : flexRender(header.column.columnDef.header, header.getContext())}
                          </TableHead>
                        ))}
                      </TableRow>
                    ))}
                  </TableHeader>
                  <TableBody>
                    {table.getRowModel().rows.map((row) => (
                      <TableRow
                        key={row.id}
                        className="cursor-pointer"
                        onClick={() => openDrawer(row.original._id)}
                      >
                        {row.getVisibleCells().map((cell) => (
                          <TableCell
                            key={cell.id}
                            onClick={(e) => {
                              if (
                                cell.column.id === 'actions' ||
                                cell.column.id === 'flags' ||
                                cell.column.id === 'monitor'
                              ) {
                                e.stopPropagation()
                              }
                            }}
                          >
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <div className="border-t border-border px-4 pb-2 pt-1">
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

      <DeviceFormDialog open={formOpen} onOpenChange={setFormOpen} device={editing} />

      <DeviceDrawer deviceId={drawerId} open={Boolean(drawerId)} onOpenChange={closeDrawer} />

      <AlertDialog open={Boolean(deleteTarget)} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete device?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently remove {deleteTarget?.hostname} ({deleteTarget?.ipAddress}) from
              monitoring.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-danger text-white hover:bg-danger/90"
              onClick={() => {
                if (!deleteTarget) return
                remove.mutate(
                  { id: deleteTarget._id, hostname: deleteTarget.hostname },
                  {
                    onSuccess: () => {
                      if (devices.length === 1 && page > 1) setPage((p) => p - 1)
                      setDeleteTarget(null)
                    },
                  },
                )
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function SectionHeading({ title, description }: { title: string; description: string }) {
  return (
    <div>
      <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
      <p className="text-sm text-muted-foreground">{description}</p>
    </div>
  )
}

function LatencyBar({ value }: { value: number | null }) {
  if (value == null) return <span className="h-1.5 w-12 rounded-full bg-muted" />
  const pct = Math.max(8, Math.min(100, 100 - value / 2))
  const color = value < 50 ? 'bg-success' : value < 150 ? 'bg-warning' : 'bg-danger'
  return (
    <span className="hidden h-1.5 w-12 overflow-hidden rounded-full bg-muted sm:inline-flex">
      <span className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
    </span>
  )
}
