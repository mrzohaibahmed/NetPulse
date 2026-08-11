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
import {
  useExportReports,
  useMitigationHistoryQuery,
  useRecoveryHistoryQuery,
  useStormIncidentsQuery,
  useUptimeReportQuery,
} from '@/hooks/queries'
import { DEVICE_TYPES } from '@/modules/ping/constants/devices'
import { formatDateTime, formatPercent } from '@/utils/format'
import type { MitigationLog, RecoveryLog, StormIncident, UptimeRow } from '@/types'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { KpiCard } from '@/shared/components/KpiCard'
import { LoadingState } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { SectionHeading } from '@/shared/components/SectionHeading'
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
  const { exportDevices, exportHistory, exportStormIncidents, exportStormMitigations, exportStormRecoveries } =
    useExportReports()
  const STORM_REPORT_LIMIT = 10
  const stormIncidents = useStormIncidentsQuery({ page: 1, limit: STORM_REPORT_LIMIT })
  const mitigationHistory = useMitigationHistoryQuery({ page: 1, limit: STORM_REPORT_LIMIT })
  const recoveryHistory = useRecoveryHistoryQuery({ page: 1, limit: STORM_REPORT_LIMIT })
  const rows = report.data ?? []
  const incidentRows = stormIncidents.data?.data ?? []
  const mitigationRows = mitigationHistory.data?.data ?? []
  const recoveryRows = recoveryHistory.data?.data ?? []
  const reportLoaded = enabled && !report.isFetching && !report.error && rows.length > 0

  const availability = useMemo(() => deriveAvailability(report.data ?? []), [report.data])
  const topOffline = useMemo(() => {
    const source = report.data ?? []
    return [...source]
      .filter((r) => r.downtimePercentage != null)
      .sort((a, b) => (b.downtimePercentage ?? 0) - (a.downtimePercentage ?? 0))
      .slice(0, 5)
  }, [report.data])
  const stormSummary = useMemo(
    () => deriveStormSummary(incidentRows, mitigationRows, recoveryRows),
    [incidentRows, mitigationRows, recoveryRows],
  )

  const runExport = async (key: string, fn: () => Promise<void>) => {
    setBusy(key)
    try {
      await fn()
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="np-page">
      <PageHeader
        title="Reports"
        description="Separate reporting views for ping monitoring and storm protection."
      />

      <section className="space-y-4" aria-label="Ping monitoring reports">
        <SectionHeading
          title="Ping Monitoring Reports"
          description="Uptime analytics, offline hotspots, and inventory / history exports."
        />

        <section className="space-y-4" aria-label="Ping monitoring filters">
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
                  tone={
                    availability.avgDowntime && availability.avgDowntime > 0 ? 'warning' : 'default'
                  }
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
      </section>

      <section className="space-y-4" aria-label="Storm protection reports">
        <SectionHeading
          title="Storm Protection Reports"
          description="Separate storm reporting for incidents, mitigation activity, and recovery history."
        />
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <KpiCard label="Open incidents" value={stormSummary.openIncidents} icon={WifiOff} tone="danger" />
          <KpiCard
            label="Critical incidents"
            value={stormSummary.criticalIncidents}
            icon={FileBarChart}
            tone={stormSummary.criticalIncidents > 0 ? 'warning' : 'default'}
          />
          <KpiCard
            label="Mitigation success"
            value={formatRatio(stormSummary.successfulMitigations, stormSummary.totalMitigations)}
            icon={Gauge}
            tone="success"
          />
          <KpiCard
            label="Recovery success"
            value={formatRatio(stormSummary.successfulRecoveries, stormSummary.totalRecoveries)}
            icon={Server}
            tone="accent"
          />
        </div>

        <div className="grid gap-4 xl:grid-cols-3">
          <Card className="glass overflow-hidden rounded-xl">
            <CardHeader>
              <CardTitle className="text-base">Recent Incidents</CardTitle>
              <CardDescription>Latest storm incidents detected by the protection pipeline.</CardDescription>
              <div className="flex flex-wrap gap-2 pt-2">
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy !== null}
                  onClick={() =>
                    void runExport('storm-incidents-csv', () =>
                      exportStormIncidents({ limit: STORM_REPORT_LIMIT, format: 'csv' }),
                    )
                  }
                >
                  <Download className="h-4 w-4" />
                  {busy === 'storm-incidents-csv' ? 'Exporting…' : 'CSV'}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy !== null}
                  onClick={() =>
                    void runExport('storm-incidents-xlsx', () =>
                      exportStormIncidents({ limit: STORM_REPORT_LIMIT, format: 'xlsx' }),
                    )
                  }
                >
                  <Download className="h-4 w-4" />
                  {busy === 'storm-incidents-xlsx' ? 'Exporting…' : 'Excel'}
                </Button>
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              <StormReportTable
                isLoading={stormIncidents.isLoading}
                error={stormIncidents.error}
                emptyTitle="No incidents found"
                emptyDescription="Storm incidents will appear here after the protection pipeline detects them."
                columns={['Incident', 'Switch', 'Interface', 'Severity', 'Status', 'Created']}
                rows={incidentRows.map((row) => ({
                  key: row.incidentId,
                  cells: [
                    row.incidentId,
                    row.hostname || 'Unknown switch',
                    row.interface,
                    row.severity,
                    row.status,
                    formatDateTime(row.createdAt),
                  ],
                }))}
                statusColumn={4}
              />
            </CardContent>
          </Card>

          <Card className="glass overflow-hidden rounded-xl">
            <CardHeader>
              <CardTitle className="text-base">Recent Mitigations</CardTitle>
              <CardDescription>Latest containment actions executed for storm incidents.</CardDescription>
              <div className="flex flex-wrap gap-2 pt-2">
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy !== null}
                  onClick={() =>
                    void runExport('storm-mitigations-csv', () =>
                      exportStormMitigations({ limit: STORM_REPORT_LIMIT, format: 'csv' }),
                    )
                  }
                >
                  <Download className="h-4 w-4" />
                  {busy === 'storm-mitigations-csv' ? 'Exporting…' : 'CSV'}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy !== null}
                  onClick={() =>
                    void runExport('storm-mitigations-xlsx', () =>
                      exportStormMitigations({ limit: STORM_REPORT_LIMIT, format: 'xlsx' }),
                    )
                  }
                >
                  <Download className="h-4 w-4" />
                  {busy === 'storm-mitigations-xlsx' ? 'Exporting…' : 'Excel'}
                </Button>
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              <StormReportTable
                isLoading={mitigationHistory.isLoading}
                error={mitigationHistory.error}
                emptyTitle="No mitigation history"
                emptyDescription="Mitigation events will appear here when storm actions are executed."
                columns={['Incident', 'Interface', 'Strategy', 'Status', 'Operator', 'Time']}
                rows={mitigationRows.map((row) => ({
                  key: `${row.incidentId}-${row.timestamp}`,
                  cells: [
                    row.incidentId,
                    row.interface,
                    row.strategy,
                    row.status,
                    row.operator,
                    formatDateTime(row.timestamp),
                  ],
                }))}
                statusColumn={3}
              />
            </CardContent>
          </Card>

          <Card className="glass overflow-hidden rounded-xl">
            <CardHeader>
              <CardTitle className="text-base">Recent Recoveries</CardTitle>
              <CardDescription>Latest restoration activity after protected storm events.</CardDescription>
              <div className="flex flex-wrap gap-2 pt-2">
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy !== null}
                  onClick={() =>
                    void runExport('storm-recoveries-csv', () =>
                      exportStormRecoveries({ limit: STORM_REPORT_LIMIT, format: 'csv' }),
                    )
                  }
                >
                  <Download className="h-4 w-4" />
                  {busy === 'storm-recoveries-csv' ? 'Exporting…' : 'CSV'}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy !== null}
                  onClick={() =>
                    void runExport('storm-recoveries-xlsx', () =>
                      exportStormRecoveries({ limit: STORM_REPORT_LIMIT, format: 'xlsx' }),
                    )
                  }
                >
                  <Download className="h-4 w-4" />
                  {busy === 'storm-recoveries-xlsx' ? 'Exporting…' : 'Excel'}
                </Button>
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              <StormReportTable
                isLoading={recoveryHistory.isLoading}
                error={recoveryHistory.error}
                emptyTitle="No recovery history"
                emptyDescription="Recovery events will appear here after rollback or restoration."
                columns={['Incident', 'Interface', 'Status', 'Triggered by', 'Time']}
                rows={recoveryRows.map((row) => ({
                  key: `${row.incidentId}-${row.timestamp}`,
                  cells: [
                    row.incidentId,
                    row.interface,
                    row.recoveryStatus,
                    row.executedBy || '—',
                    formatDateTime(row.timestamp),
                  ],
                }))}
                statusColumn={2}
              />
            </CardContent>
          </Card>
        </div>
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

