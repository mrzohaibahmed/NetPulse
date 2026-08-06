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
import { useDeviceHistoryQuery, useDeviceMutations, useNmapScanMutation } from '@/hooks/queries'
import { useAuth } from '@/shared/auth/AuthContext'
import { formatDateTime, formatMs, formatPercent, formatRelative } from '@/utils/format'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { LoadingState } from '@/shared/components/LoadingState'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
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
import { Separator } from '@/shared/ui/separator'
import type { NetworkInfo, NetworkPort } from '@/types'

interface DeviceDrawerProps {
  deviceId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

type TabId = 'overview' | 'network'

export function DeviceDrawer({ deviceId, open, onOpenChange }: DeviceDrawerProps) {
  const { isOperator } = useAuth()
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

  const openPorts = networkInfo?.ports?.filter((p) => p.state === 'open') ?? []

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-full flex-col p-0 sm:max-w-xl md:max-w-2xl">
        {/* Header */}
        <SheetHeader className="border-b border-border p-6 pb-4">
          <SheetTitle>{device?.hostname ?? 'Device details'}</SheetTitle>
          <SheetDescription className="mono">
            {device ? `${device.ipAddress} · ${device.deviceType}` : 'Loading device telemetry…'}
          </SheetDescription>
        </SheetHeader>

        {/* Tab bar */}
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
        </div>

        <ScrollArea className="flex-1">
          <div className="space-y-5 p-6">
            {query.isLoading && !data ? <LoadingState label="Loading device history…" /> : null}
            {query.error && !data ? (
              <ErrorState
                message={query.error instanceof Error ? query.error.message : 'Failed to load'}
                onRetry={() => void query.refetch()}
              />
            ) : null}

            {device ? (
              <>
                {/* ── Status bar (always visible) ── */}
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge status={device.status} />
                  {device.critical ? <Badge variant="danger">Critical</Badge> : null}
                  <Badge variant={device.monitor ? 'success' : 'muted'}>
                    {device.monitor ? 'Monitored' : 'Not monitored'}
                  </Badge>
                  <div className="ml-auto flex gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      disabled={scan.isPending}
                      onClick={() => scan.mutate(device._id)}
                    >
                      {scan.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Radar className="h-4 w-4" />}
                      Ping now
                    </Button>
                    {isOperator ? (
                    <Button
                      type="button"
                      size="sm"
                      variant={networkInfo ? 'secondary' : 'default'}
                      disabled={nmapScan.isPending || device.status !== 'Online'}
                      title={device.status !== 'Online' ? 'Device must be Online to run Nmap' : undefined}
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
                </div>

                {/* ── OVERVIEW TAB ── */}
                {activeTab === 'overview' && (
                  <>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <Meta label="Hostname" value={device.hostname} />
                      <Meta label="IP address" value={device.ipAddress} mono />
                      <Meta label="Device type" value={device.deviceType} />
                      <Meta label="Vendor" value={device.credentials?.sshVendor || '—'} />
                      <Meta
                        label="SSH Username"
                        value={device.credentials?.sshUsername || '—'}
                      />
                      <Meta
                        label="SSH Password"
                        value={device.credentials?.sshPasswordConfigured ? '••••••••' : 'Not Configured'}
                      />
                      <Meta label="Monitor" value={device.monitor ? 'Enabled' : 'Disabled'} />
                      <Meta label="Last seen" value={formatRelative(device.lastSeen)} />
                      <Meta label="Response time" value={formatMs(device.responseTime)} mono />
                      <Meta label="Consecutive failures" value={String(device.consecutiveFailures ?? 0)} />
                    </div>

                    <Separator />

                    <div className="grid gap-3 sm:grid-cols-3">
                      <Stat label="Uptime" value={formatPercent(data?.uptime.uptimePercentage)} />
                      <Stat label="Downtime" value={formatPercent(data?.uptime.downtimePercentage)} />
                      <Stat label="Checks" value={String(data?.uptime.totalChecks ?? 0)} />
                    </div>

                    <div className="flex flex-wrap items-end gap-3">
                      <div className="space-y-1.5">
                        <Label htmlFor="drawer-from">From</Label>
                        <Input id="drawer-from" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
                      </div>
                      <div className="space-y-1.5">
                        <Label htmlFor="drawer-to">To</Label>
                        <Input id="drawer-to" type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
                      </div>
                      <Button type="button" variant="secondary" onClick={() => void query.refetch()}>
                        Apply
                      </Button>
                    </div>

                    <div>
                      <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                        <Activity className="h-4 w-4 text-primary" />
                        Response time trend
                      </h4>
                      {(data?.responseTimeTrend.length ?? 0) === 0 ? (
                        <EmptyState title="No successful pings in this range" className="py-8" />
                      ) : (
                        <div className="h-56 rounded-lg border border-border/60 bg-secondary/20 p-2">
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
                                contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8 }}
                              />
                              <Line type="monotone" dataKey="responseTime" stroke="#3B82F6" strokeWidth={2} dot={false} />
                            </LineChart>
                          </ResponsiveContainer>
                        </div>
                      )}
                    </div>

                    <div>
                      <h4 className="mb-3 text-sm font-semibold">Recent scans</h4>
                      {(data?.history.length ?? 0) === 0 ? (
                        <EmptyState title="No history yet" className="py-8" />
                      ) : (
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
                                  <TableCell className="text-muted-foreground">{formatDateTime(row.timestamp)}</TableCell>
                                  <TableCell>
                                    <StatusBadge status={row.status} pulse={false} />
                                  </TableCell>
                                  <TableCell className="mono">{formatMs(row.responseTime)}</TableCell>
                                  <TableCell>{row.scanType}</TableCell>
                                </TableRow>
                              ))}
                          </TableBody>
                        </Table>
                      )}
                    </div>
                  </>
                )}

