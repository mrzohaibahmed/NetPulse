import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQueries } from '@tanstack/react-query'
import {
  ChevronDown,
  ChevronUp,
  Download,
  ExternalLink,
  Network,
  RefreshCw,
  Radar,
} from 'lucide-react'
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
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent } from '@/shared/ui/card'
import { Input } from '@/shared/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import { queryKeys } from '@/hooks/queryKeys'
import { useClientPagination } from '@/hooks/useClientPagination'
import { useDevicesQuery, useInterfaceMutations, useInterfacesQuery, useRiskQuery } from '@/hooks/queries'
import { isManagedSwitch } from '@/modules/storm/components/stormShared'
import type { InterfaceListRow, InterfaceStat } from '@/types'
import { formatDateTime, formatRelative } from '@/utils/format'
import { interfaceNamesMatch } from '@/utils/interfaceNames'

/** Backend max for GET /api/interfaces — fetch full inventory for client grouping. */
const FETCH_LIMIT = 500
const DEFAULT_SWITCHES_PER_PAGE = 10
const DEFAULT_PORTS_PER_PAGE = 25
const COLLAPSE_KEY = 'netpulse.interfaceInventory.collapsedDevices'
type RiskLevel = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN'

type InterfaceRowWithRisk = InterfaceListRow & { stormRisk: RiskLevel }

type DeviceSection = {
  deviceId: string
  hostname: string
  ipAddress: string
  vendor: string
  status: string
  monitor: boolean
  lastScan: string | null
  interfaces: InterfaceRowWithRisk[]
}

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

