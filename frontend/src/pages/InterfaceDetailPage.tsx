import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  ArrowLeft,
  Network,
  RefreshCw,
  Activity,
  ArrowDownToLine,
  ArrowUpFromLine,
  AlertTriangle,
  Radio,
} from 'lucide-react'
import { useAuth } from '@/auth/AuthContext'
import {
  InterfaceStatusBadge,
  PortClassificationBadges,
  PortModeBadge,
  UtilizationBar,
  formatAllowedVlans,
  neighborRemotePort,
} from '@/components/interfaces/InterfaceStatusBadge'
import { EmptyState } from '@/components/shared/EmptyState'
import { ErrorState } from '@/components/shared/ErrorState'
import { KpiCard } from '@/components/shared/KpiCard'
import { LoadingState, TableSkeleton } from '@/components/shared/LoadingState'
import { PageHeader } from '@/components/shared/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  useDeviceInterfaceStatsQuery,
  useDeviceInterfacesQuery,
  useInterfaceHistoryQuery,
  useInterfaceMutations,
} from '@/hooks/queries'
import {
  formatBytes,
  formatDateTime,
  formatPackets,
  formatPercent,
  formatRelative,
  formatSpeedBps,
  formatUtilization,
} from '@/utils/format'
import { interfaceNamesMatch } from '@/utils/interfaceNames'

const HISTORY_LIMITS: Record<string, number> = {
  '50': 50,
  '100': 100,
  '250': 250,
  '500': 500,
}

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean
  payload?: Array<{ name?: string; value?: number; color?: string }>
  label?: string
}) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-border bg-popover px-3 py-2 text-xs shadow-xl">
      <p className="mb-1 font-medium text-foreground">{formatDateTime(label)}</p>
      {payload.map((entry) => (
        <p key={entry.name} style={{ color: entry.color }} className="mono">
          {entry.name}:{' '}
          {String(entry.name ?? '').toLowerCase().includes('util') ||
          String(entry.name ?? '').includes('%')
            ? formatUtilization(entry.value)
            : formatPackets(entry.value)}
        </p>
      ))}
    </div>
  )
}

