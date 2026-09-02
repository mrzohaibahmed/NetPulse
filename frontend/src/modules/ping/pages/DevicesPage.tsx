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
import {
  useDashboardQuery,
  useDeviceMutations,
  useDevicesQuery,
  useDeviceNetworksQuery,
  useNmapScanAllMutation,
  usePingAllDevicesMutation,
} from '@/hooks/queries'
import { deviceTypeIcon } from '@/lib/device-icons'
import { displayDeviceType, DEVICE_TYPES } from '@/modules/ping/constants/devices'
import { SITE_LOCATIONS } from '@/modules/ping/constants/locations'
import { formatMs, formatRelative } from '@/utils/format'
import type { Device } from '@/types'
import { getDevices, updateDevice } from '@/api'
import { useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/hooks/queryKeys'
import { fetchAllListPages } from '@/utils/fetchAllPages'
import { runWithConcurrency } from '@/utils/runWithConcurrency'
import { toast } from 'sonner'
import { DeviceDrawer } from '@/modules/ping/components/DeviceDrawer'
import { DeviceFormDialog } from '@/modules/ping/components/DeviceFormDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { KpiCard } from '@/shared/components/KpiCard'
import { TableSkeleton } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { SectionHeading } from '@/shared/components/SectionHeading'
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
const BULK_MONITOR_CONCURRENCY = 5

export function DevicesPage() {
  const { isAdmin, isUser } = useAuth()
  const navigate = useNavigate()
  const { deviceId: routeDeviceId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const fileRef = useRef<HTMLInputElement>(null)

  const [query, setQuery] = useState(searchParams.get('q') || '')
  const [debouncedQuery, setDebouncedQuery] = useState(query)
  const [statusFilter, setStatusFilter] = useState(() => {
    const status = searchParams.get('status')
    return status && status.trim() ? status : 'all'
  })
  const [typeFilter, setTypeFilter] = useState(() => {
    const type = searchParams.get('type')
    return type && type.trim() ? type : 'all'
  })
  const [criticalFilter, setCriticalFilter] = useState(() => {
    const critical = searchParams.get('critical')
    if (critical === 'true') return 'critical'
    if (critical === 'false') return 'non-critical'
    return 'all'
  })
  const [networkFilter, setNetworkFilter] = useState(() => {
    const network = searchParams.get('network')
    return network && network.trim() ? network : 'all'
  })
  const [locationFilter, setLocationFilter] = useState(() => {
    const location = searchParams.get('location')
    return location && location.trim() ? location : 'all'
  })
  const [page, setPage] = useState(1)
  const [limit, setLimit] = useState(DEFAULT_LIMIT)
  const [sorting, setSorting] = useState<SortingState>([])
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Device | null>(null)
  const [drawerId, setDrawerId] = useState<string | null>(routeDeviceId ?? null)
  const [deleteTarget, setDeleteTarget] = useState<Device | null>(null)
  const [nmapAllConfirmOpen, setNmapAllConfirmOpen] = useState(false)
  const [pingAllConfirmOpen, setPingAllConfirmOpen] = useState(false)
  const [isBulkUpdating, setIsBulkUpdating] = useState(false)

  const { update, remove, scan, importCsv } = useDeviceMutations()
  const nmapScanAll = useNmapScanAllMutation()
  const pingAll = usePingAllDevicesMutation()
  const dash = useDashboardQuery()
  const queryClient = useQueryClient()

  const handleBulkMonitoringUpdate = async (isCritical: boolean | null, enableMonitoring: boolean) => {
    setIsBulkUpdating(true)
    try {
      const { data: allDevices } = await fetchAllListPages((page, limit) => getDevices({ page, limit }))
      const toUpdate = allDevices.filter(
        (d: Device) =>
          (isCritical === null || d.critical === isCritical) && d.monitor !== enableMonitoring,
      )

      if (toUpdate.length === 0) {
        const targetStr = isCritical === null ? 'all' : isCritical ? 'critical' : 'non-critical'
        toast.info(`All ${targetStr} devices are already ${enableMonitoring ? 'monitored' : 'unmonitored'}.`)
        return
      }

      let failed = 0
      await runWithConcurrency(toUpdate, BULK_MONITOR_CONCURRENCY, async (device) => {
        try {
          await updateDevice(device._id, { monitor: enableMonitoring })
        } catch {
          failed += 1
        }
      })

      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['devices'] }),
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.all }),
      ])

      const targetStr = isCritical === null ? 'all' : isCritical ? 'critical' : 'non-critical'
      const succeeded = toUpdate.length - failed
      if (failed > 0) {
        toast.warning(
          `${enableMonitoring ? 'Enabled' : 'Disabled'} monitoring for ${succeeded}/${toUpdate.length} ${targetStr} device(s).`,
        )
      } else {
        toast.success(
          `${enableMonitoring ? 'Enabled' : 'Disabled'} monitoring for ${succeeded} ${targetStr} device(s).`,
        )
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Bulk update failed')
    } finally {
      setIsBulkUpdating(false)
    }
  }

  useEffect(() => {
    setDrawerId(routeDeviceId ?? null)
  }, [routeDeviceId])

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 300)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => {
    setPage(1)
  }, [debouncedQuery, statusFilter, typeFilter, criticalFilter, networkFilter, locationFilter, limit])

  useEffect(() => {
    const q = searchParams.get('q')
    if (q != null && q !== query) setQuery(q)
    const status = searchParams.get('status')
    if (status != null && status !== statusFilter) setStatusFilter(status || 'all')
    const type = searchParams.get('type')
    if (type != null && type !== typeFilter) setTypeFilter(type || 'all')
    const critical = searchParams.get('critical')
    const nextCritical =
      critical === 'true' ? 'critical' : critical === 'false' ? 'non-critical' : 'all'
    if (critical != null && nextCritical !== criticalFilter) setCriticalFilter(nextCritical)
    const network = searchParams.get('network')
    if (network != null && network !== networkFilter) setNetworkFilter(network || 'all')
    const location = searchParams.get('location')
    if (location != null && location !== locationFilter) setLocationFilter(location || 'all')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  useEffect(() => {
    if (!searchParams.get('status')) return
    const timer = window.setTimeout(() => {
      document.getElementById('inventory-section')?.scrollIntoView({ behavior: 'smooth' })
    }, 80)
    return () => window.clearTimeout(timer)
  }, [searchParams])

  const devicesQuery = useDevicesQuery({
    page,
    limit,
    q: debouncedQuery,
    status: statusFilter !== 'all' ? statusFilter : undefined,
    deviceType: typeFilter !== 'all' ? typeFilter : undefined,
    critical:
      criticalFilter === 'critical' ? true : criticalFilter === 'non-critical' ? false : undefined,
    network: networkFilter !== 'all' ? networkFilter : undefined,
    location: locationFilter !== 'all' ? locationFilter : undefined,
  })

  const networksQuery = useDeviceNetworksQuery()
  const networks = networksQuery.data || []

  const devices = devicesQuery.data?.data ?? []
  const total = devicesQuery.data?.total ?? devicesQuery.data?.count ?? 0
  const totalPages = devicesQuery.data?.totalPages ?? 1

  const criticalOfflineCount = dash.summary?.criticalOfflineDevices ?? 0
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

  const handleCardClick = (status: string) => {
    setStatusFilter(status)
    const next = new URLSearchParams(searchParams)
    if (status === 'all') next.delete('status')
    else next.set('status', status)
    setSearchParams(next, { replace: true })
    document.getElementById('inventory-section')?.scrollIntoView({ behavior: 'smooth' })
  }

  const handleCriticalFilterChange = (val: string) => {
    setCriticalFilter(val)
    const next = new URLSearchParams(searchParams)
    if (val === 'critical') next.set('critical', 'true')
    else if (val === 'non-critical') next.set('critical', 'false')
    else next.delete('critical')
    setSearchParams(next, { replace: true })
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
              className="flex min-w-0 max-w-[14rem] items-center gap-2 text-left font-semibold text-primary hover:underline sm:max-w-[18rem]"
              title={row.original.hostname}
              onClick={(e) => {
                e.stopPropagation()
                openDrawer(row.original._id)
              }}
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <Icon className="h-4 w-4" />
              </span>
              <span className="truncate">{row.original.hostname}</span>
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
        cell: ({ row }) =>
          displayDeviceType(row.original.deviceType, row.original.classificationConfidence),
      },
      {
        id: 'vendor',
        header: 'Vendor',
        cell: ({ row }) => (
          <span className="text-muted-foreground">
            {row.original.vendor || row.original.credentials?.sshVendor || '—'}
          </span>
        ),
      },
      {
        id: 'operatingSystem',
        header: 'OS',
        cell: ({ row }) => (
          <span className="text-muted-foreground">{row.original.operatingSystem || '—'}</span>
        ),
      },
      {
        id: 'confidence',
        header: 'Confidence',
        cell: ({ row }) => (
          <span className="mono text-muted-foreground">
            {row.original.classificationConfidence != null
              ? `${row.original.classificationConfidence}%`
              : '—'}
          </span>
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
    <div className="np-page">
      <PageHeader
        title="Ping Monitoring · Devices"
        description="Enterprise inventory of monitored hosts — reachability, latency, and critical flags at a glance."
        actions={
          <>
            {isUser ? (
              <>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={pingAll.isPending || nmapScanAll.isPending}
                  onClick={() => setPingAllConfirmOpen(true)}
                >
                  <Wifi className={`h-4 w-4 ${pingAll.isPending ? 'animate-pulse' : ''}`} />
                  {pingAll.isPending ? 'Pinging all…' : 'Ping all'}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={nmapScanAll.isPending || pingAll.isPending}
                  onClick={() => setNmapAllConfirmOpen(true)}
                >
                  <Radar className={`h-4 w-4 ${nmapScanAll.isPending ? 'animate-pulse' : ''}`} />
                  {nmapScanAll.isPending ? 'Nmap scanning…' : 'Nmap scan all'}
                </Button>
              </>
            ) : null}
            {isAdmin ? (
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
            ) : null}
          </>
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
            onClick={() => handleCardClick('all')}
          />
          <KpiCard
            label="Online"
            value={dash.summary?.onlineDevices ?? '—'}
            icon={Wifi}
            tone="success"
            onClick={() => handleCardClick('Online')}
          />
          <KpiCard
            label="Critical Offline"
            value={dash.summary ? criticalOfflineCount : '—'}
            icon={WifiOff}
            tone={criticalOfflineCount > 0 ? 'danger' : 'default'}
            hint="Critical devices"
            onClick={() => handleCardClick('Offline (Critical)')}
          />
          <KpiCard
            label="Warning"
            value={dash.summary ? warningCount : '—'}
            icon={AlertTriangle}
            tone={warningCount > 0 ? 'warning' : 'default'}
            hint="Not reachable"
            onClick={() => handleCardClick('Not Reachable')}
          />
          <KpiCard
            label="Avg Response"
            value={formatMs(dash.statistics?.averageResponseTime)}
            icon={Timer}
            tone="accent"
            onClick={() => handleCardClick('all')}
          />
        </div>
      </section>

      {/* 2. Device Inventory */}
      <section id="inventory-section" className="min-w-0 max-w-full space-y-4 scroll-mt-24" aria-label="Device inventory">
        <SectionHeading
          title="Device Inventory"
          description="Search, filter, and manage monitored hosts."
        />

        {isAdmin ? (
          <p className="text-sm text-muted-foreground">
            CSV columns: hostname, ipAddress, deviceType, critical (optional), monitor (optional)
          </p>
        ) : null}

        <Card variant="section" className="glass rounded-xl">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Filter className="h-4 w-4 text-primary" />
              Filters
            </CardTitle>
            <CardDescription>
              Narrow the inventory by hostname, IP, type, status, location, or critical flag.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap items-end gap-3">
              <div className="w-[160px] space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground" htmlFor="device-search">
                  Search
                </label>
                <Input
                  id="device-search"
                  type="search"
                  placeholder="Hostname or IP…"
                  value={query}
                  onChange={(e) => onSearchChange(e.target.value)}
                  className="h-9"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Status</label>
                <Select
                  value={statusFilter}
                  onValueChange={(val) => {
                    setStatusFilter(val)
                    const next = new URLSearchParams(searchParams)
                    if (val === 'all') next.delete('status')
                    else next.set('status', val)
                    setSearchParams(next, { replace: true })
                  }}
                >
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
                <label className="text-xs font-medium text-muted-foreground">Type</label>
                <Select
                  value={typeFilter}
                  onValueChange={(val) => {
                    setTypeFilter(val)
                    const next = new URLSearchParams(searchParams)
                    if (val === 'all') next.delete('type')
                    else next.set('type', val)
                    setSearchParams(next, { replace: true })
                  }}
                >
                  <SelectTrigger className="w-[180px]">
                    <SelectValue placeholder="Type" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All types</SelectItem>
                    {DEVICE_TYPES.map((type) => (
                      <SelectItem key={type} value={type}>
                        {type}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Critical</label>
                <Select value={criticalFilter} onValueChange={handleCriticalFilterChange}>
                  <SelectTrigger className="w-[180px]">
                    <SelectValue placeholder="Critical" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All devices</SelectItem>
                    <SelectItem value="critical">Critical only</SelectItem>
                    <SelectItem value="non-critical">Non-critical only</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Location</label>
                <Select
                  value={locationFilter}
                  onValueChange={(val) => {
                    setLocationFilter(val)
                    const next = new URLSearchParams(searchParams)
                    if (val === 'all') next.delete('location')
                    else next.set('location', val)
                    setSearchParams(next, { replace: true })
                  }}
                >
                  <SelectTrigger className="h-9 w-[140px]">
                    <SelectValue placeholder="Location" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All locations</SelectItem>
                    {SITE_LOCATIONS.map((location) => (
                      <SelectItem key={location} value={location}>
                        {location}
                      </SelectItem>
                    ))}
                    <SelectItem value="__none__">Not set</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">Network</label>
                <Select
                  value={networkFilter}
                  onValueChange={(val) => {
                    setNetworkFilter(val)
                    const next = new URLSearchParams(searchParams)
                    if (val === 'all') next.delete('network')
                    else next.set('network', val)
                    setSearchParams(next, { replace: true })
                  }}
                >
                  <SelectTrigger className="w-[180px]">
                    <SelectValue placeholder="Network" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All networks</SelectItem>
                    {networks.map((net) => (
                      <SelectItem key={net} value={net}>
                        {net}.x
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button type="button" variant="secondary" onClick={() => void devicesQuery.refetch()}>
                <RefreshCw className="h-4 w-4" />
                Refresh
              </Button>
              {isAdmin ? (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button type="button" variant="outline" disabled={isBulkUpdating}>
                      {isBulkUpdating ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Filter className="h-4 w-4" />}
                      Manage Monitoring
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onClick={() => { void handleBulkMonitoringUpdate(null, true) }} disabled={isBulkUpdating}>
                      Enable Monitoring (All)
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={() => { void handleBulkMonitoringUpdate(true, true) }} disabled={isBulkUpdating}>
                      Enable Monitoring (Critical)
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => { void handleBulkMonitoringUpdate(true, false) }} disabled={isBulkUpdating}>
                      Disable Monitoring (Critical)
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => { void handleBulkMonitoringUpdate(false, true) }} disabled={isBulkUpdating}>
                      Enable Monitoring (Non-Critical)
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => { void handleBulkMonitoringUpdate(false, false) }} disabled={isBulkUpdating}>
                      Disable Monitoring (Non-Critical)
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              ) : null}
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
            title={
              total === 0 &&
              !debouncedQuery &&
              statusFilter === 'all' &&
              typeFilter === 'all' &&
              criticalFilter === 'all'
                ? 'No devices yet'
                : 'No matches'
            }
            description={
              total === 0 &&
              !debouncedQuery &&
              statusFilter === 'all' &&
              typeFilter === 'all' &&
              criticalFilter === 'all'
                ? 'Add a device manually, import CSV, or discover hosts on your network.'
                : 'Try a different search or filter.'
            }
          />
        ) : (
          <Card variant="primary" className="glass overflow-hidden rounded-xl">
            <CardContent className="p-0">
              <div className="w-full max-w-full overflow-x-auto p-1 sm:p-2">
                <Table className="min-w-[960px]">
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

      <AlertDialog open={pingAllConfirmOpen} onOpenChange={setPingAllConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Ping all devices now?</AlertDialogTitle>
            <AlertDialogDescription>
              This runs a manual ICMP ping for every device in inventory and updates status,
              latency, and ping history. It may take a minute on large fleets.
              {dash.summary?.totalDevices != null
                ? ` About ${dash.summary.totalDevices} device(s) will be pinged.`
                : ''}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={pingAll.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={pingAll.isPending}
              onClick={(e) => {
                e.preventDefault()
                pingAll.mutate(undefined, {
                  onSettled: () => setPingAllConfirmOpen(false),
                })
              }}
            >
              {pingAll.isPending ? 'Pinging…' : 'Start ping all'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={nmapAllConfirmOpen} onOpenChange={setNmapAllConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Run Nmap on all online devices?</AlertDialogTitle>
            <AlertDialogDescription>
              This starts a deep detail scan for every currently online device. It can take several
              minutes and may briefly increase network load.
              {dash.summary?.onlineDevices != null
                ? ` About ${dash.summary.onlineDevices} online device(s) will be scanned.`
                : ''}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={nmapScanAll.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={nmapScanAll.isPending}
              onClick={(e) => {
                e.preventDefault()
                nmapScanAll.mutate(undefined, {
                  onSettled: () => setNmapAllConfirmOpen(false),
                })
              }}
            >
              {nmapScanAll.isPending ? 'Scanning…' : 'Start Nmap scan'}
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