function deriveStormSummary(
  incidents: StormIncident[],
  mitigations: MitigationLog[],
  recoveries: RecoveryLog[],
) {
  return {
    openIncidents: incidents.filter((row) => !isClosedStormStatus(row.status)).length,
    criticalIncidents: incidents.filter((row) => String(row.severity).toUpperCase() === 'CRITICAL')
      .length,
    totalMitigations: mitigations.length,
    successfulMitigations: mitigations.filter((row) => isSuccessfulStormStatus(row.status)).length,
    totalRecoveries: recoveries.length,
    successfulRecoveries: recoveries.filter((row) => isSuccessfulStormStatus(row.recoveryStatus))
      .length,
  }
}

function isClosedStormStatus(status: string | null | undefined) {
  const normalized = String(status || '').trim().toUpperCase()
  return normalized === 'RECOVERED' || normalized === 'CLOSED' || normalized === 'RESOLVED'
}

function isSuccessfulStormStatus(status: string | null | undefined) {
  const normalized = String(status || '').trim().toUpperCase()
  return (
    normalized === 'SUCCESS' ||
    normalized === 'COMPLETED' ||
    normalized === 'RECOVERED' ||
    normalized === 'ROLLED_BACK'
  )
}

function formatRatio(successes: number, total: number) {
  return total === 0 ? '0 / 0' : `${successes} / ${total}`
}

function StormReportTable({
  isLoading,
  error,
  emptyTitle,
  emptyDescription,
  columns,
  rows,
  statusColumn,
}: {
  isLoading: boolean
  error: unknown
  emptyTitle: string
  emptyDescription: string
  columns: string[]
  rows: Array<{ key: string; cells: Array<string | number | null | undefined> }>
  statusColumn?: number
}) {
  if (isLoading) {
    return <LoadingState label="Loading report…" />
  }

  if (error) {
    return (
      <ErrorState message={error instanceof Error ? error.message : 'Failed to load report data'} />
    )
  }

  if (rows.length === 0) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />
  }

  return (
    <div className="overflow-auto">
      <Table>
        <TableHeader>
          <TableRow>
            {columns.map((column) => (
              <TableHead key={column}>{column}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.key}>
              {row.cells.map((cell, index) => (
                <TableCell key={`${row.key}-${columns[index]}`}>
                  {statusColumn === index ? (
                    <StatusBadge status={String(cell || 'Unknown')} pulse={false} />
                  ) : index === 0 ? (
                    <span className="mono text-xs">{cell || '—'}</span>
                  ) : (
                    cell || '—'
                  )}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
