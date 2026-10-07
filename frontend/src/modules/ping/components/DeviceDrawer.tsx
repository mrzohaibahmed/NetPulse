import { useState } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  Cpu,
  Globe,
  Loader2,
  Network,
  Radar,
  ScanLine,
  Server,
  Shield,
  Wifi,
} from 'lucide-react'
import {
  useDeviceHistoryQuery,
  useDeviceMutations,
  useNmapScanMutation,
  useServerHardwareHealthQuery,
} from '@/hooks/queries'
import { useAuth } from '@/shared/auth/AuthContext'
import { formatDateTime, formatMs, formatPercent, formatRelative } from '@/utils/format'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { LoadingState } from '@/shared/components/LoadingState'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'
import { ScrollArea } from '@/shared/ui/scroll-area'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/shared/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import type { NetworkInfo, NetworkPort } from '@/types'
import { displayDeviceType } from '@/modules/ping/constants/devices'

interface DeviceDrawerProps {
  deviceId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

type TabId = 'overview' | 'network' | 'hardware'

export function DeviceDrawer({ deviceId, open, onOpenChange }: DeviceDrawerProps) {
  const { isUser } = useAuth()
  const [activeTab, setActiveTab] = useState<TabId>('overview')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')

  const query = useDeviceHistoryQuery(
    deviceId || '',
    { startDate: startDate || undefined, endDate: endDate || undefined, limit: 300 },
    open && Boolean(deviceId),
  )
  const { scan } = useDeviceMutations()
  const nmapScan = useNmapScanMutation()

  const data = query.data
  const device = data?.device
  const networkInfo: NetworkInfo | null | undefined = device?.networkInfo

  const isServerDevice = ['Server', 'Linux Server', 'ESXi Server'].includes(device?.deviceType || '')
  const hardwareQuery = useServerHardwareHealthQuery(
    deviceId || '',
    open && Boolean(deviceId) && isServerDevice,
  )

  const openPorts = networkInfo?.ports?.filter((p) => p.state === 'open') ?? []

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-full flex-col p-0 sm:max-w-xl md:max-w-2xl">
        <SheetHeader className="space-y-1 border-b border-border bg-card/40 p-6 pb-4">
          <SheetTitle className="truncate text-xl tracking-tight">
            {device?.hostname ?? 'Device details'}
          </SheetTitle>
          <SheetDescription className="mono truncate text-sm">
            {device
              ? `${device.ipAddress || device.iloAddress || '—'} · ${displayDeviceType(device.deviceType, device.classificationConfidence)}`
              : 'Loading device telemetry…'}
          </SheetDescription>
        </SheetHeader>

        <div className="flex border-b border-border bg-card/40 px-6">
          <TabButton active={activeTab === 'overview'} onClick={() => setActiveTab('overview')}>
            <Activity className="h-3.5 w-3.5" />
            Overview
          </TabButton>
          <TabButton active={activeTab === 'network'} onClick={() => setActiveTab('network')}>
            <Network className="h-3.5 w-3.5" />
            Network Info
            {networkInfo ? (
              <span className="ml-1.5 rounded-full bg-primary/20 px-1.5 py-0.5 text-[10px] font-semibold text-primary">
                {openPorts.length}
              </span>
            ) : (
              <span className="ml-1.5 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                —
              </span>
            )}
          </TabButton>
          {isServerDevice ? (
            <TabButton active={activeTab === 'hardware'} onClick={() => setActiveTab('hardware')}>
              <Cpu className="h-3.5 w-3.5" />
              Hardware Health
              {hardwareQuery.data?.health ? (
                <span
                  className={`ml-1.5 rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${
                    hardwareQuery.data.health.overallHealth === 'OK'
                      ? 'bg-success/20 text-success'
                      : hardwareQuery.data.health.overallHealth === 'WARNING'
                        ? 'bg-warning/20 text-warning'
                        : hardwareQuery.data.health.overallHealth === 'CRITICAL'
                          ? 'bg-danger/20 text-danger'
                          : 'bg-muted text-muted-foreground'
                  }`}
                >
                  {hardwareQuery.data.health.overallHealth}
                </span>
              ) : hardwareQuery.data?.freshness?.status === 'FAILING' ? (
                <span className="ml-1.5 rounded-full bg-danger/20 px-1.5 py-0.5 text-[10px] font-semibold text-danger">
                  Failing
                </span>
              ) : hardwareQuery.data?.freshness?.status === 'NEVER_POLLED' ? (
                <span className="ml-1.5 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">
                  Never Polled
                </span>
              ) : null}
            </TabButton>
          ) : null}
        </div>

        <ScrollArea className="flex-1">
          <div className="space-y-6 p-6">
            {query.isLoading && !data ? <LoadingState label="Loading device history…" /> : null}
            {query.error && !data ? (
              <ErrorState
                message={query.error instanceof Error ? query.error.message : 'Failed to load'}
                onRetry={() => void query.refetch()}
              />
            ) : null}

            {device ? (
              <>
                <Card className="glass rounded-xl border-l-[3px] border-l-primary">
                  <CardContent className="flex flex-wrap items-center gap-2 py-4">
                    <StatusBadge status={device.status} />
                    {device.critical ? <Badge variant="danger">Critical</Badge> : null}
                    <Badge variant={device.monitor ? 'success' : 'muted'}>
                      {device.monitor ? 'Monitored' : 'Not monitored'}
                    </Badge>
                    <div className="ml-auto flex flex-wrap gap-2">
                      <Button
                        type="button"
                        size="sm"
                        variant="secondary"
                        disabled={scan.isPending}
                        onClick={() => scan.mutate(device._id)}
                      >
                        {scan.isPending ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Radar className="h-4 w-4" />
                        )}
                        Ping now
                      </Button>
                      {isUser ? (
                        <Button
                          type="button"
                          size="sm"
                          variant={networkInfo ? 'secondary' : 'default'}
                          disabled={nmapScan.isPending || device.status !== 'Online'}
                          title={
                            device.status !== 'Online' ? 'Device must be Online to run Nmap' : undefined
                          }
                          onClick={() => {
                            nmapScan.mutate(device._id, {
                              onSuccess: () => {
                                void query.refetch()
                                setActiveTab('network')
                              },
                            })
                          }}
                        >
                          {nmapScan.isPending ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <ScanLine className="h-4 w-4" />
                          )}
                          {nmapScan.isPending ? 'Scanning…' : networkInfo ? 'Re-scan' : 'Nmap scan'}
                        </Button>
                      ) : null}
                    </div>
                  </CardContent>
                </Card>

                {activeTab === 'overview' && (
                  <>
                    <section className="space-y-3">
                      <SectionHeading icon={<Server className="h-4 w-4" />} title="Identity & credentials" />
                      <div className="grid gap-3 sm:grid-cols-2">
                        <Meta label="Hostname" value={device.hostname} />
                        <Meta label="OS IP address" value={device.ipAddress || '—'} mono />
                        <Meta label="iLO address" value={device.iloAddress || '—'} mono />
                        <Meta
                          label="Device type"
                          value={displayDeviceType(
                            device.deviceType,
                            device.classificationConfidence,
                          )}
                        />
                        <Meta
                          label="Vendor"
                          value={device.vendor || device.credentials?.sshVendor || '—'}
                        />
                        <Meta label="Operating system" value={device.operatingSystem || '—'} />
                        <Meta
                          label="Confidence"
                          value={
                            device.classificationConfidence != null
                              ? `${device.classificationConfidence}%`
                              : '—'
                          }
                        />
                        <Meta
                          label="SSH Username"
                          value={device.credentials?.sshUsername || '—'}
                        />
                        <Meta
                          label="SSH Password"
                          value={
                            device.credentials?.sshPasswordConfigured ? '••••••••' : 'Not Configured'
                          }
                        />
                        <Meta
                          label="iLO Username"
                          value={device.credentials?.iloUsername || '—'}
                        />
                        <Meta
                          label="iLO Password"
                          value={
                            device.credentials?.iloPasswordConfigured ? '••••••••' : 'Not Configured'
                          }
                        />
                        <Meta label="Monitor" value={device.monitor ? 'Enabled' : 'Disabled'} />
                        <Meta label="Last seen" value={formatRelative(device.lastSeen)} />
                        <Meta label="Response time" value={formatMs(device.responseTime)} mono />
                        <Meta
                          label="Consecutive failures"
                          value={String(device.consecutiveFailures ?? 0)}
                        />
                      </div>
                    </section>

                    <section className="space-y-3">
                      <SectionHeading icon={<Activity className="h-4 w-4" />} title="Availability" />
                      <div className="grid gap-3 sm:grid-cols-3">
                        <Stat label="Uptime" value={formatPercent(data?.uptime.uptimePercentage)} />
                        <Stat label="Downtime" value={formatPercent(data?.uptime.downtimePercentage)} />
                        <Stat label="Checks" value={String(data?.uptime.totalChecks ?? 0)} />
                      </div>
                    </section>

                    <Card className="glass rounded-xl">
                      <CardHeader className="pb-3">
                        <CardTitle className="text-sm font-semibold">History range</CardTitle>
                      </CardHeader>
                      <CardContent>
                        <div className="flex flex-wrap items-end gap-3">
                          <div className="space-y-1.5">
                            <Label htmlFor="drawer-from">From</Label>
                            <Input
                              id="drawer-from"
                              type="date"
                              value={startDate}
                              onChange={(e) => setStartDate(e.target.value)}
                            />
                          </div>
                          <div className="space-y-1.5">
                            <Label htmlFor="drawer-to">To</Label>
                            <Input
                              id="drawer-to"
                              type="date"
                              value={endDate}
                              onChange={(e) => setEndDate(e.target.value)}
                            />
                          </div>
                          <Button type="button" variant="secondary" onClick={() => void query.refetch()}>
                            Apply
                          </Button>
                        </div>
                      </CardContent>
                    </Card>

                    <section className="space-y-3">
                      <SectionHeading
                        icon={<Activity className="h-4 w-4" />}
                        title="Response time trend"
                      />
                      {(data?.responseTimeTrend?.length ?? 0) === 0 ? (
                        <EmptyState title="No successful pings in this range" className="py-8" />
                      ) : (
                        <div className="h-56 rounded-xl border border-border/60 bg-secondary/20 p-3">
                          <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={data?.responseTimeTrend}>
                              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                              <XAxis
                                dataKey="timestamp"
                                tick={{ fill: '#94a3b8', fontSize: 10 }}
                                tickFormatter={(value) => formatDateTime(String(value)).slice(5, 16)}
                              />
                              <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} unit=" ms" width={48} />
                              <Tooltip
                                labelFormatter={(value) => formatDateTime(String(value))}
                                formatter={(value) => [`${Number(value).toFixed(1)} ms`, 'RTT']}
                                contentStyle={{
                                  background: '#1e293b',
                                  border: '1px solid #334155',
                                  borderRadius: 8,
                                }}
                              />
                              <Line
                                type="monotone"
                                dataKey="responseTime"
                                stroke="#3B82F6"
                                strokeWidth={2}
                                dot={false}
                              />
                            </LineChart>
                          </ResponsiveContainer>
                        </div>
                      )}
                    </section>

                    <section className="space-y-3">
                      <SectionHeading icon={<Radar className="h-4 w-4" />} title="Recent scans" />
                      {(data?.history.length ?? 0) === 0 ? (
                        <EmptyState title="No history yet" className="py-8" />
                      ) : (
                        <div className="overflow-hidden rounded-xl border border-border/60">
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead>When</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead>RTT</TableHead>
                                <TableHead>Scan</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {[...(data?.history ?? [])]
                                .reverse()
                                .slice(0, 50)
                                .map((row) => (
                                  <TableRow key={row._id}>
                                    <TableCell className="text-muted-foreground">
                                      {formatDateTime(row.timestamp)}
                                    </TableCell>
                                    <TableCell>
                                      <StatusBadge status={row.status} pulse={false} />
                                    </TableCell>
                                    <TableCell className="mono">{formatMs(row.responseTime)}</TableCell>
                                    <TableCell>{row.scanType}</TableCell>
                                  </TableRow>
                                ))}
                            </TableBody>
                          </Table>
                        </div>
                      )}
                    </section>
                  </>
                )}