export function InterfacesEnterprisePage() {
  const navigate = useNavigate()
  const { isAdmin } = useAuth()
  const mutations = useInterfaceMutations()

  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [operFilter, setOperFilter] = useState('all')
  const [modeFilter, setModeFilter] = useState('all')
  const [switchFilter, setSwitchFilter] = useState('all')
  const [vendorFilter, setVendorFilter] = useState('all')
  const [classificationFilter, setClassificationFilter] = useState('all')
  const [stormRiskFilter, setStormRiskFilter] = useState('all')
  const [vlanFilter, setVlanFilter] = useState('')
  const [collapsedDeviceIds, setCollapsedDeviceIds] = useState<Set<string>>(() => {
    try {
      const raw = sessionStorage.getItem(COLLAPSE_KEY)
      if (!raw) return new Set<string>()
      const parsed = JSON.parse(raw)
      return Array.isArray(parsed) ? new Set(parsed.filter((v) => typeof v === 'string')) : new Set<string>()
    } catch {
      return new Set<string>()
    }
  })

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 300)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => {
    sessionStorage.setItem(COLLAPSE_KEY, JSON.stringify([...collapsedDeviceIds]))
  }, [collapsedDeviceIds])

  // Ask the API for switch-like types so older switches are not dropped by
  // newest-first pagination when the inventory has many discovered hosts.
  const devicesQuery = useDevicesQuery({
    page: 1,
    limit: FETCH_LIMIT,
    deviceType: 'switch',
  })
  const devices = devicesQuery.data?.data ?? []

  const switchDevices = useMemo(
    () => devices.filter((device) => isManagedSwitch(device.deviceType)),
    [devices],
  )
  const switchIdSet = useMemo(
    () => new Set(switchDevices.map((device) => device._id)),
    [switchDevices],
  )

  const interfacesQuery = useInterfacesQuery({
    page: 1,
    limit: FETCH_LIMIT,
    ...(switchFilter !== 'all' ? { deviceId: switchFilter } : {}),
  })
  const riskQuery = useRiskQuery({ page: 1, limit: 1000 })

  const interfaces = interfacesQuery.data?.data ?? []
  const riskRows = riskQuery.data?.data ?? []

  const deviceIds = useMemo(
    () =>
      [...new Set(interfaces.map((row) => row.deviceId).filter(Boolean))].filter((id) =>
        switchIdSet.has(id),
      ),
    [interfaces, switchIdSet],
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

  const rows = useMemo(() => mergeInterfaceRows(interfaces, statsByDevice), [interfaces, statsByDevice])

  const switchRows = useMemo(
    () => rows.filter((row) => switchIdSet.has(row.deviceId)),
    [rows, switchIdSet],
  )

  const riskByInterface = useMemo(() => {
    const map = new Map<string, RiskLevel>()
    for (const row of riskRows) {
      if (!switchIdSet.has(row.deviceId)) continue
      const key = `${row.deviceId}::${String(row.interface || '').toLowerCase()}`
      const level = normalizeRisk(row.severity)
      const current = map.get(key) || 'UNKNOWN'
      if (riskRank(level) > riskRank(current)) map.set(key, level)
    }
    return map
  }, [riskRows, switchIdSet])

  const rowsWithRisk = useMemo<InterfaceRowWithRisk[]>(
    () =>
      switchRows.map((row) => {
        const exact = riskByInterface.get(`${row.deviceId}::${row.name.toLowerCase()}`)
        return { ...row, stormRisk: exact || inferStormRisk(row) }
      }),
    [switchRows, riskByInterface],
  )

  const vendorOptions = useMemo(() => {
    const fromInterfaces = rowsWithRisk.map((row) => row.vendor).filter(Boolean)
    const fromDevices = switchDevices.map(
      (device) => device.credentials?.sshVendor || '',
    ).filter(Boolean)
    return Array.from(new Set([...fromInterfaces, ...fromDevices])).sort((a, b) =>
      a.localeCompare(b),
    )
  }, [rowsWithRisk, switchDevices])

  const filteredRows = useMemo(() => {
    const search = debouncedQuery.trim().toLowerCase()
    const vlanQ = vlanFilter.trim().toLowerCase()
    const switchVendorById = new Map(
      switchDevices.map((device) => [
        device._id,
        (device.credentials?.sshVendor || '').toLowerCase(),
      ]),
    )
    return rowsWithRisk.filter((row) => {
      if (switchFilter !== 'all' && row.deviceId !== switchFilter) return false
      if (vendorFilter !== 'all') {
        const vendor = String(row.vendor || switchVendorById.get(row.deviceId) || '').toLowerCase()
        if (vendor !== vendorFilter.toLowerCase()) return false
      }
      if (operFilter !== 'all' && (row.operStatus || '').toLowerCase() !== operFilter.toLowerCase()) {
        return false
      }
      if (modeFilter !== 'all') {
        const mode = (row.portMode || row.mode || '').toLowerCase()
        if (mode !== modeFilter.toLowerCase()) return false
      }
      if (classificationFilter !== 'all' && !matchesClassification(row, classificationFilter)) return false
      if (stormRiskFilter !== 'all' && row.stormRisk !== stormRiskFilter) return false
      if (vlanQ) {
        const blobs = [
          String(row.accessVlan ?? ''),
          String(row.voiceVlan ?? ''),
          String(row.nativeVlan ?? ''),
          (row.allowedVlans || []).join(','),
        ]
        if (!blobs.some((b) => b.toLowerCase().includes(vlanQ))) return false
      }
      if (search) {
        const haystack = [
          row.name,
          row.description,
          row.hostname,
          row.ipAddress,
          row.vendor,
          row.neighbor?.hostname,
          String(row.accessVlan ?? ''),
          String(row.voiceVlan ?? ''),
          String(row.nativeVlan ?? ''),
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
        if (!haystack.includes(search)) return false
      }
      return true
    })
  }, [
    rowsWithRisk,
    switchDevices,
    switchFilter,
    vendorFilter,
    operFilter,
    modeFilter,
    classificationFilter,
    stormRiskFilter,
    vlanFilter,
    debouncedQuery,
  ])

  const deviceById = useMemo(() => new Map(switchDevices.map((d) => [d._id, d])), [switchDevices])

  const groupedDevices = useMemo<DeviceSection[]>(() => {
    const groups = new Map<string, DeviceSection>()

    // Always seed sections from managed switches so cards appear even before discovery.
    for (const device of switchDevices) {
      if (switchFilter !== 'all' && device._id !== switchFilter) continue
      if (vendorFilter !== 'all') {
        const vendor = (device.credentials?.sshVendor || '').toLowerCase()
        if (vendor !== vendorFilter.toLowerCase()) continue
      }
      groups.set(device._id, {
        deviceId: device._id,
        hostname: device.hostname || 'Unknown switch',
        ipAddress: device.ipAddress || '—',
        vendor: device.credentials?.sshVendor || 'Unknown',
        status: device.status || 'Unknown',
        monitor: device.monitor ?? true,
        lastScan: device.networkInfo?.lastScan || null,
        interfaces: [],
      })
    }

    for (const row of filteredRows) {
      if (!groups.has(row.deviceId)) {
        const device = deviceById.get(row.deviceId)
        if (!device) continue
        groups.set(row.deviceId, {
          deviceId: row.deviceId,
          hostname: row.hostname || device.hostname || 'Unknown switch',
          ipAddress: row.ipAddress || device.ipAddress || '—',
          vendor: row.vendor || device.credentials?.sshVendor || 'Unknown',
          status: device.status || 'Unknown',
          monitor: device.monitor ?? true,
          lastScan: row.statsTimestamp || row.lastUpdated || device.networkInfo?.lastScan || null,
          interfaces: [],
        })
      }
      const section = groups.get(row.deviceId)!
      section.interfaces.push(row)
      if (!section.vendor || section.vendor === 'Unknown') {
        section.vendor = row.vendor || section.vendor
      }
      const ts = row.statsTimestamp || row.lastUpdated
      if (isNewer(ts, section.lastScan)) section.lastScan = ts
    }

    // When interface-level filters are active, hide switches with no matching ports.
    const interfaceFiltersActive =
      Boolean(debouncedQuery.trim()) ||
      operFilter !== 'all' ||
      modeFilter !== 'all' ||
      classificationFilter !== 'all' ||
      stormRiskFilter !== 'all' ||
      Boolean(vlanFilter.trim())

    const sections = [...groups.values()].filter((section) => {
      if (!interfaceFiltersActive) return true
      return section.interfaces.length > 0
    })

    return sections.sort((a, b) => a.hostname.localeCompare(b.hostname))
  }, [
    switchDevices,
    filteredRows,
    deviceById,
    switchFilter,
    vendorFilter,
    debouncedQuery,
    operFilter,
    modeFilter,
    classificationFilter,
    stormRiskFilter,
    vlanFilter,
  ])

  const switchPagination = useClientPagination(groupedDevices, DEFAULT_SWITCHES_PER_PAGE)
  const pagedDevices = switchPagination.pageItems

  useEffect(() => {
    switchPagination.reset()
    // Reset only when filter inputs change — not when pagination helpers change identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional filter-driven reset
  }, [
    debouncedQuery,
    operFilter,
    modeFilter,
    switchFilter,
    vendorFilter,
    classificationFilter,
    stormRiskFilter,
    vlanFilter,
  ])

  const allVisibleDeviceIds = useMemo(() => groupedDevices.map((d) => d.deviceId), [groupedDevices])
  const hasActiveFilters =
    Boolean(debouncedQuery) ||
    operFilter !== 'all' ||
    modeFilter !== 'all' ||
    switchFilter !== 'all' ||
    vendorFilter !== 'all' ||
    classificationFilter !== 'all' ||
    stormRiskFilter !== 'all' ||
    Boolean(vlanFilter.trim())

  const noSwitchesConfigured = !devicesQuery.isLoading && switchDevices.length === 0
  const hasNonSwitchDevices = devices.length > 0 && switchDevices.length === 0
  const isBusy = mutations.discoverAll.isPending || mutations.collectAll.isPending

  const toggleDevice = (deviceId: string) => {
    setCollapsedDeviceIds((current) => {
      const next = new Set(current)
      if (next.has(deviceId)) next.delete(deviceId)
      else next.add(deviceId)
      return next
    })
  }

  const collapseAll = () => setCollapsedDeviceIds(new Set(allVisibleDeviceIds))
  const expandAll = () => setCollapsedDeviceIds(new Set())

  const exportDeviceInterfaces = (device: DeviceSection) => {
    const header = [
      'status',
      'interface',
      'description',
      'mode',
      'vlan',
      'speed',
      'utilization',
      'classification',
      'stormRisk',
      'rxUtilization',
      'txUtilization',
      'lastUpdated',
    ]
    const lines = [
      header.join(','),
      ...device.interfaces.map((row) =>
        [
          row.operStatus,
          escapeCsv(row.name),
          escapeCsv(row.description || ''),
          escapeCsv(row.portMode || row.mode || 'unknown'),
          escapeCsv(vlanSummary(row)),
          escapeCsv(String(row.speedMbps ?? row.speed ?? '—')),
          escapeCsv(formatPercentValue(row.utilization)),
          escapeCsv(classificationSummary(row)),
          row.stormRisk,
          escapeCsv(formatPercentValue(row.rxUtilization)),
          escapeCsv(formatPercentValue(row.txUtilization)),
          escapeCsv(formatDateTime(row.statsTimestamp || row.lastUpdated)),
        ].join(','),
      ),
    ]
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `interfaces_${device.hostname.replace(/[^a-z0-9-_]+/gi, '_').toLowerCase()}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const isLoading =
    (devicesQuery.isLoading && devices.length === 0) ||
    (interfacesQuery.isLoading && switchDevices.length === 0 && rows.length === 0)

  return (
    <div className="np-page mx-auto w-full max-w-[1600px]">
      <PageHeader
        title="Interface Inventory"
        description="Switch Interface Manager — managed switches and their ports only"
        actions={
          <div className="flex flex-wrap items-center justify-end gap-2">
            {isAdmin && switchDevices.length > 0 ? (
              <>
                <Button type="button" variant="secondary" size="sm" disabled={isBusy} onClick={() => mutations.discoverAll.mutate()}>
                  <Radar className="mr-1.5 h-4 w-4" />
                  {mutations.discoverAll.isPending ? 'Discovering…' : 'Discover all'}
                </Button>
                <Button type="button" size="sm" disabled={isBusy} onClick={() => mutations.collectAll.mutate()}>
                  <RefreshCw className="mr-1.5 h-4 w-4" />
                  {mutations.collectAll.isPending ? 'Collecting…' : 'Collect stats'}
                </Button>
              </>
            ) : null}
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => {
                void devicesQuery.refetch()
                void interfacesQuery.refetch()
              }}
            >
              Refresh
            </Button>
            {switchDevices.length > 0 ? (
              <>
                <Button type="button" size="sm" variant="secondary" onClick={expandAll}>
                  Expand all
                </Button>
                <Button type="button" size="sm" variant="secondary" onClick={collapseAll}>
                  Collapse all
                </Button>
              </>
            ) : null}
          </div>
        }
      />

      {noSwitchesConfigured ? (
        <EmptyState
          icon={Network}
          title={
            hasNonSwitchDevices
              ? 'Interface Inventory is available only for managed switches.'
              : 'No managed switches have been added.'
          }
          description={
            hasNonSwitchDevices
              ? 'Routers, servers, PCs, printers, and other device types appear in Device Inventory. Add a device with type Switch or Managed Switch to manage interfaces here.'
              : 'Add a managed switch from Device Inventory to begin discovering switch ports.'
          }
          actionLabel="Add Device"
          onAction={() => navigate('/devices')}
        />
      ) : (
        <>
          <Card className="border-border/70 bg-card/70 shadow-sm">
            <CardContent className="space-y-3 p-4">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
                <div className="min-w-0 space-y-1.5 lg:max-w-md lg:flex-1">
                  <label
                    className="text-xs font-medium text-muted-foreground"
                    htmlFor="interface-inventory-search"
                  >
                    Search
                  </label>
                  <Input
                    id="interface-inventory-search"
                    type="search"
                    placeholder="Search interfaces, host, IP, VLAN, neighbor..."
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </div>
                <div className="grid flex-1 gap-2 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-muted-foreground" htmlFor="interface-status-filter">
                      Status
                    </label>
                    <Select value={operFilter} onValueChange={setOperFilter}>
                      <SelectTrigger id="interface-status-filter">
                        <SelectValue placeholder="All Status" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All Status</SelectItem>
                        <SelectItem value="up">Up</SelectItem>
                        <SelectItem value="down">Down</SelectItem>
                        <SelectItem value="unknown">Unknown</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-muted-foreground" htmlFor="interface-mode-filter">
                      Mode
                    </label>
                    <Select value={modeFilter} onValueChange={setModeFilter}>
                      <SelectTrigger id="interface-mode-filter">
                        <SelectValue placeholder="All Modes" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All Modes</SelectItem>
                        <SelectItem value="access">Access</SelectItem>
                        <SelectItem value="trunk">Trunk</SelectItem>
                        <SelectItem value="routed">Routed</SelectItem>
                        <SelectItem value="unknown">Unknown</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <label
                      className="text-xs font-medium text-muted-foreground"
                      htmlFor="interface-classification-filter"
                    >
                      Classification
                    </label>
                    <Select value={classificationFilter} onValueChange={setClassificationFilter}>
                      <SelectTrigger id="interface-classification-filter">
                        <SelectValue placeholder="All Types" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All Types</SelectItem>
                        <SelectItem value="access">User</SelectItem>
                        <SelectItem value="trunk">Trunk</SelectItem>
                        <SelectItem value="uplink">Uplink</SelectItem>
                        <SelectItem value="infrastructure">Infrastructure</SelectItem>
                        <SelectItem value="management">Management</SelectItem>
                        <SelectItem value="protected">Protected</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-muted-foreground" htmlFor="interface-risk-filter">
                      Risk
                    </label>
                    <Select value={stormRiskFilter} onValueChange={setStormRiskFilter}>
                      <SelectTrigger id="interface-risk-filter">
                        <SelectValue placeholder="All Risks" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All Risks</SelectItem>
                        <SelectItem value="CRITICAL">Critical</SelectItem>
                        <SelectItem value="HIGH">High</SelectItem>
                        <SelectItem value="MEDIUM">Medium</SelectItem>
                        <SelectItem value="LOW">Low</SelectItem>
                        <SelectItem value="UNKNOWN">None</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-muted-foreground" htmlFor="interface-vlan-filter">
                      VLAN
                    </label>
                    <Input
                      id="interface-vlan-filter"
                      placeholder="All VLANs"
                      value={vlanFilter}
                      onChange={(e) => setVlanFilter(e.target.value)}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-muted-foreground" htmlFor="interface-vendor-filter">
                      Vendor
                    </label>
                    <Select value={vendorFilter} onValueChange={setVendorFilter}>
                      <SelectTrigger id="interface-vendor-filter">
                        <SelectValue placeholder="All Vendors" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All Vendors</SelectItem>
                        {vendorOptions.map((vendor) => (
                          <SelectItem key={vendor} value={vendor}>
                            {vendor}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>

          {isLoading ? (
            <>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Card key={i}>
                    <CardContent className="space-y-2 p-4">
                      <div className="h-4 w-1/2 animate-pulse rounded bg-muted" />
                      <div className="h-8 animate-pulse rounded bg-muted" />
                    </CardContent>
                  </Card>
                ))}
              </div>
              <TableSkeleton />
            </>
          ) : interfacesQuery.error && switchDevices.length === 0 ? (
            <ErrorState
              message={
                interfacesQuery.error instanceof Error
                  ? interfacesQuery.error.message
                  : 'Failed to load interfaces'
              }
              onRetry={() => void interfacesQuery.refetch()}
            />
          ) : groupedDevices.length === 0 ? (
            <EmptyState
              icon={Network}
              title={hasActiveFilters ? 'No matching switch interfaces' : 'No switch interfaces discovered yet'}
              description={
                hasActiveFilters
                  ? 'Adjust filters to see more switch ports.'
                  : isAdmin
                    ? 'Run Discover all on online switches with SSH credentials configured.'
                    : 'Ask an administrator to discover interfaces on managed switches.'
              }
              actionLabel={isAdmin && !hasActiveFilters ? 'Discover all' : undefined}
              onAction={
                isAdmin && !hasActiveFilters ? () => mutations.discoverAll.mutate() : undefined
              }
            />
          ) : (
            <>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                {pagedDevices.map((device) => {
                  const summary = summarizeDevice(device.interfaces)
                  const selected = switchFilter === device.deviceId
                  return (
                    <button
                      key={device.deviceId}
                      type="button"
                      className={`rounded-xl border p-3.5 text-left shadow-sm transition hover:-translate-y-0.5 ${
                        selected ? 'border-primary bg-primary/5' : 'border-border/70 bg-card'
                      }`}
                      onClick={() =>
                        setSwitchFilter((current) =>
                          current === device.deviceId ? 'all' : device.deviceId,
                        )
                      }
                    >
                      <div className="mb-2 flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold">{device.hostname}</p>
                          <p className="mono text-xs text-muted-foreground">{device.ipAddress}</p>
                        </div>
                        <StatusBadge status={device.status} />
                      </div>
                      <div className="grid grid-cols-2 gap-y-1 text-xs">
                        <span className="text-muted-foreground">Vendor</span>
                        <span className="truncate text-right font-medium">{device.vendor || '—'}</span>
                        <span className="text-muted-foreground">Interfaces</span>
                        <span className="text-right font-medium">{summary.total}</span>
                        <span className="text-muted-foreground">Active / Down</span>
                        <span className="text-right font-medium">
                          {summary.up} / {summary.down}
                        </span>
                        <span className="text-muted-foreground">Access / Trunk</span>
                        <span className="text-right font-medium">
                          {summary.access} / {summary.trunk}
                        </span>
                        <span className="text-muted-foreground">Last Discovery</span>
                        <span className="truncate text-right">{formatRelative(device.lastScan)}</span>
                      </div>
                    </button>
                  )
                })}
              </div>

              {switchPagination.totalPages > 1 || switchPagination.total > switchPagination.limit ? (
                <PaginationControls
                  page={switchPagination.page}
                  totalPages={Math.max(switchPagination.totalPages, 1)}
                  total={switchPagination.total}
                  limit={switchPagination.limit}
                  onPageChange={switchPagination.setPage}
                  onLimitChange={switchPagination.setLimit}
                  limitOptions={[5, 10, 25, 50]}
                  unitLabel="Switches"
                />
              ) : null}

              <div className="space-y-4">
                {pagedDevices.map((device) => (
                  <DeviceInventorySection
                    key={device.deviceId}
                    device={device}
                    collapsed={collapsedDeviceIds.has(device.deviceId)}
                    isAdmin={isAdmin}
                    collectPending={mutations.collectDevice.isPending}
                    onToggle={() => toggleDevice(device.deviceId)}
                    onCollect={() => mutations.collectDevice.mutate(device.deviceId)}
                    onExport={() => exportDeviceInterfaces(device)}
                    onOpenRow={(path) => navigate(path)}
                  />
                ))}
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}

function DeviceInventorySection({
  device,
  collapsed,
  isAdmin,
  collectPending,
  onToggle,
  onCollect,
  onExport,
  onOpenRow,
}: {
  device: DeviceSection
  collapsed: boolean
  isAdmin: boolean
  collectPending: boolean
  onToggle: () => void
  onCollect: () => void
  onExport: () => void
  onOpenRow: (path: string) => void
}) {
  const summary = summarizeDevice(device.interfaces)
  const portPagination = useClientPagination(device.interfaces, DEFAULT_PORTS_PER_PAGE)

  useEffect(() => {
    portPagination.reset()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset when port set changes
  }, [device.deviceId, device.interfaces.length])

  return (
    <Card className="border-border/70 shadow-sm">
      <div className="border-b border-border/60 bg-card">
        <div className="space-y-3 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-md bg-primary/10 p-1 text-primary">
                  <Network className="h-4 w-4" />
                </span>
                <h3 className="truncate text-base font-semibold">{device.hostname}</h3>
                <Badge variant={device.monitor ? 'success' : 'muted'}>
                  {device.monitor ? 'Monitored' : 'Not monitored'}
                </Badge>
                <StatusBadge status={device.status} />
              </div>
              <p className="mono mt-1 text-xs text-muted-foreground">
                {device.ipAddress} · {device.vendor || 'Unknown vendor'} · {summary.total} Interfaces
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant="secondary"
                disabled={collectPending}
                onClick={onCollect}
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Refresh
              </Button>
              <Button type="button" size="sm" variant="secondary" onClick={onExport}>
                <Download className="h-3.5 w-3.5" />
                Export
              </Button>
              <Button type="button" size="sm" variant="secondary" asChild>
                <Link to={`/devices/${device.deviceId}`}>
                  <ExternalLink className="h-3.5 w-3.5" />
                  Device
                </Link>
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={onToggle}>
                {collapsed ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
                {collapsed ? 'Expand' : 'Collapse'}
              </Button>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8">
            <Kpi label="Interfaces" value={String(summary.total)} />
            <Kpi label="Active" value={String(summary.up)} tone="success" />
            <Kpi label="Down" value={String(summary.down)} tone="danger" />
            <Kpi label="Access" value={String(summary.access)} />
            <Kpi label="Trunk" value={String(summary.trunk)} />
            <Kpi label="Protected" value={String(summary.protected)} />
            <Kpi label="Critical Storm" value={String(summary.criticalStorm)} tone="danger" />
            <Kpi label="Avg Util" value={formatPercentValue(summary.avgUtilization)} />
          </div>
        </div>
      </div>

      {!collapsed ? (
        <CardContent className="p-0">
          {device.interfaces.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-muted-foreground">
              No interfaces discovered for this switch yet.
              {isAdmin
                ? ' Use Discover all or refresh this switch after SSH credentials are configured.'
                : ''}
            </div>
          ) : (
            <>
              <div className="w-full max-w-full">
                <Table className="min-w-[1080px]">
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead>Status</TableHead>
                      <TableHead className="min-w-[8rem]">Interface</TableHead>
                      <TableHead className="min-w-[8rem]">Description</TableHead>
                      <TableHead>Mode</TableHead>
                      <TableHead className="min-w-[7rem]">VLAN</TableHead>
                      <TableHead>Speed</TableHead>
                      <TableHead className="min-w-[7rem]">Utilization</TableHead>
                      <TableHead className="min-w-[8rem]">Classification</TableHead>
                      <TableHead>Storm Risk</TableHead>
                      <TableHead className="min-w-[7rem]">RX / TX</TableHead>
                      <TableHead className="min-w-[7rem]">Updated</TableHead>
                      <TableHead className="w-[4.5rem]">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {portPagination.pageItems.map((row) => {
                      const detailPath = `/interfaces/${row.deviceId}/${encodeURIComponent(row.name)}`
                      const remotePort = neighborRemotePort(row.neighbor)
                      return (
                        <TableRow
                          key={row._id}
                          className="cursor-pointer hover:bg-muted/40"
                          onClick={() => onOpenRow(detailPath)}
                        >
                          <TableCell>
                            <InterfaceStatusBadge status={row.operStatus} />
                          </TableCell>
                          <TableCell className="whitespace-nowrap font-medium">{row.name}</TableCell>
                          <TableCell className="max-w-[180px] truncate text-muted-foreground">
                            {row.description || '—'}
                          </TableCell>
                          <TableCell>
                            <PortModeBadge mode={row.portMode || row.mode || 'unknown'} />
                          </TableCell>
                          <TableCell className="mono text-xs text-muted-foreground">
                            {vlanSummary(row)}
                          </TableCell>
                          <TableCell className="mono whitespace-nowrap text-muted-foreground">
                            {row.speedMbps != null ? `${row.speedMbps} Mbps` : row.speed || '—'}
                          </TableCell>
                          <TableCell>
                            <UtilizationBar value={row.utilization} />
                          </TableCell>
                          <TableCell>
                            <PortClassificationBadges iface={row} includeMode />
                          </TableCell>
                          <TableCell>
                            <RiskBadge risk={row.stormRisk} />
                          </TableCell>
                          <TableCell className="mono text-xs text-muted-foreground">
                            RX {formatPercentValue(row.rxUtilization)} / TX{' '}
                            {formatPercentValue(row.txUtilization)}
                            <div className="text-[10px] text-muted-foreground/80">
                              {row.neighbor?.hostname || '—'}
                              {remotePort ? ` (${remotePort})` : ''}
                            </div>
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                            <div>{formatRelative(row.statsTimestamp || row.lastUpdated)}</div>
                            <div className="text-[10px] text-muted-foreground/80">
                              {formatDateTime(row.statsTimestamp || row.lastUpdated)}
                            </div>
                          </TableCell>
                          <TableCell>
                            <Button
                              type="button"
                              size="sm"
                              variant="ghost"
                              asChild
                              onClick={(e) => e.stopPropagation()}
                            >
                              <Link to={detailPath}>Open</Link>
                            </Button>
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </div>
              {portPagination.totalPages > 1 || portPagination.total > portPagination.limit ? (
                <div className="border-t border-border px-4 pb-2">
                  <PaginationControls
                    page={portPagination.page}
                    totalPages={Math.max(portPagination.totalPages, 1)}
                    total={portPagination.total}
                    limit={portPagination.limit}
                    onPageChange={portPagination.setPage}
                    onLimitChange={portPagination.setLimit}
                    limitOptions={[25, 50, 100]}
                    unitLabel="Ports"
                  />
                </div>
              ) : null}
            </>
          )}
        </CardContent>
      ) : null}
    </Card>
  )
}

function summarizeDevice(rows: InterfaceRowWithRisk[]) {
  const up = rows.filter((row) => (row.operStatus || '').toLowerCase() === 'up').length
  const down = rows.filter((row) => (row.operStatus || '').toLowerCase() === 'down').length
  const access = rows.filter((row) => row.isAccess || row.portMode === 'access' || row.mode === 'access').length
  const trunk = rows.filter((row) => row.isTrunk || row.portMode === 'trunk' || row.mode === 'trunk').length
  const protectedCount = rows.filter((row) => row.isProtected).length
  const criticalStorm = rows.filter((row) => row.stormRisk === 'CRITICAL').length
  const utilValues = rows.map((row) => row.utilization).filter((value): value is number => typeof value === 'number')
  const avgUtilization = utilValues.length ? utilValues.reduce((sum, v) => sum + v, 0) / utilValues.length : null
  return {
    total: rows.length,
    up,
    down,
    access,
    trunk,
    protected: protectedCount,
    criticalStorm,
    avgUtilization,
  }
}

function normalizeRisk(value: unknown): RiskLevel {
  const normalized = String(value || '').toUpperCase()
  if (normalized === 'CRITICAL' || normalized === 'HIGH' || normalized === 'MEDIUM' || normalized === 'LOW') return normalized
  return 'UNKNOWN'
}

function inferStormRisk(row: InterfaceListRow): RiskLevel {
  const util = typeof row.utilization === 'number' ? row.utilization : 0
  const errors = (row.inputErrors ?? 0) + (row.outputErrors ?? 0) + (row.discards ?? 0)
  if (util >= 90 || errors > 2000) return 'CRITICAL'
  if (util >= 75 || errors > 800) return 'HIGH'
  if (util >= 50 || errors > 100) return 'MEDIUM'
  if (util > 0 || errors > 0) return 'LOW'
  return 'UNKNOWN'
}

function riskRank(level: RiskLevel): number {
  if (level === 'CRITICAL') return 4
  if (level === 'HIGH') return 3
  if (level === 'MEDIUM') return 2
  if (level === 'LOW') return 1
  return 0
}

function matchesClassification(row: InterfaceListRow, filter: string): boolean {
  if (filter === 'access') return Boolean(row.isAccess || row.portMode === 'access' || row.mode === 'access')
  if (filter === 'trunk') return Boolean(row.isTrunk || row.portMode === 'trunk' || row.mode === 'trunk')
  if (filter === 'uplink') return Boolean(row.isUplink)
  if (filter === 'infrastructure') return Boolean(row.isInfrastructure)
  if (filter === 'management') return Boolean(row.isManagement)
  if (filter === 'protected') return Boolean(row.isProtected)
  return true
}

function formatPercentValue(value: number | null | undefined): string {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return `${Number(value).toFixed(1)}%`
}

function vlanSummary(row: InterfaceListRow): string {
  const basic = [
    row.accessVlan != null ? `A:${row.accessVlan}` : null,
    row.voiceVlan != null ? `V:${row.voiceVlan}` : null,
    row.nativeVlan != null ? `N:${row.nativeVlan}` : null,
  ].filter(Boolean)
  const allowed = formatAllowedVlans(row.allowedVlans, 4)
  return basic.length ? `${basic.join(' ')} · ${allowed}` : allowed
}

function classificationSummary(row: InterfaceListRow): string {
  const tags: string[] = []
  if (row.isAccess || row.portMode === 'access' || row.mode === 'access') tags.push('access')
  if (row.isTrunk || row.portMode === 'trunk' || row.mode === 'trunk') tags.push('trunk')
  if (row.isUplink) tags.push('uplink')
  if (row.isInfrastructure) tags.push('infrastructure')
  if (row.isManagement) tags.push('management')
  if (row.isProtected) tags.push('protected')
  return tags.length ? tags.join('|') : 'normal'
}

function isNewer(next: string | null | undefined, current: string | null | undefined): boolean {
  if (!next) return false
  if (!current) return true
  const nextTs = Date.parse(next)
  const currentTs = Date.parse(current)
  if (Number.isNaN(nextTs)) return false
  if (Number.isNaN(currentTs)) return true
  return nextTs > currentTs
}

function escapeCsv(value: string): string {
  const escaped = value.replace(/"/g, '""')
  return /[",\n]/.test(escaped) ? `"${escaped}"` : escaped
}

function RiskBadge({ risk }: { risk: RiskLevel }) {
  const variant =
    risk === 'CRITICAL'
      ? 'danger'
      : risk === 'HIGH'
        ? 'warning'
        : risk === 'MEDIUM'
          ? 'secondary'
          : risk === 'LOW'
            ? 'success'
            : 'muted'
  return <Badge variant={variant}>{risk === 'UNKNOWN' ? 'Unknown' : risk}</Badge>
}

function Kpi({ label, value, tone = 'default' }: { label: string; value: string; tone?: 'default' | 'success' | 'danger' }) {
  const className =
    tone === 'success'
      ? 'border-success/30 bg-success/10 text-success'
      : tone === 'danger'
        ? 'border-danger/30 bg-danger/10 text-danger'
        : 'border-border/60 bg-secondary/30 text-foreground'
  return (
    <div className={`rounded-lg border px-2.5 py-2 ${className}`}>
      <p className="text-[10px] uppercase tracking-wide opacity-80">{label}</p>
      <p className="mono mt-1 text-sm font-semibold">{value}</p>
    </div>
  )
}
