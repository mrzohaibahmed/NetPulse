import { useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  Cpu,
  Filter,
  HardDrive,
  RefreshCw,
  Search,
  Server,
  Wifi,
} from 'lucide-react'
import { getDevices } from '@/api'
import { isServerHardwareDevice, SERVER_HARDWARE_DEVICE_TYPES } from '@/modules/ping/constants/devices'
import { DeviceDrawer } from '@/modules/ping/components/DeviceDrawer'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { KpiCard } from '@/shared/components/KpiCard'
import { TableSkeleton } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Input } from '@/shared/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import type { Device } from '@/types'
import { useAuth } from '@/shared/auth/AuthContext'
import { useDeviceMutations, useSettingsMutation, useSettingsQuery } from '@/hooks/queries'
import { Checkbox } from '@/shared/ui/checkbox'
import { fetchAllListPages } from '@/utils/fetchAllPages'
import { formatMs, formatRelative } from '@/utils/format'
import { useClientPagination } from '@/hooks/useClientPagination'

const DEFAULT_PAGE_SIZE = 25

export function ServerHardwarePage() {
  const navigate = useNavigate()
  const { deviceId: routeDeviceId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const { isAdmin } = useAuth()
  const deviceMutations = useDeviceMutations()
  const settingsQuery = useSettingsQuery(true)
  const settingsMutation = useSettingsMutation()
  const monitoringEnabled = Boolean(settingsQuery.data?.serverHardwareMonitoringEnabled)

  const toggleMonitoring = (enabled: boolean) => {
    settingsMutation.mutate({ serverHardwareMonitoringEnabled: enabled })
  }

  const queryParamDevice = searchParams.get('device')
  const drawerDeviceId = routeDeviceId || queryParamDevice || null

  const [searchQuery, setSearchQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState<string>('all')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [monitorFilter, setMonitorFilter] = useState<string>('all')

  const devicesQuery = useQuery({
    queryKey: ['server-hardware-devices'],
    queryFn: async () => {
      const res = await fetchAllListPages((page, limit) => getDevices({ page, limit }))
      return res.data
    },
    staleTime: 10_000,
  })

  const allDevices: Device[] = devicesQuery.data ?? []

  const eligibleServers = useMemo(() => {
    return allDevices.filter(
      (d) => isServerHardwareDevice(d.deviceType) && Boolean(d.iloAddress && d.iloAddress.trim()),
    )
  }, [allDevices])

  const filteredServers = useMemo(() => {
    return eligibleServers.filter((device) => {
      if (typeFilter !== 'all' && device.deviceType !== typeFilter) {
        return false
      }

      if (statusFilter !== 'all' && device.status !== statusFilter) {
        return false
      }

      if (monitorFilter !== 'all') {
        if (monitorFilter === 'monitored' && !device.monitor) return false
        if (monitorFilter === 'disabled' && device.monitor) return false
      }

      if (searchQuery.trim()) {
        const q = searchQuery.trim().toLowerCase()
        const textBlob = [
          device.hostname,
          device.ipAddress,
          device.iloAddress,
          device.vendor,
          device.location,
          device.deviceType,
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()

        if (!textBlob.includes(q)) return false
      }

      return true
    })
  }, [eligibleServers, typeFilter, statusFilter, monitorFilter, searchQuery])

  const pagination = useClientPagination(filteredServers, DEFAULT_PAGE_SIZE)

  const kpis = useMemo(() => {
    const total = eligibleServers.length
    const monitored = eligibleServers.filter((d) => d.monitor).length
    const online = eligibleServers.filter((d) => d.status === 'Online').length
    const criticalOffline = eligibleServers.filter(
      (d) => d.status === 'Offline (Critical)' || d.critical,
    ).length
    return { total, monitored, online, criticalOffline }
  }, [eligibleServers])

  const handleOpenDrawer = (deviceId: string) => {
    setSearchParams((prev) => {
      const copy = new URLSearchParams(prev)
      copy.set('device', deviceId)
      return copy
    })
  }

  const handleCloseDrawer = (open: boolean) => {
    if (!open) {
      if (routeDeviceId) {
        navigate('/server-hardware')
      } else {
        setSearchParams((prev) => {
          const copy = new URLSearchParams(prev)
          copy.delete('device')
          return copy
        })
      }
    }
  }

  if (devicesQuery.isLoading && allDevices.length === 0) {
    return (
      <div className="np-page space-y-6">
        <PageHeader
          title="Server Hardware"
          description="HPE iLO server hardware inventory, operational freshness, and collection diagnostics."
        />
        <TableSkeleton rows={6} />
      </div>
    )
  }

  if (devicesQuery.isError) {
    return (
      <div className="np-page space-y-6">
        <PageHeader
          title="Server Hardware"
          description="HPE iLO server hardware inventory, operational freshness, and collection diagnostics."
        />
        <ErrorState
          title="Unable to load server inventory"
          message={
            devicesQuery.error instanceof Error
              ? devicesQuery.error.message
              : 'Failed to load servers'
          }
          onRetry={() => void devicesQuery.refetch()}
        />
      </div>
    )
  }

  return (
    <div className="np-page space-y-6">
      <PageHeader
        title="Server Hardware"
        description="HPE iLO server hardware inventory, operational freshness, and collection diagnostics."
        actions={
          <div className="flex items-center gap-3">
            <Badge variant="outline" className="px-3 py-1.5 font-medium text-xs">
              {kpis.monitored}/{kpis.total} Monitored
            </Badge>
            {isAdmin ? (
              <label className="flex items-center gap-2 rounded-lg border border-border/70 bg-card px-3 py-2 text-sm">
                <Checkbox
                  checked={monitoringEnabled}
                  disabled={settingsQuery.isLoading || settingsMutation.isPending}
                  onCheckedChange={(checked) => toggleMonitoring(Boolean(checked))}
                />
                <span className="font-medium">
                  {monitoringEnabled ? 'Monitoring enabled' : 'Enable hardware monitoring'}
                </span>
              </label>
            ) : (
              <Badge variant={monitoringEnabled ? 'success' : 'warning'}>
                {monitoringEnabled ? 'Monitoring on' : 'Monitoring off'}
              </Badge>
            )}
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => void devicesQuery.refetch()}
              disabled={devicesQuery.isFetching}
            >
              <RefreshCw className={`h-4 w-4 ${devicesQuery.isFetching ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
          </div>
        }
      />

      {!monitoringEnabled ? (
        <div className="rounded-xl border border-warning/40 bg-warning/10 px-4 py-3 text-sm">
          <p className="font-medium text-foreground">Hardware monitoring is disabled</p>
          <p className="mt-1 text-muted-foreground">
            Scheduled iLO collection stays idle until an admin enables monitoring.
            {isAdmin
              ? ' Use the toggle above to turn it on.'
              : ' Ask an administrator to enable it.'}
          </p>
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard
          label="Total Servers"
          value={kpis.total}
          icon={Server}
          loading={devicesQuery.isLoading}
        />
        <KpiCard
          label="Monitored Servers"
          value={kpis.monitored}
          icon={Activity}
          loading={devicesQuery.isLoading}
        />
        <KpiCard
          label="Online Servers"
          value={kpis.online}
          icon={Wifi}
          loading={devicesQuery.isLoading}
        />
        <KpiCard
          label="Critical / Offline"
          value={kpis.criticalOffline}
          icon={HardDrive}
          loading={devicesQuery.isLoading}
        />
      </div>

      <Card variant="section" className="glass rounded-xl">
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center justify-between text-base">
            <span className="flex items-center gap-2">
              <Cpu className="h-4 w-4 text-primary" />
              Eligible Server Inventory ({filteredServers.length})
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative min-w-[220px] flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Search server hostname, IP, iLO address, location..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-9"
              />
            </div>

            <div className="flex items-center gap-2">
              <Filter className="h-4 w-4 text-muted-foreground" />
              <Select value={typeFilter} onValueChange={setTypeFilter}>
                <SelectTrigger className="w-[160px]">
                  <SelectValue placeholder="All Server Types" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Server Types</SelectItem>
                  {SERVER_HARDWARE_DEVICE_TYPES.map((t) => (
                    <SelectItem key={t} value={t}>
                      {t}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Select value={statusFilter} onValueChange={setStatusFilter}>
                <SelectTrigger className="w-[160px]">
                  <SelectValue placeholder="All Statuses" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Statuses</SelectItem>
                  <SelectItem value="Online">Online</SelectItem>
                  <SelectItem value="Not Reachable">Not Reachable</SelectItem>
                  <SelectItem value="Offline (Critical)">Offline (Critical)</SelectItem>
                  <SelectItem value="Offline">Offline</SelectItem>
                  <SelectItem value="Unknown">Unknown</SelectItem>
                </SelectContent>
              </Select>

              <Select value={monitorFilter} onValueChange={setMonitorFilter}>
                <SelectTrigger className="w-[160px]">
                  <SelectValue placeholder="All Monitoring" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Monitoring</SelectItem>
                  <SelectItem value="monitored">Monitored Only</SelectItem>
                  <SelectItem value="disabled">Disabled Only</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {eligibleServers.length === 0 ? (
            <EmptyState
              icon={Server}
              title="No server hardware devices"
              description="No Server, Linux Server, or ESXi Server devices with a configured iLO management address were found."
            />
          ) : filteredServers.length === 0 ? (
            <EmptyState
              icon={Search}
              title="No matching server devices"
              description="Try adjusting your search query or status/type/monitoring filters."
            />
          ) : (
            <>
              <div className="w-full max-w-full overflow-x-auto rounded-xl border border-border/60">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Server Hostname</TableHead>
                      <TableHead>Management IP / iLO</TableHead>
                      <TableHead>Device Type</TableHead>
                      <TableHead>Monitoring</TableHead>
                      <TableHead>Ping Status</TableHead>
                      <TableHead>Response Time</TableHead>
                      <TableHead>Last Checked</TableHead>
                      <TableHead className="text-right">Action</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {pagination.pageItems.map((device) => (
                      <TableRow
                        key={device._id}
                        className="cursor-pointer hover:bg-muted/50 transition-colors"
                        onClick={() => handleOpenDrawer(device._id)}
                      >
                        <TableCell className="font-semibold text-foreground">
                          <div className="flex items-center gap-2">
                            <span>{device.hostname}</span>
                            {device.critical ? <Badge variant="danger">Critical</Badge> : null}
                          </div>
                        </TableCell>
                        <TableCell className="mono text-xs text-muted-foreground">
                          <div>
                            {device.ipAddress ? (
                              <span>OS: {device.ipAddress}</span>
                            ) : (
                              <span>OS: —</span>
                            )}
                          </div>
                          {device.iloAddress ? (
                            <div className="text-primary">iLO: {device.iloAddress}</div>
                          ) : null}
                        </TableCell>
                        <TableCell>
                          <Badge variant="secondary" className="font-mono text-xs">
                            {device.deviceType}
                          </Badge>
                        </TableCell>
                        <TableCell onClick={(e) => e.stopPropagation()}>
                          <label className="flex items-center gap-2 cursor-pointer text-xs font-medium select-none">
                            <Checkbox
                              aria-label={`Toggle monitoring for ${device.hostname}`}
                              checked={Boolean(device.monitor)}
                              disabled={!isAdmin || deviceMutations.update.isPending}
                              onCheckedChange={() =>
                                isAdmin &&
                                deviceMutations.update.mutate({
                                  id: device._id,
                                  payload: { monitor: !device.monitor },
                                })
                              }
                            />
                            <Badge
                              variant={device.monitor ? 'success' : 'outline'}
                              className="text-[11px] px-1.5 py-0"
                            >
                              {device.monitor ? 'Monitored' : 'Disabled'}
                            </Badge>
                          </label>
                        </TableCell>
                        <TableCell>
                          <StatusBadge status={device.status} pulse={false} />
                        </TableCell>
                        <TableCell className="mono text-xs">
                          {formatMs(device.responseTime)}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {formatRelative(device.lastCheckedAt || device.lastSeen)}
                        </TableCell>
                        <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                          <Button
                            type="button"
                            size="sm"
                            variant="secondary"
                            onClick={() => handleOpenDrawer(device._id)}
                          >
                            <Cpu className="h-3.5 w-3.5" />
                            Hardware Health
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              {pagination.totalPages > 1 && (
                <div className="pt-2">
                  <PaginationControls
                    page={pagination.page}
                    totalPages={pagination.totalPages}
                    total={pagination.total}
                    limit={pagination.limit}
                    onPageChange={pagination.setPage}
                    onLimitChange={pagination.setLimit}
                    unitLabel="Servers"
                  />
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <DeviceDrawer
        deviceId={drawerDeviceId}
        open={Boolean(drawerDeviceId)}
        onOpenChange={handleCloseDrawer}
      />
    </div>
  )
}
