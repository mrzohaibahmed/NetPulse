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
import { KpiCard } from '@/shared/components/KpiCard'
import { LoadingState, TableSkeleton } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/shared/ui/tooltip'
import {
  useDeviceInterfaceStatsQuery,
  useDeviceInterfacesQuery,
  useInterfaceHistoryQuery,
  useInterfaceMutations,
  useInterfaceRiskQuery,
  useStormIncidentsQuery,
  useMitigationDetailQuery,
  useRecoveryDetailQuery,
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

/** Theme-aware chart colors from index.css design tokens. */
const CHART_COLORS = {
  grid: 'var(--color-border)',
  tick: 'var(--color-muted-foreground)',
  primary: 'var(--color-primary)',
  success: 'var(--color-success)',
  warning: 'var(--color-warning)',
  danger: 'var(--color-danger)',
  info: 'var(--color-info)',
  muted: 'var(--color-muted-foreground)',
  border: 'var(--color-border)',
} as const

const CHART_TICK = { fill: CHART_COLORS.tick, fontSize: 10 }
const CHART_TICK_Y = { fill: CHART_COLORS.tick, fontSize: 11 }

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
  POSSIBLE_SOURCE: 'Possible Source',
  LIKELY_FORWARDER: 'Likely Forwarder',
  LIKELY_RECEIVER: 'Likely Receiver',
  NORMAL: 'Normal',
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
): 'default' | 'secondary' | 'outline' | 'danger' | 'warning' | 'success' {
  switch (classification) {
    case 'LIKELY_SOURCE':
      return 'danger'
    case 'POSSIBLE_SOURCE':
      return 'warning'
    case 'LIKELY_FORWARDER':
      return 'secondary'
    case 'LIKELY_RECEIVER':
      return 'outline'
    case 'NORMAL':
      return 'success'
    default:
      return 'default'
  }
}

function monitoringBadgeLabel(iface: {
  administratorDisabled?: boolean
  monitoringMode?: string
  monitoringReason?: string
  monitoringEnabled?: boolean
  adminStatus?: string
  operStatus?: string
  effectiveMonitoring?: boolean
} | null): string | null {
  if (!iface) return null
  if (
    iface.administratorDisabled === true ||
    iface.monitoringMode === 'DISABLED_BY_USER' ||
    (iface.monitoringEnabled === false && iface.monitoringMode !== 'AUTO')
  ) {
    return 'Monitoring Disabled'
  }
  const reason = iface.monitoringReason
  if (reason === 'administrative_down' || reason === 'operational_down') {
    return 'Interface Down'
  }
  const admin = (iface.adminStatus || '').toLowerCase()
  const oper = (iface.operStatus || '').toLowerCase()
  if (admin === 'down' || oper === 'down') {
    return 'Interface Down'
  }
  return null
}

function monitoringDetailValue(iface: {
  administratorDisabled?: boolean
  monitoringMode?: string
  monitoringReason?: string
  monitoringEnabled?: boolean
  effectiveMonitoring?: boolean
} | null): string {
  if (!iface) return '—'
  if (iface.administratorDisabled || iface.monitoringMode === 'DISABLED_BY_USER') {
    return 'disabled by administrator'
  }
  if (iface.monitoringReason === 'administrative_down') {
    return 'paused — administrative down'
  }
  if (iface.monitoringReason === 'operational_down') {
    return 'paused — operational down'
  }
  if (iface.effectiveMonitoring) {
    return 'enabled'
  }
  if (iface.monitoringEnabled === false) {
    return 'disabled'
  }
  return 'enabled'
}