                {activeTab === 'network' && (
                  <>
                    {!networkInfo ? (
                      <div className="flex flex-col items-center gap-4 rounded-xl border border-dashed border-border bg-secondary/10 py-14 text-center">
                        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
                          <ScanLine className="h-6 w-6 text-primary" />
                        </div>
                        <div>
                          <p className="font-semibold">No scan data yet</p>
                          <p className="mt-1 text-sm text-muted-foreground">
                            Click <strong>Nmap scan</strong> above to discover OS, ports, and services.
                            {device.status !== 'Online' && (
                              <span className="mt-1 block text-warning">
                                {' '}
                                Device must be Online to scan.
                              </span>
                            )}
                          </p>
                        </div>
                      </div>
                    ) : (
                      <div className="space-y-6">
                        <p className="text-xs text-muted-foreground">
                          Last scanned:{' '}
                          {networkInfo.lastScan ? formatDateTime(networkInfo.lastScan) : '—'}
                        </p>

                        <Card className="glass rounded-xl">
                          <CardContent className="space-y-3 pt-5">
                            <SectionHeading icon={<Cpu className="h-4 w-4" />} title="Operating System" />
                            <div className="grid gap-3 sm:grid-cols-2">
                              <Meta label="OS name" value={networkInfo.os?.name || '—'} />
                              <Meta label="OS family" value={networkInfo.os?.family || '—'} />
                              <Meta label="Generation" value={networkInfo.os?.generation || '—'} />
                              <Meta
                                label="Detection accuracy"
                                value={
                                  networkInfo.os?.accuracy ? `${networkInfo.os.accuracy}%` : '—'
                                }
                              />
                            </div>
                          </CardContent>
                        </Card>

                        <Card className="glass rounded-xl">
                          <CardContent className="space-y-3 pt-5">
                            <SectionHeading icon={<Server className="h-4 w-4" />} title="Device Identity" />
                            <div className="grid gap-3 sm:grid-cols-2">
                              <Meta label="Nmap hostname" value={networkInfo.hostname || '—'} />
                              <Meta label="Device class" value={networkInfo.deviceType || '—'} />
                              <Meta label="MAC address" value={networkInfo.macAddress || '—'} mono />
                              <Meta label="Vendor" value={networkInfo.vendor || '—'} />
                            </div>
                          </CardContent>
                        </Card>

                        {networkInfo.services && networkInfo.services.length > 0 && (
                          <Card className="glass rounded-xl">
                            <CardContent className="space-y-3 pt-5">
                              <SectionHeading
                                icon={<Globe className="h-4 w-4" />}
                                title="Detected Services"
                              />
                              <div className="flex flex-wrap gap-2">
                                {networkInfo.services.map((svc) => (
                                  <Badge key={svc} variant="secondary" className="font-mono text-xs">
                                    {svc}
                                  </Badge>
                                ))}
                              </div>
                            </CardContent>
                          </Card>
                        )}

                        <Card className="glass rounded-xl">
                          <CardContent className="space-y-3 pt-5">
                            <SectionHeading
                              icon={<Wifi className="h-4 w-4" />}
                              title={`Open Ports (${openPorts.length})`}
                            />
                            {openPorts.length === 0 ? (
                              <EmptyState title="No open ports detected" className="py-6" />
                            ) : (
                              <div className="w-full max-w-full overflow-x-auto rounded-lg border border-border/60">
                                <Table>
                                  <TableHeader>
                                    <TableRow>
                                      <TableHead>Port</TableHead>
                                      <TableHead>Proto</TableHead>
                                      <TableHead>Service</TableHead>
                                      <TableHead>Product / Version</TableHead>
                                    </TableRow>
                                  </TableHeader>
                                  <TableBody>
                                    {openPorts.map((p: NetworkPort) => (
                                      <TableRow key={`${p.protocol}-${p.port}`}>
                                        <TableCell className="mono font-semibold text-primary">
                                          {p.port}
                                        </TableCell>
                                        <TableCell>
                                          <Badge variant="outline" className="text-[10px] uppercase">
                                            {p.protocol}
                                          </Badge>
                                        </TableCell>
                                        <TableCell className="mono text-sm">
                                          {p.service || '—'}
                                        </TableCell>
                                        <TableCell className="text-sm text-muted-foreground">
                                          {[p.product, p.version].filter(Boolean).join(' ') || '—'}
                                          {p.extraInfo ? (
                                            <span className="ml-1 text-xs opacity-60">
                                              ({p.extraInfo})
                                            </span>
                                          ) : null}
                                        </TableCell>
                                      </TableRow>
                                    ))}
                                  </TableBody>
                                </Table>
                              </div>
                            )}
                          </CardContent>
                        </Card>

                        {networkInfo.ports && networkInfo.ports.length > openPorts.length && (
                          <Card className="glass rounded-xl">
                            <CardContent className="space-y-3 pt-5">
                              <SectionHeading
                                icon={<Shield className="h-4 w-4" />}
                                title={`All Scanned Ports (${networkInfo.ports.length})`}
                              />
                              <div className="w-full max-w-full overflow-x-auto rounded-lg border border-border/60">
                                <Table>
                                  <TableHeader>
                                    <TableRow>
                                      <TableHead>Port</TableHead>
                                      <TableHead>Proto</TableHead>
                                      <TableHead>State</TableHead>
                                      <TableHead>Service</TableHead>
                                    </TableRow>
                                  </TableHeader>
                                  <TableBody>
                                    {networkInfo.ports.map((p: NetworkPort) => (
                                      <TableRow key={`all-${p.protocol}-${p.port}`}>
                                        <TableCell className="mono font-medium">{p.port}</TableCell>
                                        <TableCell>
                                          <Badge variant="outline" className="text-[10px] uppercase">
                                            {p.protocol}
                                          </Badge>
                                        </TableCell>
                                        <TableCell>
                                          <span
                                            className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                                              p.state === 'open'
                                                ? 'bg-success/20 text-success'
                                                : p.state === 'filtered'
                                                  ? 'bg-warning/20 text-warning'
                                                  : 'bg-muted text-muted-foreground'
                                            }`}
                                          >
                                            {p.state}
                                          </span>
                                        </TableCell>
                                        <TableCell className="mono text-sm text-muted-foreground">
                                          {p.service || '—'}
                                        </TableCell>
                                      </TableRow>
                                    ))}
                                  </TableBody>
                                </Table>
                              </div>
                            </CardContent>
                          </Card>
                        )}
                      </div>
                    )}
                  </>
                )}

                {activeTab === 'hardware' && isServerDevice && (
                  <div className="space-y-6">
                    {hardwareQuery.isLoading ? (
                      <LoadingState label="Loading hardware health telemetry…" />
                    ) : null}
                    {hardwareQuery.isError ? (
                      <ErrorState
                        message={
                          hardwareQuery.error instanceof Error
                            ? hardwareQuery.error.message
                            : 'Failed to load hardware health'
                        }
                        onRetry={() => void hardwareQuery.refetch()}
                      />
                    ) : null}
                    {!hardwareQuery.isLoading && !hardwareQuery.isError && !hardwareQuery.data ? (
                      <EmptyState
                        icon={Server}
                        title="Hardware Telemetry Unavailable"
                        description="No HPE iLO hardware snapshot is currently available for this server endpoint."
                      />
                    ) : null}

                    {hardwareQuery.data && (() => {
                      const hwData = hardwareQuery.data
                      const { freshness, collection, health, observedAt } = hwData

                      return (
                        <>
                          <Card className="glass rounded-xl border border-border/80">
                            <CardHeader className="pb-3">
                              <CardTitle className="flex items-center justify-between text-sm font-semibold">
                                <span className="flex items-center gap-2">
                                  <Activity className="h-4 w-4 text-primary" />
                                  iLO Telemetry & Collection Status
                                </span>
                                {freshness?.status === 'FRESH' && (
                                  <span className="inline-flex items-center rounded-md border border-success/30 bg-success/20 px-2 py-0.5 text-xs font-semibold text-success">
                                    Fresh
                                  </span>
                                )}
                                {freshness?.status === 'STALE' && (
                                  <span className="inline-flex items-center rounded-md border border-warning/30 bg-warning/20 px-2 py-0.5 text-xs font-semibold text-warning">
                                    Stale
                                  </span>
                                )}
                                {freshness?.status === 'FAILING' && (
                                  <span className="inline-flex items-center rounded-md border border-danger/30 bg-danger/20 px-2 py-0.5 text-xs font-semibold text-danger">
                                    Collection Failing
                                  </span>
                                )}
                                {freshness?.status === 'NEVER_POLLED' && (
                                  <span className="inline-flex items-center rounded-md border border-border bg-muted px-2 py-0.5 text-xs font-semibold text-muted-foreground">
                                    Never Polled
                                  </span>
                                )}
                              </CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-3 pt-0">
                              <div className="grid gap-3 sm:grid-cols-2">
                                <Meta
                                  label="Last Poll Attempt"
                                  value={collection?.lastAttemptAt ? formatRelative(collection.lastAttemptAt) : 'Never'}
                                  title={collection?.lastAttemptAt ? formatDateTime(collection.lastAttemptAt) : undefined}
                                  mono
                                />
                                <Meta
                                  label="Last Successful Poll"
                                  value={collection?.lastSuccessAt ? formatRelative(collection.lastSuccessAt) : 'Never'}
                                  title={collection?.lastSuccessAt ? formatDateTime(collection.lastSuccessAt) : undefined}
                                  mono
                                />
                                <Meta
                                  label="Last Hardware Observation"
                                  value={
                                    freshness?.observedAt || observedAt
                                      ? formatRelative(freshness?.observedAt || observedAt)
                                      : 'Never'
                                  }
                                  title={
                                    freshness?.observedAt || observedAt
                                      ? formatDateTime(freshness?.observedAt || observedAt)
                                      : undefined
                                  }
                                  mono
                                />
                                <Meta
                                  label="Consecutive Failures"
                                  value={String(collection?.consecutiveFailures ?? 0)}
                                />
                              </div>
                            </CardContent>
                          </Card>

                          {(freshness?.status === 'FAILING' || (collection?.consecutiveFailures ?? 0) > 0) && health !== null && (
                            <div className="rounded-xl border border-danger/40 bg-danger/10 p-4 text-xs text-danger-foreground">
                              <div className="flex items-center gap-2 font-semibold text-danger">
                                <AlertCircle className="h-4 w-4" />
                                <span>Hardware collection is failing</span>
                              </div>
                              <p className="mt-1 text-muted-foreground">
                                NetPulse could not retrieve the latest iLO telemetry.
                                {collection?.lastSuccessAt
                                  ? ` Last successful collection: ${formatRelative(collection.lastSuccessAt)} (${formatDateTime(collection.lastSuccessAt)}).`
                                  : ''}
                              </p>
                              {collection?.lastError && (
                                <div className="mt-2 rounded-lg border border-danger/30 bg-card/80 p-2.5 font-mono text-[11px] text-danger">
                                  <span className="mb-0.5 block text-[10px] font-semibold uppercase text-muted-foreground">
                                    Error Detail:
                                  </span>
                                  {collection.lastError}
                                </div>
                              )}
                            </div>
                          )}

                          {freshness?.isStale && health !== null && (
                            <div className="rounded-xl border border-warning/40 bg-warning/10 p-4 text-xs text-warning-foreground">
                              <div className="flex items-center gap-2 font-semibold text-warning">
                                <AlertTriangle className="h-4 w-4" />
                                <span>Telemetry is stale</span>
                              </div>
                              <p className="mt-1 text-muted-foreground">
                                The hardware information shown below may not reflect the server's current state. Telemetry is older than 30 minutes.
                              </p>
                            </div>
                          )}

                          {health === null && (collection?.consecutiveFailures ?? 0) > 0 ? (
                            <Card className="glass rounded-xl border border-danger/40 bg-danger/5">
                              <CardContent className="space-y-3 py-6 text-center">
                                <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-danger/20 text-danger">
                                  <AlertCircle className="h-5 w-5" />
                                </div>
                                <div>
                                  <h4 className="font-semibold text-foreground">Hardware polling is failing</h4>
                                  <p className="mt-1 text-xs text-muted-foreground">
                                    This server is configured for iLO monitoring, but NetPulse has not successfully collected hardware telemetry yet.
                                  </p>
                                </div>
                                {collection?.lastError && (
                                  <div className="mx-auto max-w-md rounded-lg border border-danger/30 bg-card p-3 text-left font-mono text-xs text-danger">
                                    <span className="mb-1 block text-[10px] font-semibold uppercase text-muted-foreground">
                                      Collection Error:
                                    </span>
                                    {collection.lastError}
                                  </div>
                                )}
                              </CardContent>
                            </Card>
                          ) : health === null ? (
                            <EmptyState
                              icon={Server}
                              title="Hardware telemetry has not been collected yet"
                              description="This server is configured for iLO monitoring. Telemetry has not been polled yet."
                            />
                          ) : null}

                          {health !== null && (
                            <>
                              <Card className="glass rounded-xl border-l-[3px] border-l-primary">
                                <CardHeader className="pb-2">
                                  <CardTitle className="flex items-center justify-between text-base">
                                    <span className="flex items-center gap-2">
                                      <Activity className="h-4 w-4 text-primary" />
                                      Overall Hardware Status
                                    </span>
                                    <span
                                      className={`inline-flex items-center rounded-md px-2.5 py-1 text-xs font-bold ${
                                        health.overallHealth === 'OK'
                                          ? 'border border-success/30 bg-success/20 text-success'
                                          : health.overallHealth === 'WARNING'
                                            ? 'border border-warning/30 bg-warning/20 text-warning'
                                            : health.overallHealth === 'CRITICAL'
                                              ? 'border border-danger/30 bg-danger/20 text-danger'
                                              : 'border border-border bg-muted text-muted-foreground'
                                      }`}
                                    >
                                      {health.overallHealth}
                                    </span>
                                  </CardTitle>
                                </CardHeader>
                                <CardContent className="space-y-3 pt-0">
                                  <div className="grid gap-3 sm:grid-cols-2">
                                    <Meta label="Power State" value={health.powerState || 'Unknown'} />
                                    <Meta
                                      label="Telemetry Timestamp"
                                      value={observedAt ? formatDateTime(observedAt) : '—'}
                                      mono
                                    />
                                  </div>

                                  {health.summaryReasons.length > 0 && (
                                    <div className="rounded-xl border border-warning/30 bg-warning/10 p-3 text-xs text-warning">
                                      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider">
                                        Status Reasons:
                                      </p>
                                      <ul className="list-inside list-disc space-y-0.5">
                                        {health.summaryReasons.map((r, idx) => (
                                          <li key={idx}>{r}</li>
                                        ))}
                                      </ul>
                                    </div>
                                  )}
                                </CardContent>
                              </Card>

                              <section className="space-y-3">
                                <SectionHeading icon={<Cpu className="h-4 w-4" />} title="Hardware Subsystems" />
                                <div className="grid gap-3 sm:grid-cols-2">
                                  {Object.entries(health.subsystems).map(([key, sub]) => (
                                    <div
                                      key={key}
                                      className="space-y-2 rounded-xl border border-border/60 bg-card p-3.5"
                                    >
                                      <div className="flex items-center justify-between">
                                        <span className="text-sm font-semibold capitalize">{sub.name}</span>
                                        <span
                                          className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-bold ${
                                            sub.status === 'OK'
                                              ? 'bg-success/20 text-success'
                                              : sub.status === 'WARNING'
                                                ? 'bg-warning/20 text-warning'
                                                : sub.status === 'CRITICAL'
                                                  ? 'bg-danger/20 text-danger'
                                                  : 'bg-muted text-muted-foreground'
                                          }`}
                                        >
                                          {sub.status}
                                        </span>
                                      </div>

                                      <div className="flex items-center gap-3 text-xs text-muted-foreground">
                                        <span>
                                          Total: <strong className="text-foreground">{sub.totalComponents}</strong>
                                        </span>
                                        {sub.healthyComponents > 0 && (
                                          <span className="text-success">Healthy: {sub.healthyComponents}</span>
                                        )}
                                        {sub.warningComponents > 0 && (
                                          <span className="font-medium text-warning">
                                            Warning: {sub.warningComponents}
                                          </span>
                                        )}
                                        {sub.criticalComponents > 0 && (
                                          <span className="font-medium text-danger">
                                            Critical: {sub.criticalComponents}
                                          </span>
                                        )}
                                      </div>

                                      {sub.reasons.length > 0 && (
                                        <ul className="mt-1 list-inside list-disc text-[11px] text-warning space-y-0.5">
                                          {sub.reasons.map((r, i) => (
                                            <li key={i}>{r}</li>
                                          ))}
                                        </ul>
                                      )}
                                    </div>
                                  ))}
                                </div>
                              </section>
                            </>
                          )}
                        </>
                      )
                    })()}
                  </div>
                )}
              </>
            ) : null}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-1.5 border-b-2 px-4 py-3 text-sm font-medium transition-colors ${
        active
          ? 'border-primary text-primary'
          : 'border-transparent text-muted-foreground hover:text-foreground'
      }`}
    >
      {children}
    </button>
  )
}

function SectionHeading({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <h4 className="flex items-center gap-2 text-sm font-semibold text-foreground">
      <span className="text-primary">{icon}</span>
      {title}
    </h4>
  )
}

function Meta({ label, value, mono, title }: { label: string; value: string; mono?: boolean; title?: string }) {
  return (
    <div className="rounded-xl border border-border/60 bg-secondary/30 px-3 py-2.5" title={title}>
      <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className={`mt-1 text-sm font-medium ${mono ? 'mono' : ''}`}>{value}</p>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border/60 bg-card px-3 py-3.5 text-center">
      <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-bold tracking-tight">{value}</p>
    </div>
  )
}