export function InterfaceDetailPage() {
  const navigate = useNavigate()
  const { deviceId = '', interfaceName: rawName = '' } = useParams()
  const interfaceName = decodeURIComponent(rawName)
  const { isAdmin } = useAuth()
  const mutations = useInterfaceMutations()
  const [historyLimit, setHistoryLimit] = useState('100')

  const inventoryQuery = useDeviceInterfacesQuery(
    deviceId,
    { limit: 1000 },
    Boolean(deviceId),
  )
  const statsQuery = useDeviceInterfaceStatsQuery(deviceId, Boolean(deviceId))
  const historyQuery = useInterfaceHistoryQuery(
    deviceId,
    interfaceName,
    { limit: HISTORY_LIMITS[historyLimit] ?? 100 },
    Boolean(deviceId && interfaceName),
  )

  const iface = useMemo(() => {
    const list = inventoryQuery.data?.data ?? []
    return (
      list.find((row) => row.name === interfaceName) ||
      list.find((row) => interfaceNamesMatch(row.name, interfaceName)) ||
      null
    )
  }, [inventoryQuery.data, interfaceName])

  const latestStat = useMemo(() => {
    const list = statsQuery.data ?? []
    return (
      list.find((row) => row.interfaceName === interfaceName) ||
      list.find((row) => interfaceNamesMatch(row.interfaceName, interfaceName)) ||
      historyQuery.data?.data?.[0] ||
      null
    )
  }, [statsQuery.data, historyQuery.data, interfaceName])

  const chartData = useMemo(() => {
    const history = [...(historyQuery.data?.data ?? [])].reverse()
    return history.map((sample) => ({
      timestamp: sample.timestamp,
      utilization: sample.utilization ?? null,
      rxUtilization: sample.rxUtilization ?? null,
      txUtilization: sample.txUtilization ?? null,
      broadcastPackets: sample.broadcastPackets,
      multicastPackets: sample.multicastPackets,
      inputErrors: sample.inputErrors,
      outputErrors: sample.outputErrors,
      discards: sample.discards,
      rxBytes: sample.rxBytes,
      txBytes: sample.txBytes,
    }))
  }, [historyQuery.data])

  const hostname =
    iface?.hostname ||
    latestStat?.hostname ||
    inventoryQuery.data?.hostname ||
    '—'
  const ipAddress =
    iface?.ipAddress ||
    latestStat?.ipAddress ||
    inventoryQuery.data?.ipAddress ||
    '—'

  const isLoading =
    (inventoryQuery.isLoading && !iface) ||
    (historyQuery.isLoading && chartData.length === 0 && !latestStat)

  const loadError =
    (inventoryQuery.error && !iface) ||
    (historyQuery.error && chartData.length === 0 && !latestStat)
      ? inventoryQuery.error || historyQuery.error
      : null

  if (!deviceId || !interfaceName) {
    return (
      <EmptyState
        title="Interface not specified"
        description="Choose an interface from the Interfaces list."
        actionLabel="Back to Interfaces"
        onAction={() => navigate('/interfaces')}
      />
    )
  }

  if (isLoading) {
    return <LoadingState label="Loading interface details…" />
  }

  if (loadError && !iface && !latestStat) {
    return (
      <ErrorState
        message={loadError instanceof Error ? loadError.message : 'Failed to load interface'}
        onRetry={() => {
          void inventoryQuery.refetch()
          void statsQuery.refetch()
          void historyQuery.refetch()
        }}
      />
    )
  }

  const totalErrors =
    (latestStat?.inputErrors ?? 0) +
    (latestStat?.outputErrors ?? 0) +
    (latestStat?.discards ?? 0)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="ghost" size="sm" asChild>
          <Link to="/interfaces">
            <ArrowLeft className="mr-1.5 h-4 w-4" />
            Interfaces
          </Link>
        </Button>
        <Button type="button" variant="ghost" size="sm" asChild>
          <Link to={`/devices/${deviceId}`}>Open device</Link>
        </Button>
      </div>

      <PageHeader
        title={interfaceName}
        description={`${hostname} · ${ipAddress}${iface?.description ? ` · ${iface.description}` : ''}`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {isAdmin ? (
              <Button
                type="button"
                disabled={mutations.collectDevice.isPending}
                onClick={() => mutations.collectDevice.mutate(deviceId)}
              >
                <RefreshCw className="mr-2 h-4 w-4" />
                {mutations.collectDevice.isPending ? 'Collecting…' : 'Collect stats'}
              </Button>
            ) : null}
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                void statsQuery.refetch()
                void historyQuery.refetch()
                void inventoryQuery.refetch()
              }}
            >
              Refresh
            </Button>
          </div>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <InterfaceStatusBadge status={iface?.operStatus || 'unknown'} />
        <InterfaceStatusBadge status={iface?.adminStatus || 'unknown'} kind="admin" />
        <PortModeBadge mode={iface?.portMode || iface?.mode || 'unknown'} />
        {iface ? <PortClassificationBadges iface={iface} /> : null}
        {iface?.accessVlan != null ? (
          <Badge variant="secondary">Access VLAN {iface.accessVlan}</Badge>
        ) : null}
        {iface?.voiceVlan != null ? (
          <Badge variant="secondary">Voice VLAN {iface.voiceVlan}</Badge>
        ) : null}
        {iface?.nativeVlan != null ? (
          <Badge variant="outline">Native VLAN {iface.nativeVlan}</Badge>
        ) : null}
        {iface?.monitoringEnabled === false ? (
          <Badge variant="outline" className="border-muted-foreground/40 text-muted-foreground">
            monitoring off
          </Badge>
        ) : null}
        {latestStat?.collectionMethod ? (
          <Badge variant="muted" className="uppercase">
            via {latestStat.collectionMethod}
          </Badge>
        ) : null}
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label="Utilization"
          value={formatUtilization(latestStat?.utilization)}
          icon={Activity}
          tone={
            (latestStat?.utilization ?? 0) >= 85
              ? 'danger'
              : (latestStat?.utilization ?? 0) >= 60
                ? 'warning'
                : 'success'
          }
          hint={
            latestStat
              ? `RX ${formatUtilization(latestStat.rxUtilization)} · TX ${formatUtilization(latestStat.txUtilization)}`
              : 'No samples yet'
          }
        />
        <KpiCard
          label="RX / TX bytes"
          value={`${formatBytes(latestStat?.rxBytes)} / ${formatBytes(latestStat?.txBytes)}`}
          icon={ArrowDownToLine}
          tone="accent"
          hint={formatSpeedBps(latestStat?.speedBps) !== '—'
            ? `Link ${formatSpeedBps(latestStat?.speedBps)}`
            : iface?.speed
              ? `Speed ${iface.speed}`
              : undefined}
        />
        <KpiCard
          label="Broadcast / Multicast"
          value={`${formatPackets(latestStat?.broadcastPackets)} / ${formatPackets(latestStat?.multicastPackets)}`}
          icon={Radio}
          tone="default"
        />
        <KpiCard
          label="Errors + discards"
          value={formatPackets(totalErrors)}
          icon={AlertTriangle}
          tone={totalErrors > 0 ? 'danger' : 'success'}
          hint={
            latestStat
              ? `In ${formatPackets(latestStat.inputErrors)} · Out ${formatPackets(latestStat.outputErrors)} · Drop ${formatPackets(latestStat.discards)}`
              : undefined
          }
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="glass lg:col-span-1">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Interface details</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <DetailRow label="Name" value={interfaceName} mono />
            <DetailRow label="Description" value={iface?.description || '—'} />
            <DetailRow label="Device" value={hostname} />
            <DetailRow label="IP address" value={ipAddress} mono />
            <DetailRow label="Admin status" value={iface?.adminStatus || '—'} />
            <DetailRow label="Oper status" value={iface?.operStatus || '—'} />
            <DetailRow label="Port mode" value={iface?.portMode || iface?.mode || '—'} />
            <DetailRow label="Access" value={iface?.isAccess ? 'yes' : 'no'} />
            <DetailRow label="Trunk" value={iface?.isTrunk ? 'yes' : 'no'} />
            <DetailRow label="Uplink" value={iface?.isUplink ? 'yes' : 'no'} />
            <DetailRow label="Infrastructure" value={iface?.isInfrastructure ? 'yes' : 'no'} />
            <DetailRow label="Management" value={iface?.isManagement ? 'yes' : 'no'} />
            <DetailRow label="Protected" value={iface?.isProtected ? 'yes' : 'no'} />
            <DetailRow
              label="Monitoring"
              value={iface?.monitoringEnabled === false ? 'disabled' : 'enabled'}
            />
            <DetailRow
              label="Access VLAN"
              value={iface?.accessVlan != null ? String(iface.accessVlan) : '—'}
              mono
            />
            <DetailRow
              label="Voice VLAN"
              value={iface?.voiceVlan != null ? String(iface.voiceVlan) : '—'}
              mono
            />
            <DetailRow
              label="Native VLAN"
              value={iface?.nativeVlan != null ? String(iface.nativeVlan) : '—'}
              mono
            />
            <DetailRow
              label="Allowed VLANs"
              value={formatAllowedVlans(iface?.allowedVlans, 20)}
              mono
            />
            <DetailRow
              label="Speed"
              value={
                iface?.speedMbps != null
                  ? `${iface.speedMbps} Mbps`
                  : iface?.speed || '—'
              }
              mono
            />
            <DetailRow label="Duplex" value={iface?.duplex || '—'} />
            <DetailRow
              label="Neighbor"
              value={
                iface?.neighbor?.hostname
                  ? `${iface.neighbor.hostname}${
                      neighborRemotePort(iface.neighbor)
                        ? ` · ${neighborRemotePort(iface.neighbor)}`
                        : ''
                    }`
                  : '—'
              }
            />
            <DetailRow
              label="Neighbor device type"
              value={iface?.neighbor?.deviceType || '—'}
            />
            <DetailRow
              label="Neighbor platform"
              value={iface?.neighbor?.platform || '—'}
            />
            <DetailRow
              label="Neighbor IP"
              value={iface?.neighbor?.ip || '—'}
              mono
            />
            <DetailRow
              label="Management address"
              value={iface?.neighbor?.managementAddress || '—'}
              mono
            />
            <DetailRow
              label="Neighbor protocol"
              value={iface?.neighbor?.protocol || '—'}
            />
            <DetailRow
              label="Capabilities"
              value={
                iface?.neighbor?.capabilities?.length
                  ? iface.neighbor.capabilities.join(', ')
                  : '—'
              }
            />
            <DetailRow
              label="System description"
              value={iface?.neighbor?.systemDescription || '—'}
            />
            <DetailRow label="Vendor" value={iface?.vendor || '—'} />
            <DetailRow
              label="Last inventory update"
              value={formatRelative(iface?.lastUpdated)}
            />
            <DetailRow
              label="Last stats sample"
              value={formatRelative(latestStat?.timestamp)}
            />
            <div className="pt-2">
              <p className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">
                Current utilization
              </p>
              <UtilizationBar value={latestStat?.utilization} />
            </div>
          </CardContent>
        </Card>

        <Card className="glass lg:col-span-2">
          <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3 pb-2">
            <CardTitle className="text-base">Utilization history</CardTitle>
            <Select value={historyLimit} onValueChange={setHistoryLimit}>
              <SelectTrigger className="w-[130px]">
                <SelectValue placeholder="Samples" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="50">Last 50</SelectItem>
                <SelectItem value="100">Last 100</SelectItem>
                <SelectItem value="250">Last 250</SelectItem>
                <SelectItem value="500">Last 500</SelectItem>
              </SelectContent>
            </Select>
          </CardHeader>
          <CardContent>
            {historyQuery.isLoading && chartData.length === 0 ? (
              <div className="flex h-64 items-center justify-center">
                <LoadingState label="Loading history…" />
              </div>
            ) : historyQuery.error && chartData.length === 0 ? (
              <ErrorState
                message={
                  historyQuery.error instanceof Error
                    ? historyQuery.error.message
                    : 'Failed to load history'
                }
                onRetry={() => void historyQuery.refetch()}
              />
            ) : chartData.length === 0 ? (
              <EmptyState
                icon={Network}
                title="No traffic history yet"
                description="Statistics appear after the interface stats collector runs (SNMP or SSH)."
                actionLabel={isAdmin ? 'Collect now' : undefined}
                onAction={
                  isAdmin ? () => mutations.collectDevice.mutate(deviceId) : undefined
                }
              />
            ) : (
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={chartData}>
                    <defs>
                      <linearGradient id="utilFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#3B82F6" stopOpacity={0.35} />
                        <stop offset="100%" stopColor="#3B82F6" stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
                    <XAxis
                      dataKey="timestamp"
                      tick={{ fill: '#94a3b8', fontSize: 10 }}
                      tickFormatter={(value) => formatDateTime(String(value)).slice(5, 16)}
                      minTickGap={28}
                    />
                    <YAxis
                      tick={{ fill: '#94a3b8', fontSize: 11 }}
                      unit="%"
                      width={42}
                      domain={[0, 'auto']}
                    />
                    <Tooltip content={<ChartTooltip />} />
                    <Legend />
                    <Area
                      type="monotone"
                      dataKey="utilization"
                      name="Utilization"
                      stroke="#3B82F6"
                      fill="url(#utilFill)"
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="rxUtilization"
                      name="RX %"
                      stroke="#22C55E"
                      strokeWidth={1.5}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="txUtilization"
                      name="TX %"
                      stroke="#F59E0B"
                      strokeWidth={1.5}
                      dot={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="glass">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Broadcast / multicast</CardTitle>
          </CardHeader>
          <CardContent>
            {chartData.length === 0 ? (
              <EmptyState title="No samples" description="Collect stats to populate this chart." />
            ) : (
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                    <XAxis
                      dataKey="timestamp"
                      tick={{ fill: '#94a3b8', fontSize: 10 }}
                      tickFormatter={(value) => formatDateTime(String(value)).slice(5, 16)}
                      minTickGap={28}
                    />
                    <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} width={48} />
                    <Tooltip content={<ChartTooltip />} />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="broadcastPackets"
                      name="Broadcast"
                      stroke="#8B5CF6"
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="multicastPackets"
                      name="Multicast"
                      stroke="#06B6D4"
                      strokeWidth={2}
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="glass">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Errors & discards</CardTitle>
          </CardHeader>
          <CardContent>
            {chartData.length === 0 ? (
              <EmptyState title="No samples" description="Collect stats to populate this chart." />
            ) : (
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                    <XAxis
                      dataKey="timestamp"
                      tick={{ fill: '#94a3b8', fontSize: 10 }}
                      tickFormatter={(value) => formatDateTime(String(value)).slice(5, 16)}
                      minTickGap={28}
                    />
                    <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} width={48} />
                    <Tooltip content={<ChartTooltip />} />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="inputErrors"
                      name="Input errors"
                      stroke="#EF4444"
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="outputErrors"
                      name="Output errors"
                      stroke="#F97316"
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="discards"
                      name="Discards"
                      stroke="#94A3B8"
                      strokeWidth={2}
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="glass overflow-hidden">
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <ArrowUpFromLine className="h-4 w-4 text-muted-foreground" />
            Recent statistics
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {historyQuery.isLoading && !historyQuery.data ? (
            <TableSkeleton rows={6} />
          ) : (historyQuery.data?.data?.length ?? 0) === 0 ? (
            <EmptyState
              title="No statistics samples"
              description="Historical counters are stored each time the collector polls this interface."
            />
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader className="sticky top-0 z-10 bg-card/95 backdrop-blur">
                  <TableRow>
                    <TableHead>Timestamp</TableHead>
                    <TableHead>Util</TableHead>
                    <TableHead>RX bytes</TableHead>
                    <TableHead>TX bytes</TableHead>
                    <TableHead className="hidden md:table-cell">Broadcast</TableHead>
                    <TableHead className="hidden md:table-cell">Multicast</TableHead>
                    <TableHead>Errors</TableHead>
                    <TableHead className="hidden sm:table-cell">Method</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(historyQuery.data?.data ?? []).slice(0, 25).map((sample) => (
                    <TableRow key={sample._id}>
                      <TableCell className="text-muted-foreground">
                        {formatDateTime(sample.timestamp)}
                      </TableCell>
                      <TableCell className="mono">
                        {formatPercent(sample.utilization)}
                      </TableCell>
                      <TableCell className="mono text-muted-foreground">
                        {formatBytes(sample.rxBytes)}
                      </TableCell>
                      <TableCell className="mono text-muted-foreground">
                        {formatBytes(sample.txBytes)}
                      </TableCell>
                      <TableCell className="hidden mono text-muted-foreground md:table-cell">
                        {formatPackets(sample.broadcastPackets)}
                      </TableCell>
                      <TableCell className="hidden mono text-muted-foreground md:table-cell">
                        {formatPackets(sample.multicastPackets)}
                      </TableCell>
                      <TableCell className="mono">
                        {formatPackets(
                          sample.inputErrors + sample.outputErrors + sample.discards,
                        )}
                      </TableCell>
                      <TableCell className="hidden uppercase text-muted-foreground sm:table-cell">
                        {sample.collectionMethod}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function DetailRow({
  label,
  value,
  mono,
}: {
  label: string
  value: string
  mono?: boolean
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border/50 pb-2 last:border-0 last:pb-0">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className={`text-right font-medium ${mono ? 'mono' : ''}`}>{value}</span>
    </div>
  )
}