                {/* ── NETWORK INFO TAB ── */}
                {activeTab === 'network' && (
                  <>
                    {!networkInfo ? (
                      <div className="flex flex-col items-center gap-4 rounded-xl border border-dashed border-border py-14 text-center">
                        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
                          <ScanLine className="h-6 w-6 text-primary" />
                        </div>
                        <div>
                          <p className="font-semibold">No scan data yet</p>
                          <p className="mt-1 text-sm text-muted-foreground">
                            Click <strong>Nmap scan</strong> above to discover OS, ports, and services.
                            {device.status !== 'Online' && (
                              <span className="mt-1 block text-warning"> Device must be Online to scan.</span>
                            )}
                          </p>
                        </div>
                      </div>
                    ) : (
                      <div className="space-y-5">
                        {/* Last scan timestamp */}
                        <p className="text-xs text-muted-foreground">
                          Last scanned: {networkInfo.lastScan ? formatDateTime(networkInfo.lastScan) : '—'}
                        </p>

                        {/* OS & Identity */}
                        <section>
                          <SectionHeading icon={<Cpu className="h-4 w-4" />} title="Operating System" />
                          <div className="grid gap-3 sm:grid-cols-2">
                            <Meta label="OS name" value={networkInfo.os?.name || '—'} />
                            <Meta label="OS family" value={networkInfo.os?.family || '—'} />
                            <Meta label="Generation" value={networkInfo.os?.generation || '—'} />
                            <Meta
                              label="Detection accuracy"
                              value={networkInfo.os?.accuracy ? `${networkInfo.os.accuracy}%` : '—'}
                            />
                          </div>
                        </section>

                        <Separator />

                        {/* Device / Network identity */}
                        <section>
                          <SectionHeading icon={<Server className="h-4 w-4" />} title="Device Identity" />
                          <div className="grid gap-3 sm:grid-cols-2">
                            <Meta label="Nmap hostname" value={networkInfo.hostname || '—'} />
                            <Meta label="Device class" value={networkInfo.deviceType || '—'} />
                            <Meta label="MAC address" value={networkInfo.macAddress || '—'} mono />
                            <Meta label="Vendor" value={networkInfo.vendor || '—'} />
                          </div>
                        </section>

                        <Separator />

                        {/* Services */}
                        {networkInfo.services && networkInfo.services.length > 0 && (
                          <>
                            <section>
                              <SectionHeading icon={<Globe className="h-4 w-4" />} title="Detected Services" />
                              <div className="flex flex-wrap gap-2">
                                {networkInfo.services.map((svc) => (
                                  <Badge key={svc} variant="secondary" className="font-mono text-xs">
                                    {svc}
                                  </Badge>
                                ))}
                              </div>
                            </section>
                            <Separator />
                          </>
                        )}

                        {/* Ports table */}
                        <section>
                          <SectionHeading
                            icon={<Wifi className="h-4 w-4" />}
                            title={`Open Ports (${openPorts.length})`}
                          />
                          {openPorts.length === 0 ? (
                            <EmptyState title="No open ports detected" className="py-6" />
                          ) : (
                            <div className="overflow-auto rounded-lg border border-border/60">
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
                                      <TableCell className="mono text-sm">{p.service || '—'}</TableCell>
                                      <TableCell className="text-sm text-muted-foreground">
                                        {[p.product, p.version].filter(Boolean).join(' ') || '—'}
                                        {p.extraInfo ? (
                                          <span className="ml-1 text-xs opacity-60">({p.extraInfo})</span>
                                        ) : null}
                                      </TableCell>
                                    </TableRow>
                                  ))}
                                </TableBody>
                              </Table>
                            </div>
                          )}
                        </section>

                        {/* All ports (collapsed view) */}
                        {networkInfo.ports && networkInfo.ports.length > openPorts.length && (
                          <section>
                            <SectionHeading
                              icon={<Shield className="h-4 w-4" />}
                              title={`All Scanned Ports (${networkInfo.ports.length})`}
                            />
                            <div className="overflow-auto rounded-lg border border-border/60">
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
                          </section>
                        )}
                      </div>
                    )}
                  </>
                )}
              </>
            ) : null}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────

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
    <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
      <span className="text-primary">{icon}</span>
      {title}
    </h4>
  )
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="rounded-lg border border-border/60 bg-secondary/30 px-3 py-2">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={`mt-0.5 text-sm font-medium ${mono ? 'mono' : ''}`}>{value}</p>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border/60 bg-card px-3 py-3 text-center">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-bold">{value}</p>
    </div>
  )
}
