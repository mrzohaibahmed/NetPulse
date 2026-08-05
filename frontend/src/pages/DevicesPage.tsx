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
  MoreHorizontal,
  Pencil,
  Plus,
  Radar,
  Trash2,
  Upload,
  Eye,
  ArrowUpDown,
} from 'lucide-react'
import { useAuth } from '@/auth/AuthContext'
import { useDeviceMutations, useDevicesQuery } from '@/hooks/queries'
import { deviceTypeIcon } from '@/lib/device-icons'
import { formatMs, formatRelative } from '@/utils/format'
import type { Device } from '@/types'
import { DeviceDrawer } from '@/components/devices/DeviceDrawer'
import { DeviceFormDialog } from '@/components/devices/DeviceFormDialog'
import { EmptyState } from '@/components/shared/EmptyState'
import { ErrorState } from '@/components/shared/ErrorState'
import { TableSkeleton } from '@/components/shared/LoadingState'
import { PageHeader } from '@/components/shared/PageHeader'
import { PaginationControls } from '@/components/shared/PaginationControls'
import { StatusBadge } from '@/components/shared/StatusBadge'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
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
    <div className="space-y-6">
      <PageHeader
        title="Devices"
        description="Manage monitored hosts and run manual pings"
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

      {isAdmin ? (
        <p className="text-sm text-muted-foreground">
          CSV columns: hostname, ipAddress, deviceType, critical (optional), monitor (optional)
        </p>
      ) : null}

      <div className="flex flex-wrap gap-3">
        <Input
          type="search"
          className="max-w-sm"
          placeholder="Search hostname, IP, or type…"
          value={query}
          onChange={(e) => onSearchChange(e.target.value)}
        />
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
        <Button type="button" variant="secondary" onClick={() => void devicesQuery.refetch()}>
          Refresh
        </Button>
      </div>

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
        <Card className="glass overflow-hidden">
          <CardContent className="p-0">
            <div className="overflow-auto">
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
                            if (cell.column.id === 'actions' || cell.column.id === 'flags' || cell.column.id === 'monitor') {
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
