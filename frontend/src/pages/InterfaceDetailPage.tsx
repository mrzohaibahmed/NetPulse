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
  Tooltip as RechartsTooltip,
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
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import {
  useDeviceInterfaceStatsQuery,
  useDeviceInterfacesQuery,
  useInterfaceHistoryQuery,
  useInterfaceMutations,
  useInterfaceRiskQuery,
  useStormIncidentsQuery,
  useMitigationDetailQuery,
  useRecoveryDetailQuery,
  useManualPortControlMutations,
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
import type { StormSourceClassification } from '@/types'

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

const SOURCE_CLASSIFICATION_LABELS: Record<string, string> = {
  LIKELY_SOURCE: 'Likely Source',
  LIKELY_FORWARDER: 'Likely Forwarder',
  LIKELY_RECEIVER: 'Likely Receiver',
  UNKNOWN: 'Unknown',
}

function formatDirectionalPackets(
  rx?: number | null,
  tx?: number | null,
  combined?: number | null,
) {
  if (rx != null || tx != null) {
    return `RX ${formatPackets(rx ?? 0)} · TX ${formatPackets(tx ?? 0)}`
  }
  return formatPackets(combined ?? 0)
}

function sourceBadgeVariant(
  classification?: StormSourceClassification | string | null,
): 'default' | 'secondary' | 'outline' | 'danger' {
  switch (classification) {
    case 'LIKELY_SOURCE':
      return 'danger'
    case 'LIKELY_FORWARDER':
      return 'secondary'
    case 'LIKELY_RECEIVER':
      return 'outline'
    default:
      return 'default'
  }
}

export function InterfaceDetailPage() {
  const navigate = useNavigate()
  const { deviceId = '', interfaceName: rawName = '' } = useParams()
  const interfaceName = decodeURIComponent(rawName)
  const { user, isAdmin, isSuperAdmin } = useAuth()
  const mutations = useInterfaceMutations()
  const [historyLimit, setHistoryLimit] = useState('100')

  const manualMutations = useManualPortControlMutations()
  const stormIncidentsQuery = useStormIncidentsQuery({
    limit: 50,
    deviceId,
    q: interfaceName,
  })

  const latestIncident = useMemo(() => {
    const rows = stormIncidentsQuery.data?.data ?? []
    return rows[0] ?? null
  }, [stormIncidentsQuery.data])

  const canSafeShutdown = user?.role === 'admin' || user?.role === 'operator' || isSuperAdmin
  const canSafeRecover = isAdmin
  const canEmergencyShutdown = isSuperAdmin

  const latestRecoverableIncident = useMemo(() => {
    const rows = stormIncidentsQuery.data?.data ?? []
    return (
      rows.find((row) => {
        const statusOk = ['MITIGATED', 'RECOVERY_FAILED', 'MITIGATION_FAILED'].includes(
          row.status || '',
        )
        if (!statusOk) return false
        if (row.deviceId && deviceId && row.deviceId !== deviceId) return false
        return interfaceNamesMatch(row.interface || '', interfaceName)
      }) ?? null
    )
  }, [stormIncidentsQuery.data, deviceId, interfaceName])

  const mitigationDetailQuery = useMitigationDetailQuery(
    latestIncident?.incidentId || '',
    Boolean(latestIncident?.incidentId),
  )
  const recoveryDetailQuery = useRecoveryDetailQuery(
    latestIncident?.incidentId || '',
    Boolean(latestIncident?.incidentId),
  )

  const [shutdownDialogOpen, setShutdownDialogOpen] = useState(false)
  const [shutdownReason, setShutdownReason] = useState('')

  const [recoverDialogOpen, setRecoverDialogOpen] = useState(false)
  const [recoverReason, setRecoverReason] = useState('')

  const [emergencyDialogOpen, setEmergencyDialogOpen] = useState(false)
  const [emergencyReason, setEmergencyReason] = useState('')
  const [emergencyConfirmText, setEmergencyConfirmText] = useState('')

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
  const riskQuery = useInterfaceRiskQuery(deviceId, interfaceName, Boolean(deviceId && interfaceName))
  const latestRisk = riskQuery.data?.data ?? null

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
      rxBroadcastPackets: sample.rxBroadcastPackets ?? null,
      txBroadcastPackets: sample.txBroadcastPackets ?? null,
      rxMulticastPackets: sample.rxMulticastPackets ?? null,
      txMulticastPackets: sample.txMulticastPackets ?? null,
      inputErrors: sample.inputErrors,
      outputErrors: sample.outputErrors,
      discards: sample.discards,
      rxDiscards: sample.rxDiscards ?? null,
      txDiscards: sample.txDiscards ?? null,
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
    (latestStat?.outputErrors ?? 0)

  const totalDiscards =
    latestStat?.discards ??
    (latestStat?.rxDiscards ?? 0) + (latestStat?.txDiscards ?? 0)

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

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
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
          label="Broadcast"
          value={formatDirectionalPackets(
            latestStat?.rxBroadcastPackets,
            latestStat?.txBroadcastPackets,
            latestStat?.broadcastPackets,
          )}
          icon={Radio}
          tone="default"
          hint={
            latestStat?.broadcastPackets != null
              ? `Combined ${formatPackets(latestStat.broadcastPackets)}`
              : undefined
          }
        />
        <KpiCard
          label="Multicast"
          value={formatDirectionalPackets(
            latestStat?.rxMulticastPackets,
            latestStat?.txMulticastPackets,
            latestStat?.multicastPackets,
          )}
          icon={Radio}
          tone="default"
          hint={
            latestStat?.multicastPackets != null
              ? `Combined ${formatPackets(latestStat.multicastPackets)}`
              : undefined
          }
        />
        <KpiCard
          label="Discards"
          value={formatDirectionalPackets(
            latestStat?.rxDiscards,
            latestStat?.txDiscards,
            latestStat?.discards,
          )}
          icon={AlertTriangle}
          tone={totalDiscards > 0 ? 'danger' : 'success'}
        />
        <KpiCard
          label="Errors"
          value={formatPackets(totalErrors)}
          icon={AlertTriangle}
          tone={totalErrors > 0 ? 'danger' : 'success'}
          hint={
            latestStat
              ? `In ${formatPackets(latestStat.inputErrors)} · Out ${formatPackets(latestStat.outputErrors)}`
              : undefined
          }
        />
      </div>

      <Card className="glass">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Storm Source Analysis</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-4">
          {riskQuery.isLoading && !latestRisk ? (
            <LoadingState label="Loading risk analysis…" />
          ) : !latestRisk ? (
            <p className="text-sm text-muted-foreground">
              No risk history yet. Storm scoring runs on the scheduler after stats collection.
            </p>
          ) : (
            <>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Badge variant={sourceBadgeVariant(latestRisk.sourceClassification)}>
                      {SOURCE_CLASSIFICATION_LABELS[latestRisk.sourceClassification || 'UNKNOWN'] ||
                        'Unknown'}
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-sm">
                    <p className="text-xs">
                      {latestRisk.sourceRationale ||
                        'Classification uses RX/TX broadcast and multicast rates, port mode, neighbor type, and topology role.'}
                    </p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <div className="text-sm">
                <span className="text-muted-foreground">Confidence:</span>{' '}
                <span className="font-mono font-medium">
                  {(latestRisk.sourceConfidence ?? 0).toFixed(0)}%
                </span>
              </div>
              <div className="text-sm text-muted-foreground">
                Risk score {latestRisk.riskScore.toFixed(1)} · {latestRisk.severity}
              </div>
            </>
          )}
        </CardContent>
      </Card>

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
                    <RechartsTooltip content={<ChartTooltip />} />
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
        <Card className="glass lg:col-span-2">
          <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3 pb-3">
            <div className="space-y-1">
              <CardTitle className="text-base">Storm Diagnostics & Manual Control</CardTitle>
              <p className="text-sm text-muted-foreground">
                Safe Shutdown / Safe Recover (admin & super-admin) and emergency shutdown (Super Admin).
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {canSafeShutdown ? (
                <Button
                  type="button"
                  variant="destructive"
                  disabled={manualMutations.shutdown.isPending}
                  onClick={() => {
                    setShutdownReason('')
                    setShutdownDialogOpen(true)
                  }}
                >
                  {manualMutations.shutdown.isPending ? 'Shutting down…' : 'Shutdown Port'}
                </Button>
              ) : null}

              {canSafeRecover ? (
                <Button
                  type="button"
                  variant="secondary"
                  disabled={manualMutations.recover.isPending}
                  onClick={() => {
                    setRecoverReason('')
                    setRecoverDialogOpen(true)
                  }}
                >
                  {manualMutations.recover.isPending ? 'Recovering…' : 'Recover Port'}
                </Button>
              ) : null}

              {canEmergencyShutdown ? (
                <Button
                  type="button"
                  variant="destructive"
                  disabled={manualMutations.emergencyShutdown.isPending}
                  onClick={() => {
                    setEmergencyReason('')
                    setEmergencyConfirmText('')
                    setEmergencyDialogOpen(true)
                  }}
                >
                  {manualMutations.emergencyShutdown.isPending ? (
                    <>
                      <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
                      Emergency shutting down…
                    </>
                  ) : (
                    <>
                      <AlertTriangle className="mr-2 h-4 w-4" />
                      Emergency Shutdown
                    </>
                  )}
                </Button>
              ) : null}
            </div>
          </CardHeader>

          <CardContent className="space-y-5">
            {/* ── Shutdown confirmation ─────────────────────────── */}
            {canSafeShutdown ? (
              <AlertDialog open={shutdownDialogOpen} onOpenChange={setShutdownDialogOpen}>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Shutdown Port (Safe Manual)</AlertDialogTitle>
                    <AlertDialogDescription>
                      You are about to safely shut down this port. The backend will run the Safety Engine
                      and block the action if any precondition fails.
                    </AlertDialogDescription>
                  </AlertDialogHeader>

                  <div className="space-y-3">
                    <div className="grid gap-2 text-sm">
                      <div>
                        <span className="font-semibold">Device:</span> {hostname}
                      </div>
                      <div>
                        <span className="font-semibold">Device IP:</span> {ipAddress}
                      </div>
                      <div>
                        <span className="font-semibold">Interface:</span> {interfaceName}
                      </div>
                      <div>
                        <span className="font-semibold">Description:</span> {iface?.description || '—'}
                      </div>
                      <div>
                        <span className="font-semibold">Current status:</span>{' '}
                        {iface?.operStatus || '—'} / {iface?.adminStatus || '—'}
                      </div>
                      <div>
                        <span className="font-semibold">Classification:</span>{' '}
                        {iface?.isAccess ? 'Access' : iface?.isTrunk ? 'Trunk' : '—'}
                      </div>
                      <div>
                        <span className="font-semibold">Current risk:</span>{' '}
                        {String((latestIncident?.risk as any)?.riskScore ?? '—')}
                      </div>
                      <div>
                        <span className="font-semibold">Current eligibility:</span>{' '}
                        {(latestIncident?.eligibility as any)?.eligible ? 'Eligible' : 'Not eligible'}
                      </div>
                    </div>

                    <div className="space-y-1">
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        Mandatory reason
                      </p>
                      <Input
                        value={shutdownReason}
                        onChange={(e) => setShutdownReason(e.target.value)}
                        placeholder="Maintenance, Testing, Security Isolation, Manual Troubleshooting, Other"
                      />
                    </div>
                  </div>

                  <AlertDialogFooter>
                    <AlertDialogCancel disabled={manualMutations.shutdown.isPending}>Cancel</AlertDialogCancel>
                    <AlertDialogAction
                      disabled={!shutdownReason || manualMutations.shutdown.isPending}
                      onClick={() => {
                        manualMutations.shutdown.mutate(
                          {
                            deviceId,
                            interface: interfaceName,
                            reason: shutdownReason,
                          },
                          {
                            onSuccess: async () => {
                              setShutdownDialogOpen(false)
                              void inventoryQuery.refetch()
                              void statsQuery.refetch()
                              void historyQuery.refetch()
                            },
                          },
                        )
                      }}
                    >
                      {manualMutations.shutdown.isPending ? 'Executing…' : 'Confirm Shutdown'}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            ) : null}

            {/* ── Recover confirmation ─────────────────────────── */}
            {canSafeRecover ? (
              <AlertDialog open={recoverDialogOpen} onOpenChange={setRecoverDialogOpen}>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Recover Port</AlertDialogTitle>
                    <AlertDialogDescription>
                      Immediately re-enable this interface. Manual recovery bypasses automated
                      recovery policy checks and executes no-shutdown now.
                    </AlertDialogDescription>
                  </AlertDialogHeader>

                  <div className="space-y-3">
                    <div className="grid gap-2 text-sm">
                      <div>
                        <span className="font-semibold">Device:</span> {hostname}
                      </div>
                      <div>
                        <span className="font-semibold">Interface:</span> {interfaceName}
                      </div>
                      <div>
                        <span className="font-semibold">Incident:</span>{' '}
                        <span className="mono">
                          {latestRecoverableIncident?.incidentId || 'Will create if needed'}
                        </span>
                      </div>
                    </div>

                    <div className="space-y-1">
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        Mandatory reason
                      </p>
                      <Input
                        value={recoverReason}
                        onChange={(e) => setRecoverReason(e.target.value)}
                        placeholder="Reason for manual recovery"
                      />
                    </div>
                  </div>

                  <AlertDialogFooter>
                    <AlertDialogCancel disabled={manualMutations.recover.isPending}>Cancel</AlertDialogCancel>
                    <AlertDialogAction
                      disabled={!recoverReason.trim() || manualMutations.recover.isPending}
                      onClick={(event) => {
                        event.preventDefault()
                        manualMutations.recover.mutate(
                          {
                            deviceId,
                            interface: interfaceName,
                            reason: recoverReason.trim(),
                          },
                          {
                            onSuccess: async (res) => {
                              if (res.success) {
                                setRecoverDialogOpen(false)
                                setRecoverReason('')
                                void inventoryQuery.refetch()
                                void statsQuery.refetch()
                                void historyQuery.refetch()
                                void stormIncidentsQuery.refetch()
                              }
                            },
                          },
                        )
                      }}
                    >
                      {manualMutations.recover.isPending ? 'Executing…' : 'Confirm Recovery'}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            ) : null}

            {/* ── Emergency confirmation ───────────────────────── */}
            {canEmergencyShutdown ? (
              <AlertDialog
                open={emergencyDialogOpen}
                onOpenChange={(open) => {
                  if (!manualMutations.emergencyShutdown.isPending) {
                    setEmergencyDialogOpen(open)
                  }
                }}
              >
                <AlertDialogContent className="max-w-lg">
                  <AlertDialogHeader>
                    <AlertDialogTitle className="text-destructive">
                      Emergency Shutdown
                    </AlertDialogTitle>
                    <AlertDialogDescription asChild>
                      <div className="space-y-3 text-sm text-foreground">
                        <p className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-destructive">
                          Emergency Shutdown immediately disables this interface. This bypasses
                          automated storm validation. Use only during security incidents or emergency
                          network isolation.
                        </p>
                        <div className="grid gap-2 rounded-md border border-border bg-muted/40 p-3">
                          <div>
                            <span className="font-semibold">Device Name:</span> {hostname}
                          </div>
                          <div>
                            <span className="font-semibold">Management IP:</span> {ipAddress}
                          </div>
                          <div>
                            <span className="font-semibold">Hostname:</span> {hostname}
                          </div>
                          <div>
                            <span className="font-semibold">Vendor:</span> {iface?.vendor || '—'}
                          </div>
                          <div>
                            <span className="font-semibold">Model:</span>{' '}
                            {iface?.neighbor?.platform || '—'}
                          </div>
                          <div>
                            <span className="font-semibold">Interface:</span> {interfaceName}
                          </div>
                          <div>
                            <span className="font-semibold">Description:</span>{' '}
                            {iface?.description || '—'}
                          </div>
                          <div>
                            <span className="font-semibold">Current Status:</span>{' '}
                            {iface?.operStatus || '—'} / {iface?.adminStatus || '—'}
                          </div>
                          <div>
                            <span className="font-semibold">Classification:</span>{' '}
                            {iface?.isAccess ? 'Access' : iface?.isTrunk ? 'Trunk' : '—'}
                          </div>
                          <div>
                            <span className="font-semibold">Current VLAN:</span>{' '}
                            {iface?.vlan ?? iface?.accessVlan ?? '—'}
                          </div>
                          <div>
                            <span className="font-semibold">Current Speed:</span>{' '}
                            {iface?.speed || (iface?.speedMbps ? `${iface.speedMbps} Mbps` : '—')}
                          </div>
                          <div>
                            <span className="font-semibold">Neighbor:</span>{' '}
                            {iface?.neighbor?.hostname
                              ? `${iface.neighbor.hostname}${neighborRemotePort(iface.neighbor) ? ` (${neighborRemotePort(iface.neighbor)})` : ''}`
                              : '—'}
                          </div>
                        </div>
                      </div>
                    </AlertDialogDescription>
                  </AlertDialogHeader>

                  <div className="space-y-3">
                    <div className="space-y-1">
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        Type SHUTDOWN to confirm
                      </p>
                      <Input
                        value={emergencyConfirmText}
                        onChange={(e) => setEmergencyConfirmText(e.target.value)}
                        placeholder="SHUTDOWN"
                        autoComplete="off"
                        disabled={manualMutations.emergencyShutdown.isPending}
                      />
                    </div>
                    <div className="space-y-1">
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        Mandatory reason (min. 10 characters)
                      </p>
                      <Input
                        value={emergencyReason}
                        onChange={(e) => setEmergencyReason(e.target.value)}
                        placeholder="Describe the emergency justification"
                        disabled={manualMutations.emergencyShutdown.isPending}
                      />
                    </div>
                  </div>

                  <AlertDialogFooter>
                    <AlertDialogCancel disabled={manualMutations.emergencyShutdown.isPending}>
                      Cancel
                    </AlertDialogCancel>
                    <AlertDialogAction
                      className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                      disabled={
                        emergencyConfirmText !== 'SHUTDOWN' ||
                        emergencyReason.trim().length < 10 ||
                        manualMutations.emergencyShutdown.isPending
                      }
                      onClick={() => {
                        manualMutations.emergencyShutdown.mutate(
                          {
                            deviceId,
                            interface: interfaceName,
                            reason: emergencyReason.trim(),
                            confirmation: 'SHUTDOWN',
                          },
                          {
                            onSuccess: async () => {
                              setEmergencyDialogOpen(false)
                              setEmergencyReason('')
                              setEmergencyConfirmText('')
                              void inventoryQuery.refetch()
                              void statsQuery.refetch()
                              void historyQuery.refetch()
                              void stormIncidentsQuery.refetch()
                            },
                          },
                        )
                      }}
                    >
                      {manualMutations.emergencyShutdown.isPending ? (
                        <>
                          <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
                          Executing…
                        </>
                      ) : (
                        'Execute Emergency Shutdown'
                      )}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            ) : null}

            {/* ── Diagnostics + History ─────────────────────────── */}
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="space-y-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Diagnostics
                </p>
                {latestIncident ? (
                  <div className="space-y-2">
                    <pre className="max-h-48 overflow-auto rounded-md bg-muted p-2 text-xs">
                      {JSON.stringify(
                        {
                          interfaceSnapshot: latestIncident.interfaceSnapshot,
                          switchportSnapshot: latestIncident.switchportSnapshot,
                          macTable: latestIncident.macTable,
                          statistics: latestIncident.statistics,
                          neighbor: latestIncident.neighbor,
                          timeline: latestIncident.timeline,
                        },
                        null,
                        2,
                      )}
                    </pre>
                  </div>
                ) : (
                  <EmptyState
                    title="No diagnostics available"
                    description="A Storm incident must exist (automatic or manual) to show diagnostics."
                  />
                )}
              </div>

              <div className="space-y-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  History
                </p>
                {latestIncident ? (
                  <div className="space-y-3">
                    <div className="text-sm">
                      <span className="font-semibold">Mitigation:</span>{' '}
                      {(mitigationDetailQuery.data?.data ?? [])[0]?.status || '—'}
                    </div>
                    <div className="text-sm">
                      <span className="font-semibold">Recovery:</span>{' '}
                      {(recoveryDetailQuery.data?.data ?? [])[0]?.recoveryStatus || '—'}
                    </div>
                  </div>
                ) : (
                  <EmptyState
                    title="No history available"
                    description="Run a manual shutdown or recover to generate mitigation/recovery history."
                  />
                )}
              </div>
            </div>
          </CardContent>
        </Card>

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
                    <RechartsTooltip content={<ChartTooltip />} />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="broadcastPackets"
                      name="Broadcast (combined)"
                      stroke="#8B5CF6"
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="rxBroadcastPackets"
                      name="RX Broadcast"
                      stroke="#A78BFA"
                      strokeWidth={1.5}
                      strokeDasharray="4 4"
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="txBroadcastPackets"
                      name="TX Broadcast"
                      stroke="#7C3AED"
                      strokeWidth={1.5}
                      strokeDasharray="4 4"
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="multicastPackets"
                      name="Multicast (combined)"
                      stroke="#06B6D4"
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="rxMulticastPackets"
                      name="RX Multicast"
                      stroke="#22D3EE"
                      strokeWidth={1.5}
                      strokeDasharray="4 4"
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="txMulticastPackets"
                      name="TX Multicast"
                      stroke="#0891B2"
                      strokeWidth={1.5}
                      strokeDasharray="4 4"
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
                    <RechartsTooltip content={<ChartTooltip />} />
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
                      name="Discards (combined)"
                      stroke="#94A3B8"
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="rxDiscards"
                      name="RX Discards"
                      stroke="#64748B"
                      strokeWidth={1.5}
                      strokeDasharray="4 4"
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="txDiscards"
                      name="TX Discards"
                      stroke="#CBD5E1"
                      strokeWidth={1.5}
                      strokeDasharray="4 4"
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
                    <TableHead className="hidden lg:table-cell">Broadcast RX/TX</TableHead>
                    <TableHead className="hidden lg:table-cell">Multicast RX/TX</TableHead>
                    <TableHead className="hidden md:table-cell">Discards RX/TX</TableHead>
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
                      <TableCell className="hidden mono text-muted-foreground lg:table-cell">
                        {formatDirectionalPackets(
                          sample.rxBroadcastPackets,
                          sample.txBroadcastPackets,
                          sample.broadcastPackets,
                        )}
                      </TableCell>
                      <TableCell className="hidden mono text-muted-foreground lg:table-cell">
                        {formatDirectionalPackets(
                          sample.rxMulticastPackets,
                          sample.txMulticastPackets,
                          sample.multicastPackets,
                        )}
                      </TableCell>
                      <TableCell className="hidden mono text-muted-foreground md:table-cell">
                        {formatDirectionalPackets(
                          sample.rxDiscards,
                          sample.txDiscards,
                          sample.discards,
                        )}
                      </TableCell>
                      <TableCell className="mono">
                        {formatPackets(sample.inputErrors + sample.outputErrors)}
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