export function InterfaceDetailPage() {
  const navigate = useNavigate()
  const { deviceId = '', interfaceName: rawName = '' } = useParams()
  const interfaceName = decodeURIComponent(rawName)
  const { isAdmin, isUser } = useAuth()
  const mutations = useInterfaceMutations()
  const [historyLimit, setHistoryLimit] = useState('100')
  const [confirmAction, setConfirmAction] = useState<'shutdown' | 'recover' | null>(null)
  const [shutdownReason, setShutdownReason] = useState('')

  const stormIncidentsQuery = useStormIncidentsQuery({
    limit: 50,
    deviceId,
    q: interfaceName,
  })

  const latestIncident = useMemo(() => {
    const rows = stormIncidentsQuery.data?.data ?? []
    return rows[0] ?? null
  }, [stormIncidentsQuery.data])

  const mitigationDetailQuery = useMitigationDetailQuery(
    latestIncident?.incidentId || '',
    Boolean(latestIncident?.incidentId),
  )
  const recoveryDetailQuery = useRecoveryDetailQuery(
    latestIncident?.incidentId || '',
    Boolean(latestIncident?.incidentId),
  )

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
    <div className="np-page min-w-0">
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
          <div className="space-y-2">
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
              {isUser ? (
                <Button
                  type="button"
                  variant="destructive"
                  disabled={mutations.manualShutdown.isPending}
                  onClick={() => setConfirmAction('shutdown')}
                >
                  {mutations.manualShutdown.isPending ? 'Shutting down…' : 'Manual shutdown'}
                </Button>
              ) : null}
              {isUser ? (
                <Button
                  type="button"
                  variant="secondary"
                  disabled={mutations.manualRecover.isPending}
                  onClick={() => setConfirmAction('recover')}
                >
                  {mutations.manualRecover.isPending ? 'Recovering…' : 'Manual recover'}
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
            {isUser ? (
              <div className="text-right text-xs text-muted-foreground">
                {latestIncident ? (
                  <>
                    Latest incident <span className="mono">{latestIncident.incidentId}</span> is{' '}
                    <span className="font-medium text-foreground">
                      {latestIncident.status}
                    </span>
                    . Manual recover is always available. Recovery is only permitted when the
                    incident is in MITIGATED status.
                  </>
                ) : (
                  <>
                    No incident is currently associated with this interface. Manual recover is
                    always available. Recovery is only permitted when the incident is in MITIGATED
                    status.
                  </>
                )}
              </div>
            ) : null}
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
        {(() => {
          const label = monitoringBadgeLabel(iface)
          return label ? (
            <Badge variant="outline" className="border-muted-foreground/40 text-muted-foreground">
              {label}
            </Badge>
          ) : null
        })()}
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
              value={monitoringDetailValue(iface)}
            />
            {(isAdmin || isUser) && iface ? (
              <div className="flex items-center justify-between gap-2 pt-1">
                <span className="text-muted-foreground">Admin preference</span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={mutations.setMonitoring.isPending}
                  onClick={() =>
                    mutations.setMonitoring.mutate({
                      deviceId,
                      interfaceName,
                      enabled: Boolean(
                        iface.administratorDisabled ||
                          iface.monitoringMode === 'DISABLED_BY_USER' ||
                          iface.monitoringEnabled === false,
                      ),
                    })
                  }
                >
                  {iface.administratorDisabled ||
                  iface.monitoringMode === 'DISABLED_BY_USER' ||
                  iface.monitoringEnabled === false
                    ? 'Enable monitoring'
                    : 'Disable monitoring'}
                </Button>
              </div>
            ) : null}
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
            {iface?.resolvedDeviceIp ? (
              <DetailRow
                label="Connected device IP"
                value={iface.resolvedDeviceIp}
                mono
              />
            ) : null}
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
              <div className="h-72 min-w-0 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={chartData}>
                    <defs>
                      <linearGradient id="utilFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={CHART_COLORS.primary} stopOpacity={0.35} />
                        <stop offset="100%" stopColor={CHART_COLORS.primary} stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-border" vertical={false} />
                    <XAxis
                      dataKey="timestamp"
                      tick={CHART_TICK}
                      tickFormatter={(value) => formatDateTime(String(value)).slice(5, 16)}
                      minTickGap={28}
                    />
                    <YAxis
                      tick={CHART_TICK_Y}
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
                      stroke={CHART_COLORS.primary}
                      fill="url(#utilFill)"
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="rxUtilization"
                      name="RX %"
                      stroke={CHART_COLORS.success}
                      strokeWidth={1.5}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="txUtilization"
                      name="TX %"
                      stroke={CHART_COLORS.warning}
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
          <CardHeader className="pb-3">
            <div className="space-y-1">
              <CardTitle className="text-base">Storm Diagnostics</CardTitle>
              <p className="text-sm text-muted-foreground">
                Storm Protection still handles the automated path. Operator-authorized manual shutdown and recovery are available from the page actions above.
              </p>
            </div>
          </CardHeader>

          <CardContent className="space-y-5">
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
                    description="A Storm incident must exist for this interface to show diagnostics."
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
                    description="History appears after automatic Storm Protection mitigation or recovery."
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
              <div className="h-56 min-w-0 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                    <XAxis
                      dataKey="timestamp"
                      tick={CHART_TICK}
                      tickFormatter={(value) => formatDateTime(String(value)).slice(5, 16)}
                      minTickGap={28}
                    />
                    <YAxis tick={CHART_TICK} width={48} />
                    <RechartsTooltip content={<ChartTooltip />} />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="broadcastPackets"
                      name="Broadcast (combined)"
                      stroke={CHART_COLORS.primary}
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="rxBroadcastPackets"
                      name="RX Broadcast"
                      stroke={CHART_COLORS.info}
                      strokeWidth={1.5}
                      strokeDasharray="4 4"
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="txBroadcastPackets"
                      name="TX Broadcast"
                      stroke={CHART_COLORS.warning}
                      strokeWidth={1.5}
                      strokeDasharray="4 4"
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="multicastPackets"
                      name="Multicast (combined)"
                      stroke={CHART_COLORS.info}
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="rxMulticastPackets"
                      name="RX Multicast"
                      stroke={CHART_COLORS.success}
                      strokeWidth={1.5}
                      strokeDasharray="4 4"
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="txMulticastPackets"
                      name="TX Multicast"
                      stroke={CHART_COLORS.warning}
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
              <div className="h-56 min-w-0 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                    <XAxis
                      dataKey="timestamp"
                      tick={CHART_TICK}
                      tickFormatter={(value) => formatDateTime(String(value)).slice(5, 16)}
                      minTickGap={28}
                    />
                    <YAxis tick={CHART_TICK} width={48} />
                    <RechartsTooltip content={<ChartTooltip />} />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="inputErrors"
                      name="Input errors"
                      stroke={CHART_COLORS.danger}
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="outputErrors"
                      name="Output errors"
                      stroke={CHART_COLORS.warning}
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="discards"
                      name="Discards (combined)"
                      stroke={CHART_COLORS.muted}
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="rxDiscards"
                      name="RX Discards"
                      stroke={CHART_COLORS.border}
                      strokeWidth={1.5}
                      strokeDasharray="4 4"
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="txDiscards"
                      name="TX Discards"
                      stroke={CHART_COLORS.muted}
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

      <AlertDialog
        open={confirmAction === 'shutdown'}
        onOpenChange={(open) => {
          if (!open) {
            setConfirmAction(null)
            setShutdownReason('')
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Shut down interface {interfaceName}?</AlertDialogTitle>
            <AlertDialogDescription>
              Emergency break-glass action. This sends a live shutdown to the switch
              and creates a MANUAL incident. A reason is required for audit.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2 py-2">
            <Label htmlFor="manual-shutdown-reason">Reason</Label>
            <Input
              id="manual-shutdown-reason"
              value={shutdownReason}
              onChange={(event) => setShutdownReason(event.target.value)}
              placeholder="e.g. Port flooding lab traffic — manual shutdown"
              autoFocus
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={!shutdownReason.trim()}
              onClick={(event) => {
                const reason = shutdownReason.trim()
                if (!reason) {
                  event.preventDefault()
                  return
                }
                mutations.manualShutdown.mutate({
                  deviceId,
                  interfaceName,
                  confirm: true,
                  reason,
                })
                setConfirmAction(null)
                setShutdownReason('')
              }}
            >
              Yes, shut down interface {interfaceName}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={confirmAction === 'recover'} onOpenChange={(open) => !open && setConfirmAction(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Recover interface {interfaceName}?</AlertDialogTitle>
            <AlertDialogDescription>
              This is a manual override. Recovery Safety rules (R1–R8) are
              bypassed and a live &quot;no shutdown&quot; is sent immediately to restore
              the port, reusing the current mitigated incident.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                mutations.manualRecover.mutate({
                  deviceId,
                  interfaceName,
                  confirm: true,
                  incidentId: latestIncident?.incidentId,
                })
                setConfirmAction(null)
              }}
            >
              Yes, recover interface {interfaceName}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
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
