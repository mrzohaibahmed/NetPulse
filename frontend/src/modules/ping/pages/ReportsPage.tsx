import { useMemo, useState } from 'react'
import {
  Calculator,
  Download,
  FileBarChart,
  FileSpreadsheet,
  Filter,
  Gauge,
  Server,
  WifiOff,
} from 'lucide-react'
import { useExportReports, useUptimeReportQuery } from '@/hooks/queries'
import { DEVICE_TYPES } from '@/modules/ping/constants/devices'
import { formatPercent } from '@/utils/format'
import type { UptimeRow } from '@/types'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { KpiCard } from '@/shared/components/KpiCard'
import { LoadingState } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/ui/card'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'

const REPORT_DEVICE_TYPES = ['all', ...DEVICE_TYPES] as const
const STATUSES = ['all', 'Online', 'Not Reachable', 'Offline (Critical)', 'Unknown']

export function ReportsPage() {
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [deviceType, setDeviceType] = useState('all')
  const [status, setStatus] = useState('all')
  const [enabled, setEnabled] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)

  const params = {
    startDate: startDate || undefined,
    endDate: endDate || undefined,
    deviceType,
    status,
  }

  const report = useUptimeReportQuery(params, enabled)
  const { exportDevices, exportHistory } = useExportReports()
  const rows = report.data ?? []
  const reportLoaded = enabled && !report.isFetching && !report.error && rows.length > 0

  const availability = useMemo(
    () => deriveAvailability(report.data ?? []),
    [report.data],
  )
  const topOffline = useMemo(() => {
    const source = report.data ?? []
    return [...source]
      .filter((r) => r.downtimePercentage != null)
      .sort((a, b) => (b.downtimePercentage ?? 0) - (a.downtimePercentage ?? 0))
      .slice(0, 5)
  }, [report.data])

  const runExport = async (key: string, fn: () => Promise<void>) => {
    setBusy(key)
    try {
      await fn()
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title="Ping Monitoring · Reports"
        description="Uptime analytics, offline hotspots, and inventory / history exports."
      />

      {/* 1. Filters */}
      <section className="space-y-4" aria-label="Filters">
        <SectionHeading
          title="Filters"
          description="Apply date and device filters to uptime calculations and exports."
        />
        <Card className="glass rounded-xl">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Filter className="h-4 w-4 text-primary" />
              Report scope
            </CardTitle>
            <CardDescription>Narrow the calculation window and device subset.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap items-end gap-3">
            <div className="space-y-1.5">
              <Label>From</Label>
              <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>To</Label>
              <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>Type</Label>
              <Select value={deviceType} onValueChange={setDeviceType}>
                <SelectTrigger className="w-[160px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {REPORT_DEVICE_TYPES.map((type) => (
                    <SelectItem key={type} value={type}>
                      {type === 'all' ? 'All types' : type}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Status</Label>
              <Select value={status} onValueChange={setStatus}>
                <SelectTrigger className="w-[180px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STATUSES.map((item) => (
                    <SelectItem key={item} value={item}>
                      {item === 'all' ? 'All statuses' : item}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              type="button"
              onClick={() => {
                setEnabled(true)
                void report.refetch()
              }}
            >
              <Calculator className="h-4 w-4" />
              Calculate uptime
            </Button>
          </CardContent>
        </Card>
      </section>

      {/* 2. Network Availability + 3. Response Time Summary (availability-focused; no RTT on UptimeRow) */}
      {reportLoaded ? (
        <>
          <section className="space-y-4" aria-label="Network availability">
            <SectionHeading
              title="Network Availability"
              description="Derived from the loaded uptime report rows."
            />
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <KpiCard
                label="Devices in report"
                value={availability.deviceCount}
                icon={Server}
                tone="accent"
              />
              <KpiCard
                label="Avg uptime"
                value={
                  availability.avgUptime == null ? '—' : formatPercent(availability.avgUptime)
                }
                icon={Gauge}
                tone="success"
              />
              <KpiCard
                label="100% uptime"
                value={availability.perfectUptime}
                icon={Gauge}
                tone="success"
                hint="Devices"
              />
              <KpiCard
                label="Avg downtime"
                value={
                  availability.avgDowntime == null ? '—' : formatPercent(availability.avgDowntime)
                }
                icon={WifiOff}
                tone={availability.avgDowntime && availability.avgDowntime > 0 ? 'warning' : 'default'}
              />
            </div>
          </section>

          <section className="space-y-4" aria-label="Availability summary">
            <SectionHeading
              title="Availability Summary"
              description="UptimeRow has no response-time fields — showing check and reachability KPIs instead."
            />
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <KpiCard
                label="Total checks"
                value={availability.totalChecks}
                icon={FileBarChart}
                tone="accent"
              />
              <KpiCard
                label="Online checks"
                value={availability.onlineChecks}
                icon={Gauge}
                tone="success"
              />
              <KpiCard
                label="Downtime checks"
                value={availability.downtimeChecks}
                icon={WifiOff}
                tone={availability.downtimeChecks > 0 ? 'danger' : 'default'}
              />
              <KpiCard
                label="With downtime"
                value={availability.withDowntime}
                icon={WifiOff}
                tone={availability.withDowntime > 0 ? 'warning' : 'default'}
                hint="Devices"
              />
            </div>
          </section>

          {/* 4. Top Offline Devices */}
          <section className="space-y-4" aria-label="Top offline devices">
            <SectionHeading
              title="Top Offline Devices"
              description="Highest downtime percentage in this report window."
            />
            <Card className="glass rounded-xl">
              <CardContent className="pt-5">
                {topOffline.length === 0 ? (
                  <EmptyState title="No downtime recorded" description="All devices show zero downtime." />
                ) : (
                  <ul className="space-y-3">
                    {topOffline.map((row, index) => (
                      <li
                        key={row.deviceId}
                        className="flex flex-wrap items-center gap-3 rounded-xl border border-border/60 bg-secondary/20 px-4 py-3"
                      >
                        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-danger/15 text-sm font-bold text-danger">
                          {index + 1}
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="font-semibold">{row.hostname}</p>
                          <p className="mono text-xs text-muted-foreground">{row.ipAddress}</p>
                        </div>
                        <StatusBadge status={row.status} pulse={false} />
                        <div className="text-right">
                          <p className="text-sm font-bold text-danger">
                            {row.downtimePercentage == null
                              ? '—'
                              : formatPercent(row.downtimePercentage)}
                          </p>
                          <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
                            Downtime
                          </p>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          </section>
        </>
      ) : null}

      {/* 5. Report Export */}
      <section className="space-y-4" aria-label="Report export">
        <SectionHeading
          title="Report Export"
          description="Download device inventory and status logs for the selected filters."
        />
        <div className="grid gap-4 md:grid-cols-2">
          <Card className="glass rounded-xl hover:border-primary/40">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <FileSpreadsheet className="h-5 w-5 text-primary" />
                Devices export
              </CardTitle>
              <CardDescription>Download the current device inventory.</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="secondary"
                disabled={busy !== null}
                onClick={() =>
                  void runExport('devices-csv', () =>
                    exportDevices({ deviceType, status, format: 'csv' }),
                  )
                }
              >
                <Download className="h-4 w-4" />
                {busy === 'devices-csv' ? 'Exporting…' : 'CSV'}
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={busy !== null}
                onClick={() =>
                  void runExport('devices-xlsx', () =>
                    exportDevices({ deviceType, status, format: 'xlsx' }),
                  )
                }
              >
                <Download className="h-4 w-4" />
                {busy === 'devices-xlsx' ? 'Exporting…' : 'Excel'}
              </Button>
            </CardContent>
          </Card>

          <Card className="glass rounded-xl hover:border-primary/40">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Download className="h-5 w-5 text-primary" />
                Status logs export
              </CardTitle>
              <CardDescription>Export ping history for the selected range.</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="secondary"
                disabled={busy !== null}
                onClick={() =>
                  void runExport('history-csv', () => exportHistory({ ...params, format: 'csv' }))
                }
              >
                <Download className="h-4 w-4" />
                {busy === 'history-csv' ? 'Exporting…' : 'CSV'}
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={busy !== null}
                onClick={() =>
                  void runExport('history-xlsx', () => exportHistory({ ...params, format: 'xlsx' }))
                }
              >
                <Download className="h-4 w-4" />
                {busy === 'history-xlsx' ? 'Exporting…' : 'Excel'}
              </Button>
            </CardContent>
          </Card>
        </div>
      </section>

      <section className="space-y-4" aria-label="Uptime detail">
        <SectionHeading
          title="Uptime / Downtime by Device"
          description="Per-host availability for the calculated window."
        />
        <Card className="glass overflow-hidden rounded-xl">
          <CardContent className="pt-5">
            {report.isFetching ? (
              <LoadingState label="Calculating…" />
            ) : report.error ? (
              <ErrorState
                message={report.error instanceof Error ? report.error.message : 'Failed to load report'}
                onRetry={() => void report.refetch()}
              />
            ) : !enabled || rows.length === 0 ? (
              <EmptyState
                title="No report loaded"
                description="Set filters and click Calculate uptime."
              />
            ) : (
              <div className="overflow-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Hostname</TableHead>
                      <TableHead>IP</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Checks</TableHead>
                      <TableHead>Uptime</TableHead>
                      <TableHead>Downtime</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rows.map((row) => (
                      <TableRow key={row.deviceId}>
                        <TableCell className="font-semibold">{row.hostname}</TableCell>
                        <TableCell className="mono text-muted-foreground">{row.ipAddress}</TableCell>
                        <TableCell>{row.deviceType}</TableCell>
                        <TableCell>
                          <StatusBadge status={row.status} />
                        </TableCell>
                        <TableCell>{row.totalChecks}</TableCell>
                        <TableCell>
                          {row.uptimePercentage === null ? '—' : formatPercent(row.uptimePercentage)}
                        </TableCell>
                        <TableCell>
                          {row.downtimePercentage === null
                            ? '—'
                            : formatPercent(row.downtimePercentage)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      </section>
    </div>
  )
}

function deriveAvailability(rows: UptimeRow[]) {
  const withUptime = rows.filter((r) => r.uptimePercentage != null)
  const withDowntimePct = rows.filter((r) => r.downtimePercentage != null)
  const avgUptime =
    withUptime.length === 0
      ? null
      : withUptime.reduce((sum, r) => sum + (r.uptimePercentage ?? 0), 0) / withUptime.length
  const avgDowntime =
    withDowntimePct.length === 0
      ? null
      : withDowntimePct.reduce((sum, r) => sum + (r.downtimePercentage ?? 0), 0) /
        withDowntimePct.length

  return {
    deviceCount: rows.length,
    avgUptime,
    avgDowntime,
    perfectUptime: rows.filter((r) => r.uptimePercentage === 100).length,
    totalChecks: rows.reduce((sum, r) => sum + (r.totalChecks ?? 0), 0),
    onlineChecks: rows.reduce((sum, r) => sum + (r.onlineChecks ?? 0), 0),
    downtimeChecks: rows.reduce((sum, r) => sum + (r.downtimeChecks ?? 0), 0),
    withDowntime: rows.filter((r) => (r.downtimePercentage ?? 0) > 0).length,
  }
}

function SectionHeading({ title, description }: { title: string; description: string }) {
  return (
    <div>
      <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
      <p className="text-sm text-muted-foreground">{description}</p>
    </div>
  )
}
