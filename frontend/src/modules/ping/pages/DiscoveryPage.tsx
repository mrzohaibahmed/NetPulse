import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  CheckCircle2,
  Loader2,
  Network,
  Radar,
  Sparkles,
  Table2,
  Upload,
} from 'lucide-react'
import { useAuth } from '@/shared/auth/AuthContext'
import { useClientPagination } from '@/hooks/useClientPagination'
import { useDiscoveryMutation, useNetworkHintQuery } from '@/hooks/queries'
import { formatMs } from '@/utils/format'
import type { DiscoveryDevice, DiscoverySummary } from '@/types'
import { EmptyState } from '@/shared/components/EmptyState'
import { KpiCard } from '@/shared/components/KpiCard'
import { PageHeader } from '@/shared/components/PageHeader'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/ui/card'
import { Checkbox } from '@/shared/ui/checkbox'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'
import { Progress } from '@/shared/ui/progress'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import { toast } from 'sonner'

export function DiscoveryPage() {
  const { isAdmin } = useAuth()
  const hintQuery = useNetworkHintQuery(isAdmin)
  const discovery = useDiscoveryMutation()

  const [startIP, setStartIP] = useState('')
  const [endIP, setEndIP] = useState('')
  const [summary, setSummary] = useState<DiscoverySummary | null>(null)
  const [devices, setDevices] = useState<DiscoveryDevice[]>([])
  const [showOnlineOnly, setShowOnlineOnly] = useState(false)
  const [progress, setProgress] = useState(0)

  useEffect(() => {
    if (hintQuery.data) {
      setStartIP(hintQuery.data.startIP)
      setEndIP(hintQuery.data.endIP)
    } else if (hintQuery.isError) {
      setStartIP((v) => v || '192.168.0.1')
      setEndIP((v) => v || '192.168.0.254')
    }
  }, [hintQuery.data, hintQuery.isError])

  useEffect(() => {
    if (!discovery.isPending) {
      setProgress(0)
      return
    }
    setProgress(8)
    const timer = window.setInterval(() => {
      setProgress((p) => (p >= 92 ? p : p + Math.random() * 8))
    }, 600)
    return () => window.clearInterval(timer)
  }, [discovery.isPending])

  const visible = useMemo(
    () => (showOnlineOnly ? devices.filter((d) => d.status === 'Online') : devices),
    [devices, showOnlineOnly],
  )
  const pagination = useClientPagination(visible, 25)
  const { reset } = pagination

  useEffect(() => {
    reset()
  }, [showOnlineOnly, devices, reset])

  if (!isAdmin) return <Navigate to="/" replace />

  const applyNetworkHint = () => {
    if (!hintQuery.data) return
    setStartIP(hintQuery.data.startIP)
    setEndIP(hintQuery.data.endIP)
    toast.info(`Using detected network ${hintQuery.data.network}`)
  }

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setSummary(null)
    setDevices([])
    try {
      const result = await discovery.mutateAsync({
        startIP: startIP.trim(),
        endIP: endIP.trim(),
      })
      setProgress(100)
      setSummary(result.summary)
      setDevices(result.devices)
      if (result.summary.online === 0) {
        toast.message('No online hosts found in this range.')
      } else if ((result.summary.newlySaved ?? 0) === 0) {
        toast.success('Scan complete. All online hosts were already monitored.')
      } else {
        toast.success(`Scan complete. Saved ${result.summary.newlySaved} new device(s).`)
      }
    } catch {
      /* toast handled in mutation */
    }
  }

  const savedCount = summary?.newlySaved ?? devices.filter((d) => d.saved).length
  const scanning = discovery.isPending

  return (
    <div className="space-y-8">
      <PageHeader
        title="Ping Monitoring · Discovery"
        description="Scan an IPv4 range, detect online hosts, and auto-save new devices to inventory."
      />

      {/* 1. Network Range */}
      <section className="space-y-4" aria-label="Network range">
        <SectionHeading
          title="Network Range"
          description="Define the IPv4 window to probe. Scans cover up to 1024 addresses."
        />
        <Card className="glass rounded-xl">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Network className="h-5 w-5 text-primary" />
              Scan parameters
            </CardTitle>
            <CardDescription>
              {hintQuery.data ? (
                <>
                  Detected local network:{' '}
                  <span className="mono text-foreground">{hintQuery.data.network}</span> (this machine
                  is <span className="mono text-foreground">{hintQuery.data.localIP}</span>)
                </>
              ) : (
                'Online hosts not already monitored are saved automatically.'
              )}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={(e) => void onSubmit(e)}>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="startIP">Start IP</Label>
                  <Input
                    id="startIP"
                    className="mono"
                    required
                    value={startIP}
                    onChange={(e) => setStartIP(e.target.value)}
                    disabled={scanning}
                    placeholder="192.168.0.1"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="endIP">End IP</Label>
                  <Input
                    id="endIP"
                    className="mono"
                    required
                    value={endIP}
                    onChange={(e) => setEndIP(e.target.value)}
                    disabled={scanning}
                    placeholder="192.168.0.254"
                  />
                </div>
              </div>

              <div className="flex flex-wrap justify-end gap-2">
                {hintQuery.data ? (
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={scanning}
                    onClick={applyNetworkHint}
                  >
                    Use detected network
                  </Button>
                ) : null}
                <Button type="submit" disabled={scanning || !startIP || !endIP}>
                  {scanning ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                  {scanning ? 'Scanning…' : 'Start scan'}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </section>

      {/* 2. Discovery Progress */}
      {scanning ? (
        <section className="space-y-4" aria-label="Discovery progress">
          <SectionHeading
            title="Discovery Progress"
            description="Probing hosts in parallel across the selected range."
          />
          <Card className="glass overflow-hidden rounded-xl border-l-[3px] border-l-primary">
            <CardContent className="space-y-6 py-8">
              <div className="flex flex-col items-center gap-4 text-center sm:flex-row sm:text-left">
                <motion.div
                  animate={{ rotate: 360 }}
                  transition={{ repeat: Infinity, duration: 2, ease: 'linear' }}
                  className="flex h-16 w-16 shrink-0 items-center justify-center rounded-2xl bg-primary/15 text-primary shadow-inner"
                >
                  <Radar className="h-8 w-8" />
                </motion.div>
                <div className="min-w-0 flex-1 space-y-1">
                  <p className="text-lg font-semibold tracking-tight">Scanning range</p>
                  <p className="mono text-sm text-muted-foreground">
                    {startIP} → {endIP}
                  </p>
                  <p className="text-sm text-muted-foreground">Pinging hosts in parallel…</p>
                </div>
                <div className="text-center sm:text-right">
                  <p className="text-3xl font-bold tabular-nums tracking-tight text-primary">
                    {Math.round(progress)}%
                  </p>
                  <p className="text-xs uppercase tracking-wider text-muted-foreground">Complete</p>
                </div>
              </div>
              <div className="space-y-2">
                <Progress value={progress} className="h-3" />
                <div className="flex justify-between text-xs text-muted-foreground">
                  <span>Initializing sweep</span>
                  <span>Awaiting results</span>
                </div>
              </div>
            </CardContent>
          </Card>
        </section>
      ) : null}

      {/* 3. Discovered Devices */}
      {devices.length > 0 ? (
        <section className="space-y-4" aria-label="Discovered devices">
          <SectionHeading
            title="Discovered Devices"
            description="Hosts found in the scanned range."
          />

          <Card className="glass rounded-xl">
            <CardContent className="flex flex-wrap items-center gap-3 py-4">
              <label className="flex items-center gap-2 text-sm text-muted-foreground">
                <Checkbox
                  checked={showOnlineOnly}
                  onCheckedChange={(checked) => setShowOnlineOnly(Boolean(checked))}
                />
                Show online hosts only
              </label>
              <span className="text-sm text-muted-foreground">
                {visible.length} of {devices.length} hosts
              </span>
            </CardContent>
          </Card>

          {visible.length === 0 ? (
            <EmptyState title="No online hosts in this range" />
          ) : (
            <Card className="glass overflow-hidden rounded-xl">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Table2 className="h-4 w-4 text-primary" />
                  Host table
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0 pt-0">
                <div className="overflow-auto px-2 pb-2">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>IP</TableHead>
                        <TableHead>Hostname</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>RTT</TableHead>
                        <TableHead>Saved</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {pagination.pageItems.map((device) => (
                        <TableRow key={device.ipAddress}>
                          <TableCell className="mono">{device.ipAddress}</TableCell>
                          <TableCell className="font-medium">{device.hostname ?? '—'}</TableCell>
                          <TableCell>
                            <StatusBadge status={device.status} />
                          </TableCell>
                          <TableCell className="mono">{formatMs(device.responseTime)}</TableCell>
                          <TableCell>
                            {device.saved ? (
                              <Badge variant="default">New device</Badge>
                            ) : device.status === 'Online' ? (
                              <span className="text-sm text-muted-foreground">Already monitored</span>
                            ) : (
                              <span className="text-sm text-muted-foreground">—</span>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <div className="border-t border-border px-4 pb-2">
                  <PaginationControls
                    page={pagination.page}
                    totalPages={pagination.totalPages}
                    total={pagination.total}
                    limit={pagination.limit}
                    onPageChange={pagination.setPage}
                    onLimitChange={pagination.setLimit}
                  />
                </div>
              </CardContent>
            </Card>
          )}
        </section>
      ) : null}

      {/* 4. Import Results */}
      {summary ? (
        <section className="space-y-4" aria-label="Import results">
          <SectionHeading
            title="Import Results"
            description="Outcome of the latest discovery sweep."
          />
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <KpiCard label="Scanned" value={summary.totalScanned} icon={Radar} tone="accent" />
            <KpiCard label="Online" value={summary.online} icon={CheckCircle2} tone="success" />
            <KpiCard label="Offline" value={summary.offline} icon={Network} tone="danger" />
            <KpiCard label="Newly saved" value={savedCount} icon={Upload} tone="accent" />
          </div>
        </section>
      ) : null}
    </div>
  )
}

function SectionHeading({ title, description }: { title: string; description: string }) {
  return (
    <div>
      <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
      <p className="text-sm text-muted-foreground">{description}</p>
    </div>
  )
}
