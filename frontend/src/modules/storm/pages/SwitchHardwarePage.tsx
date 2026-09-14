import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  Cpu,
  RefreshCw,
  Server,
  Thermometer,
} from 'lucide-react'
import { getDevices } from '@/api'
import {
  formatHardwareValue,
  HardwareHealthBadge,
} from '@/modules/storm/components/hardware/hardwareStatus'
import { isManagedSwitch } from '@/modules/storm/components/stormShared'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { KpiCard } from '@/shared/components/KpiCard'
import { TableSkeleton } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { useAuth } from '@/shared/auth/AuthContext'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Checkbox } from '@/shared/ui/checkbox'
import { Input } from '@/shared/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import {
  useBatchedSwitchHardware,
  useSettingsMutation,
  useSettingsQuery,
} from '@/hooks/queries'
import { useClientPagination } from '@/hooks/useClientPagination'
import type { Device } from '@/types'
import { fetchAllListPages } from '@/utils/fetchAllPages'
import { formatDateTime, formatRelative } from '@/utils/format'
import type { SwitchHardwareCurrent } from '@/api/switchHardwareService'

function isCiscoCandidate(device: Device): boolean {
  if (!isManagedSwitch(device.deviceType)) return false
  const vendor = (device.vendor || '').toLowerCase()
  const sshVendor = (device.credentials?.sshVendor || '').toLowerCase()
  if (vendor.includes('cisco') || sshVendor.includes('cisco')) return true
  // Include monitored switches with credentials when vendor is unknown.
  return Boolean(device.credentials) || !vendor
}

function healthBucket(status: string | null | undefined): string {
  const value = (status || 'unknown').toLowerCase()
  if (value === 'healthy') return 'healthy'
  if (value === 'warning') return 'warning'
  if (value === 'critical') return 'critical'
  if (value === 'not_available') return 'unavailable'
  return 'unknown'
}

type FleetRow = {
  device: Device
  hardware: SwitchHardwareCurrent | null
}

export function SwitchHardwarePage() {
  const navigate = useNavigate()
  const { isAdmin } = useAuth()
  const settingsQuery = useSettingsQuery(true)
  const settingsMutation = useSettingsMutation()
  const monitoringEnabled = Boolean(settingsQuery.data?.switchHardwareMonitoringEnabled)
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [healthFilter, setHealthFilter] = useState('all')
  const [platformFilter, setPlatformFilter] = useState('all')

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 300)
    return () => window.clearTimeout(timer)
  }, [query])

  const toggleMonitoring = (enabled: boolean) => {
    settingsMutation.mutate({ switchHardwareMonitoringEnabled: enabled })
  }

  const devicesQuery = useQuery({
    queryKey: ['devices', 'inventory', 'switch', 'hardware'],
    queryFn: () =>
      fetchAllListPages((page, limit) => getDevices({ page, limit, deviceType: 'switch' })),
    refetchInterval: 30_000,
    staleTime: 10_000,
  })

  const switchDevices = useMemo(
    () => (devicesQuery.data?.data ?? []).filter(isCiscoCandidate),
    [devicesQuery.data],
  )

  const deviceIds = useMemo(() => switchDevices.map((d) => d._id), [switchDevices])
  const hardwareQuery = useBatchedSwitchHardware(deviceIds)

  const rows: FleetRow[] = useMemo(
    () =>
      switchDevices.map((device) => ({
        device,
        hardware: hardwareQuery.data?.get(device._id) ?? null,
      })),
    [switchDevices, hardwareQuery.data],
  )

  const platforms = useMemo(() => {
    const set = new Set<string>()
    rows.forEach((row) => {
      const platform = row.hardware?.platform
      if (platform) set.add(platform)
    })
    return [...set].sort()
  }, [rows])

  const filtered = useMemo(() => {
    const q = debouncedQuery.trim().toLowerCase()
    return rows.filter(({ device, hardware }) => {
      if (q) {
        const haystack = `${device.hostname} ${device.ipAddress} ${hardware?.inventory?.model || ''}`.toLowerCase()
        if (!haystack.includes(q)) return false
      }
      if (healthFilter !== 'all') {
        const bucket = healthBucket(hardware?.overallHealth)
        if (bucket !== healthFilter) return false
      }
      if (platformFilter !== 'all' && (hardware?.platform || '') !== platformFilter) {
        return false
      }
      return true
    })
  }, [rows, debouncedQuery, healthFilter, platformFilter])

  const pagination = useClientPagination(filtered, 10)

  const kpis = useMemo(() => {
    let healthy = 0
    let warning = 0
    let critical = 0
    let unknown = 0
    let activeAlerts = 0
    rows.forEach(({ hardware }) => {
      const bucket = healthBucket(hardware?.overallHealth)
      if (bucket === 'healthy') healthy += 1
      else if (bucket === 'warning') warning += 1
      else if (bucket === 'critical') critical += 1
      else unknown += 1
      if ((hardware?.hardwareAlarms || []).length > 0) activeAlerts += 1
    })
    return {
      total: rows.length,
      healthy,
      warning,
      critical,
      unknown,
      activeAlerts,
    }
  }, [rows])

  const loading = devicesQuery.isLoading || (deviceIds.length > 0 && hardwareQuery.isLoading)
  const error = devicesQuery.error

  return (
    <div className="np-page space-y-6">
      <PageHeader
        title="Hardware Health"
        description="Cisco switch chassis health — temperature, fans, power supplies, CPU, memory, and outage evidence."
        actions={
          <div className="flex flex-wrap items-center gap-3">
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
              onClick={() => {
                void devicesQuery.refetch()
                void hardwareQuery.refetch()
              }}
            >
              <RefreshCw className="h-4 w-4" />
              Refresh
            </Button>
          </div>
        }
      />

      {!monitoringEnabled ? (
        <div className="rounded-xl border border-warning/40 bg-warning/10 px-4 py-3 text-sm">
          <p className="font-medium text-foreground">Hardware monitoring is disabled</p>
          <p className="mt-1 text-muted-foreground">
            Scheduled collection and Collect Now stay idle until an admin enables monitoring.
            {isAdmin
              ? ' Use the toggle above to turn it on.'
              : ' Ask an administrator to enable it.'}
          </p>
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
        <KpiCard label="Total Switches" value={kpis.total} icon={Server} loading={loading} />
        <KpiCard label="Healthy" value={kpis.healthy} tone="success" icon={Cpu} loading={loading} />
        <KpiCard label="Warning" value={kpis.warning} tone="warning" icon={Thermometer} loading={loading} />
        <KpiCard label="Critical" value={kpis.critical} tone="danger" icon={AlertTriangle} loading={loading} />
        <KpiCard label="Unknown / N/A" value={kpis.unknown} loading={loading} />
        <KpiCard label="Active Alarms" value={kpis.activeAlerts} tone="warning" loading={loading} />
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search name, IP, or model…"
          className="max-w-sm"
        />
        <Select value={healthFilter} onValueChange={setHealthFilter}>
          <SelectTrigger className="w-[160px]">
            <SelectValue placeholder="Health" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All health</SelectItem>
            <SelectItem value="healthy">Healthy</SelectItem>
            <SelectItem value="warning">Warning</SelectItem>
            <SelectItem value="critical">Critical</SelectItem>
            <SelectItem value="unknown">Unknown</SelectItem>
            <SelectItem value="unavailable">Not available</SelectItem>
          </SelectContent>
        </Select>
        <Select value={platformFilter} onValueChange={setPlatformFilter}>
          <SelectTrigger className="w-[160px]">
            <SelectValue placeholder="Platform" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All platforms</SelectItem>
            {platforms.map((platform) => (
              <SelectItem key={platform} value={platform}>
                {platform}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {error ? (
        <ErrorState
          title="Unable to load hardware monitoring data"
          message={error instanceof Error ? error.message : 'Request failed'}
          onRetry={() => void devicesQuery.refetch()}
        />
      ) : loading ? (
        <TableSkeleton rows={8} />
      ) : filtered.length === 0 ? (
        <EmptyState
          title="No hardware data"
          description="No eligible Cisco switches matched the current filters. Hardware monitoring must be enabled and switches need credentials."
          action={
            <Button asChild variant="secondary" size="sm">
              <Link to="/switches">Open Switches</Link>
            </Button>
          }
        />
      ) : (
        <div className="space-y-4">
          <div className="overflow-x-auto rounded-xl border border-border/70">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Switch</TableHead>
                  <TableHead>IP</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead>Platform</TableHead>
                  <TableHead>Health</TableHead>
                  <TableHead>Temp</TableHead>
                  <TableHead>Fans</TableHead>
                  <TableHead>PSU</TableHead>
                  <TableHead>CPU</TableHead>
                  <TableHead>Memory</TableHead>
                  <TableHead>Last collection</TableHead>
                  <TableHead>Reachability</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pagination.pageItems.map(({ device, hardware }) => (
                  <TableRow
                    key={device._id}
                    className="cursor-pointer"
                    onClick={() => navigate(`/switches/${device._id}/hardware`)}
                  >
                    <TableCell className="font-medium">{device.hostname}</TableCell>
                    <TableCell className="mono text-sm">{device.ipAddress}</TableCell>
                    <TableCell>{formatHardwareValue(hardware?.inventory?.model)}</TableCell>
                    <TableCell>{formatHardwareValue(hardware?.platform)}</TableCell>
                    <TableCell>
                      <HardwareHealthBadge status={hardware?.overallHealth} />
                    </TableCell>
                    <TableCell>
                      <HardwareHealthBadge status={hardware?.temperature?.status} />
                    </TableCell>
                    <TableCell>
                      {hardware?.fans?.failedCount
                        ? `${hardware.fans.failedCount} failed`
                        : formatHardwareValue(hardware?.fans?.count)}
                    </TableCell>
                    <TableCell>
                      {hardware?.powerSupplies?.failedCount
                        ? `${hardware.powerSupplies.failedCount} failed`
                        : formatHardwareValue(hardware?.powerSupplies?.count)}
                    </TableCell>
                    <TableCell className="mono">
                      {formatHardwareValue(hardware?.cpu?.utilizationPercent, '%')}
                    </TableCell>
                    <TableCell className="mono">
                      {formatHardwareValue(hardware?.memory?.utilizationPercent, '%')}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {hardware?.lastSuccessfulCollectionAt
                        ? formatRelative(hardware.lastSuccessfulCollectionAt)
                        : hardware?.lastAttemptedCollectionAt
                          ? `Attempted ${formatDateTime(hardware.lastAttemptedCollectionAt)}`
                          : 'Not available'}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={device.status} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <PaginationControls
            page={pagination.page}
            totalPages={pagination.totalPages}
            total={filtered.length}
            limit={pagination.limit}
            onPageChange={pagination.setPage}
            onLimitChange={pagination.setLimit}
            limitOptions={[10, 25, 50]}
          />
        </div>
      )}
    </div>
  )
}
