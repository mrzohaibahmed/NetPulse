import { useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  Download,
  FileBarChart,
  Filter,
  Gauge,
  Radio,
  Server,
  ShieldAlert,
  WifiOff,
} from 'lucide-react'
import {
  useAlertsIncidentsReportQuery,
  useAvailabilityReportQuery,
  useExecutiveReportQuery,
  useExportReports,
  usePerformanceReportQuery,
  useReportFiltersQuery,
  useStormManagementReportQuery,
} from '@/hooks/queries'
import { DEVICE_TYPES } from '@/modules/ping/constants/devices'
import { formatDateTime, formatMs, formatPercent } from '@/utils/format'
import type { PaginationParams, ReportType } from '@/types'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { KpiCard, KpiGrid } from '@/shared/components/KpiCard'
import { LoadingState } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { PaginationControls } from '@/shared/components/PaginationControls'
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
import {
  ALERT_STATUSES,
  ALERT_TYPES,
  DEFAULT_LIMIT,
  DEVICE_STATUSES,
  INCIDENT_STATUSES,
  PERIOD_OPTIONS,
  REPORT_OPTIONS,
  SEVERITIES,
} from '@/modules/ping/components/reports/constants'
import { formatRatio, reportQueryParams } from '@/modules/ping/components/reports/helpers'
import { ReportLimitations } from '@/modules/ping/components/reports/ReportLimitations'
import { ReportTrendChart } from '@/modules/ping/components/reports/ReportTrendChart'
import { StormIncidentDialog } from '@/modules/ping/components/reports/StormIncidentDialog'

const REPORT_DEVICE_TYPES = ['all', ...DEVICE_TYPES]

type DraftFilters = {
  reportType: ReportType
  period: string
  startDate: string
  endDate: string
  deviceId: string
  deviceType: string
  status: string
  iface: string
  severity: string
  alertType: string
  alertStatus: string
  incidentStatus: string
}

const DEFAULT_DRAFT: DraftFilters = {
  reportType: 'executive',
  period: '24h',
  startDate: '',
  endDate: '',
  deviceId: 'all',
  deviceType: 'all',
  status: 'all',
  iface: 'all',
  severity: 'all',
  alertType: 'all',
  alertStatus: 'all',
  incidentStatus: 'all',
}

export function ReportsPage() {
  const [draft, setDraft] = useState<DraftFilters>(DEFAULT_DRAFT)
  const [applied, setApplied] = useState<DraftFilters>(DEFAULT_DRAFT)
  const [page, setPage] = useState(1)
  const [limit, setLimit] = useState(DEFAULT_LIMIT)
  const [busy, setBusy] = useState<string | null>(null)
  const [incidentId, setIncidentId] = useState<string | null>(null)

  const filtersQuery = useReportFiltersQuery(
    applied.deviceId !== 'all' ? applied.deviceId : undefined,
  )
  const { exportManagement } = useExportReports()

  const queryParams = useMemo(
    () => reportQueryParams({ ...applied, page, limit }),
    [applied, page, limit],
  )

  const customReady = applied.period !== 'custom' || Boolean(applied.startDate && applied.endDate)
  const enabled = customReady

  const executive = useExecutiveReportQuery(queryParams, enabled && applied.reportType === 'executive')
  const availability = useAvailabilityReportQuery(
    queryParams,
    enabled && applied.reportType === 'availability',
  )
  const performance = usePerformanceReportQuery(
    queryParams,
    enabled && applied.reportType === 'performance',
  )
  const alerts = useAlertsIncidentsReportQuery(queryParams, enabled && applied.reportType === 'alerts')
  const storm = useStormManagementReportQuery(queryParams, enabled && applied.reportType === 'storm')

  const activeQuery = {
    executive,
    availability,
    performance,
    alerts,
    storm,
  }[applied.reportType]

  const periodLabel = activeQuery.data?.period?.label
  const periodRange =
    activeQuery.data?.period?.start && activeQuery.data?.period?.end
      ? `${formatDateTime(activeQuery.data.period.start)} – ${formatDateTime(activeQuery.data.period.end)}`
      : null

  const applyFilters = () => {
    if (draft.period === 'custom' && (!draft.startDate || !draft.endDate)) return
    setPage(1)
    setApplied(draft)
  }

  const runExport = async (format: 'csv' | 'xlsx') => {
    const key = `${applied.reportType}-${format}`
    setBusy(key)
    try {
      const exportParams: PaginationParams & { format?: string } = {
        ...reportQueryParams({ ...applied, page: 1, limit }),
        format,
      }
      await exportManagement(applied.reportType, exportParams)
    } finally {
      setBusy(null)
    }
  }

  const updateDraft = <K extends keyof DraftFilters>(key: K, value: DraftFilters[K]) => {
    setDraft((prev) => ({ ...prev, [key]: value }))
  }

  const selectedReport = REPORT_OPTIONS.find((item) => item.value === draft.reportType)
  const devices = filtersQuery.data?.devices ?? []
  const interfaces = filtersQuery.data?.interfaces ?? []

  return (
    <div className="np-page min-w-0">
      <PageHeader
        title="Reports"
        description="Management reports from live inventory, ping history, alerts, and storm incidents. Probe success is not SLA or availability."
        actions={
          <>
            <Button
              type="button"
              variant="secondary"
              disabled={Boolean(busy) || !customReady}
              onClick={() => void runExport('csv')}
            >
              <Download className="h-4 w-4" />
              {busy?.endsWith('csv') ? 'Exporting…' : 'Export CSV'}
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={Boolean(busy) || !customReady}
              onClick={() => void runExport('xlsx')}
            >
              <FileBarChart className="h-4 w-4" />
              {busy?.endsWith('xlsx') ? 'Exporting…' : 'Export Excel'}
            </Button>
          </>
        }
      />

      <Card className="glass rounded-xl">
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Filter className="h-4 w-4 text-primary" />
            Report scope
          </CardTitle>
          <CardDescription>
            {selectedReport?.description} Maximum range is 90 days. Default period is last 24 hours.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-3">
          <FilterSelect
            label="Report"
            value={draft.reportType}
            onChange={(value) => {
              const next = value as ReportType
              updateDraft('reportType', next)
              setApplied((prev) => ({ ...prev, reportType: next }))
              setPage(1)
            }}
            width="w-[240px]"
            options={REPORT_OPTIONS.map((item) => ({ value: item.value, label: item.label }))}
          />
          <FilterSelect
            label="Period"
            value={draft.period}
            onChange={(value) => updateDraft('period', value)}
            options={PERIOD_OPTIONS.map((item) => ({ value: item.value, label: item.label }))}
          />
          {draft.period === 'custom' ? (
            <>
              <div className="space-y-1.5">
                <Label>From</Label>
                <Input
                  type="date"
                  value={draft.startDate}
                  onChange={(event) => updateDraft('startDate', event.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label>To</Label>
                <Input
                  type="date"
                  value={draft.endDate}
                  onChange={(event) => updateDraft('endDate', event.target.value)}
                />
              </div>
            </>
          ) : null}
          <FilterSelect
            label="Device"
            value={draft.deviceId}
            onChange={(value) => updateDraft('deviceId', value)}
            width="w-[220px]"
            options={[
              { value: 'all', label: 'All devices' },
              ...devices.map((device) => ({
                value: device.deviceId,
                label: `${device.hostname || 'Unknown'} (${device.ipAddress || '—'})`,
              })),
            ]}
          />
          <FilterSelect
            label="Type"
            value={draft.deviceType}
            onChange={(value) => updateDraft('deviceType', value)}
            options={REPORT_DEVICE_TYPES.map((type) => ({
              value: type,
              label: type === 'all' ? 'All types' : type,
            }))}
          />
          {draft.reportType === 'executive' ||
          draft.reportType === 'availability' ||
          draft.reportType === 'performance' ? (
            <FilterSelect
              label="Current status"
              value={draft.status}
              onChange={(value) => updateDraft('status', value)}
              width="w-[180px]"
              options={DEVICE_STATUSES.map((item) => ({
                value: item,
                label: item === 'all' ? 'All statuses' : item,
              }))}
            />
          ) : null}
          {draft.reportType === 'performance' ? (
            <FilterSelect
              label="Interface"
              value={draft.iface}
              onChange={(value) => updateDraft('iface', value)}
              width="w-[180px]"
              options={[
                { value: 'all', label: 'All interfaces' },
                ...interfaces.map((name) => ({ value: name, label: name })),
              ]}
            />
          ) : null}
          {draft.reportType === 'alerts' || draft.reportType === 'storm' ? (
            <FilterSelect
              label="Severity"
              value={draft.severity}
              onChange={(value) => updateDraft('severity', value)}
              options={SEVERITIES.map((item) => ({
                value: item,
                label: item === 'all' ? 'All severities' : item,
              }))}
            />
          ) : null}
          {draft.reportType === 'alerts' ? (
            <>
              <FilterSelect
                label="Alert type"
                value={draft.alertType}
                onChange={(value) => updateDraft('alertType', value)}
                width="w-[180px]"
                options={ALERT_TYPES.map((item) => ({
                  value: item,
                  label: item === 'all' ? 'All types' : item,
                }))}
              />
              <FilterSelect
                label="Alert status"
                value={draft.alertStatus}
                onChange={(value) => updateDraft('alertStatus', value)}
                options={ALERT_STATUSES.map((item) => ({
                  value: item,
                  label: item === 'all' ? 'All statuses' : item,
                }))}
              />
            </>
          ) : null}
          {draft.reportType === 'storm' ? (
            <FilterSelect
              label="Incident status"
              value={draft.incidentStatus}
              onChange={(value) => updateDraft('incidentStatus', value)}
              width="w-[170px]"
              options={INCIDENT_STATUSES.map((item) => ({
                value: item,
                label: item === 'all' ? 'All statuses' : item,
              }))}
            />
          ) : null}
          <Button type="button" onClick={applyFilters}>
            Generate
          </Button>
        </CardContent>
      </Card>

      {draft.period === 'custom' && (!draft.startDate || !draft.endDate) ? (
        <p className="text-sm text-warning">Custom range requires both start and end dates (max 90 days).</p>
      ) : null}

      {periodLabel ? (
        <p className="np-caption">
          Showing {periodLabel}
          {periodRange ? ` · ${periodRange}` : ''}
        </p>
      ) : null}

      {!enabled ? (
        <EmptyState
          icon={FileBarChart}
          title="Choose a custom range"
          description="Select start and end dates, then click Generate."
        />
      ) : activeQuery.isLoading ? (
        <LoadingState label="Building report…" />
      ) : activeQuery.error ? (
        <ErrorState
          message={activeQuery.error instanceof Error ? activeQuery.error.message : 'Failed to build report'}
          onRetry={() => void activeQuery.refetch()}
        />
      ) : applied.reportType === 'executive' && executive.data ? (
        <ExecutiveView data={executive.data} />
      ) : applied.reportType === 'availability' && availability.data ? (
        <AvailabilityView
          data={availability.data}
          page={page}
          limit={limit}
          onPageChange={setPage}
          onLimitChange={(next) => {
            setLimit(next)
            setPage(1)
          }}
        />
      ) : applied.reportType === 'performance' && performance.data ? (
        <PerformanceView data={performance.data} />
      ) : applied.reportType === 'alerts' && alerts.data ? (
        <AlertsView
          data={alerts.data}
          page={page}
          limit={limit}
          onPageChange={setPage}
          onLimitChange={(next) => {
            setLimit(next)
            setPage(1)
          }}
        />
      ) : applied.reportType === 'storm' && storm.data ? (
        <StormView
          data={storm.data}
          page={page}
          limit={limit}
          onPageChange={setPage}
          onLimitChange={(next) => {
            setLimit(next)
            setPage(1)
          }}
          onOpenIncident={setIncidentId}
        />
      ) : (
        <EmptyState
          icon={FileBarChart}
          title="No report data"
          description="No matching inventory or history for this period and filter set."
        />
      )}

      <StormIncidentDialog incidentId={incidentId} onClose={() => setIncidentId(null)} />
    </div>
  )
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
  width = 'w-[160px]',
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: Array<{ value: string; label: string }>
  width?: string
}) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className={width}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

function ExecutiveView({
  data,
}: {
  data: NonNullable<ReturnType<typeof useExecutiveReportQuery>['data']>
}) {
  const snap = data.snapshot
  const metrics = data.periodMetrics
  const quality = data.dataQuality
  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <SectionHeading title="Current snapshot" description="Live inventory at report generation time." />
        <KpiGrid>
          <KpiCard label="Devices" value={snap.totalDevices} icon={Server} tone="accent" />
          <KpiCard label="Online now" value={snap.onlineDevices} icon={Activity} tone="success" />
          <KpiCard
            label="Unreachable now"
            value={snap.unreachableDevices}
            icon={WifiOff}
            tone={snap.unreachableDevices ? 'danger' : 'default'}
          />
          <KpiCard
            label="Monitoring coverage"
            value={formatPercent(snap.monitoringCoveragePercent)}
            icon={Gauge}
            hint="Devices with monitoring enabled / total devices"
          />
          <KpiCard
            label="Open critical alerts"
            value={snap.openCriticalAlerts}
            icon={AlertTriangle}
            tone={snap.openCriticalAlerts ? 'warning' : 'default'}
          />
          <KpiCard
            label="Open storm incidents"
            value={snap.openStormIncidents}
            icon={ShieldAlert}
            tone={snap.openStormIncidents ? 'danger' : 'default'}
          />
          <KpiCard
            label="High/critical risk interfaces"
            value={snap.highCriticalRiskInterfaces}
            icon={Radio}
            hint="Current storm risk snapshot, not historical"
          />
          <KpiCard
            label="Monitored devices"
            value={snap.monitoredDevices}
            icon={Server}
            hint={`${snap.unknownDevices} unknown status`}
          />
        </KpiGrid>
      </section>

      <section className="space-y-3">
        <SectionHeading
          title="Period metrics"
          description="Probe success ratio is online checks ÷ total checks. It is not time-based availability or SLA."
        />
        <KpiGrid>
          <KpiCard
            label="Probe success ratio"
            value={formatRatio(metrics.probeSuccessRatio)}
            icon={Gauge}
            tone="accent"
            hint={`${metrics.onlineChecks} online / ${metrics.totalChecks} checks`}
          />
          <KpiCard label="Failed checks" value={metrics.failedChecks} icon={WifiOff} />
          <KpiCard
            label="Critical alerts created"
            value={metrics.criticalAlertsCreated}
            icon={AlertTriangle}
            tone={metrics.criticalAlertsCreated ? 'warning' : 'default'}
          />
          <KpiCard
            label="Storm incidents created"
            value={metrics.stormIncidentsCreated}
            icon={ShieldAlert}
          />
          <KpiCard
            label="Successful ICMP scan RTT (avg)"
            value={formatMs(metrics.successfulIcmpScanRtt.averageRttMs)}
            icon={Activity}
            hint={`P95 ${formatMs(metrics.successfulIcmpScanRtt.p95RttMs)} · P99 ${formatMs(metrics.successfulIcmpScanRtt.p99RttMs)}`}
          />
        </KpiGrid>
      </section>

      <section className="space-y-3">
        <SectionHeading title="Data quality" description="Gaps that limit how far these numbers can be trusted." />
        <KpiGrid>
          <KpiCard label="Stale monitoring" value={quality.staleMonitoringDevices} hint={`Last check older than ${quality.staleThresholdSeconds}s`} />
          <KpiCard label="Missing hostname" value={quality.missingHostnameDevices} />
          <KpiCard
            label="Interfaces missing speed"
            value={quality.interfacesMissingSpeed}
            hint={`${quality.totalInterfaces} interfaces in inventory`}
          />
        </KpiGrid>
      </section>

      <Card className="glass rounded-xl">
        <CardHeader>
          <CardTitle>Highest current risk interfaces</CardTitle>
          <CardDescription>From the latest storm risk snapshot. Not a historical ranking.</CardDescription>
        </CardHeader>
        <CardContent>
          {data.highRisk.length === 0 ? (
            <EmptyState title="No high-risk interfaces" description="No HIGH or CRITICAL risk scores in the current snapshot." />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Device</TableHead>
                  <TableHead>Interface</TableHead>
                  <TableHead>Risk</TableHead>
                  <TableHead>Severity</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.highRisk.map((row) => (
                  <TableRow key={`${row.deviceId}-${row.interface}`}>
                    <TableCell>{row.hostname}</TableCell>
                    <TableCell>{row.interface || '—'}</TableCell>
                    <TableCell>{row.riskScore ?? '—'}</TableCell>
                    <TableCell>
                      <StatusBadge status={row.severity} pulse={false} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <ReportLimitations items={data.limitations} />
    </div>
  )
}

function AvailabilityView({
  data,
  page,
  limit,
  onPageChange,
  onLimitChange,
}: {
  data: NonNullable<ReturnType<typeof useAvailabilityReportQuery>['data']>
  page: number
  limit: number
  onPageChange: (page: number) => void
  onLimitChange: (limit: number) => void
}) {
  return (
    <div className="space-y-6">
      <KpiGrid>
        <KpiCard label="Online now" value={data.currentStatus.onlineDevices} icon={Activity} tone="success" />
        <KpiCard
          label="Unreachable now"
          value={data.currentStatus.unreachableDevices}
          icon={WifiOff}
          tone={data.currentStatus.unreachableDevices ? 'danger' : 'default'}
        />
        <KpiCard
          label="Probe success ratio"
          value={formatRatio(data.probeSuccess.probeSuccessRatio)}
          icon={Gauge}
          hint={`${data.probeSuccess.onlineChecks} online / ${data.probeSuccess.totalChecks} checks`}
        />
        <KpiCard
          label="Confirmed outage events"
          value={data.confirmedOutageEventCount}
          icon={AlertTriangle}
          hint="Critical-device offline alerts in this period"
        />
      </KpiGrid>

      <Card className="glass rounded-xl">
        <CardHeader>
          <CardTitle>Devices</CardTitle>
          <CardDescription>
            Probe success ratio is not availability. Time since last successful ping is not a confirmed outage duration.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {data.devices.length === 0 ? (
            <EmptyState title="No devices" description="No inventory matches this filter set." />
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Device</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Probe success</TableHead>
                    <TableHead>Checks</TableHead>
                    <TableHead>Outage events</TableHead>
                    <TableHead>First failed probe</TableHead>
                    <TableHead>Time since last ping</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.devices.map((row) => (
                    <TableRow key={row.deviceId}>
                      <TableCell>
                        <div className="font-medium">{row.hostname}</div>
                        <div className="np-caption">{row.ipAddress}</div>
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={row.status} />
                      </TableCell>
                      <TableCell>{formatRatio(row.probeSuccessRatio)}</TableCell>
                      <TableCell>
                        {row.onlineChecks}/{row.totalChecks}
                      </TableCell>
                      <TableCell>{row.confirmedOutageEvents}</TableCell>
                      <TableCell>{formatDateTime(row.firstFailedProbeAt)}</TableCell>
                      <TableCell>{row.timeSinceLastSuccessfulPingLabel || '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <PaginationControls
                page={page}
                totalPages={data.pagination.totalPages}
                total={data.pagination.total}
                limit={limit}
                onPageChange={onPageChange}
                onLimitChange={onLimitChange}
              />
            </>
          )}
        </CardContent>
      </Card>
      <ReportLimitations items={data.limitations} />
    </div>
  )
}

function PerformanceView({
  data,
}: {
  data: NonNullable<ReturnType<typeof usePerformanceReportQuery>['data']>
}) {
  const ping = data.ping
  return (
    <div className="space-y-6">
      <KpiGrid>
        <KpiCard label="Successful scans" value={ping.successfulScans} icon={Activity} tone="success" />
        <KpiCard label="Failed scans" value={ping.failedScans} icon={WifiOff} />
        <KpiCard label="Avg RTT" value={formatMs(ping.averageRttMs)} icon={Gauge} hint="Successful ICMP scan RTT only" />
        <KpiCard label="P95 RTT" value={formatMs(ping.p95RttMs)} icon={Gauge} />
        <KpiCard label="P99 RTT" value={formatMs(ping.p99RttMs)} icon={Gauge} />
        <KpiCard label="Min / max RTT" value={`${formatMs(ping.minRttMs)} / ${formatMs(ping.maxRttMs)}`} icon={Activity} />
        <KpiCard
          label="Valid util samples"
          value={data.interfaces.validSamples}
          icon={Radio}
          hint="Interfaces with known speed only"
        />
      </KpiGrid>

      <Card className="glass rounded-xl">
        <CardHeader>
          <CardTitle>Successful ICMP scan RTT trend</CardTitle>
          <CardDescription>Average RTT of scans that returned a response time. Packet loss is not available.</CardDescription>
        </CardHeader>
        <CardContent>
          <ReportTrendChart
            data={ping.trend as unknown as Array<Record<string, unknown>>}
            series={[
              { dataKey: 'averageRttMs', name: 'Avg RTT (ms)', color: 'var(--color-primary)' },
            ]}
            yFormatter={(value) => `${value.toFixed(0)}`}
            emptyLabel="Not enough successful ICMP scans in this period."
          />
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="glass rounded-xl">
          <CardHeader>
            <CardTitle>Highest average RTT devices</CardTitle>
          </CardHeader>
          <CardContent>
            {ping.topDevices.length === 0 ? (
              <EmptyState title="No RTT samples" description="No successful ICMP scans in this period." />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Device</TableHead>
                    <TableHead>Avg</TableHead>
                    <TableHead>Max</TableHead>
                    <TableHead>Scans</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {ping.topDevices.map((row) => (
                    <TableRow key={row.deviceId ?? row.hostname}>
                      <TableCell>
                        <div className="font-medium">{row.hostname}</div>
                        <div className="np-caption">{row.ipAddress || '—'}</div>
                      </TableCell>
                      <TableCell>{formatMs(row.averageRttMs)}</TableCell>
                      <TableCell>{formatMs(row.maxRttMs)}</TableCell>
                      <TableCell>{row.successfulScans}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card className="glass rounded-xl">
          <CardHeader>
            <CardTitle>Highest valid interface utilization</CardTitle>
            <CardDescription>Samples without a known interface speed are excluded.</CardDescription>
          </CardHeader>
          <CardContent>
            {data.interfaces.topUtilization.length === 0 ? (
              <EmptyState
                title="No valid utilization"
                description="No interface samples with a known speed in this period."
              />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Interface</TableHead>
                    <TableHead>Avg</TableHead>
                    <TableHead>Peak</TableHead>
                    <TableHead>Samples</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.interfaces.topUtilization.map((row) => (
                    <TableRow key={`${row.deviceId}-${row.interface}`}>
                      <TableCell>
                        <div className="font-medium">{row.hostname}</div>
                        <div className="np-caption">{row.interface || '—'}</div>
                      </TableCell>
                      <TableCell>{formatPercent(row.averageUtilizationPercent)}</TableCell>
                      <TableCell>{formatPercent(row.peakUtilizationPercent)}</TableCell>
                      <TableCell>{row.validSamples}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
      <ReportLimitations items={data.limitations} />
    </div>
  )
}

function AlertsView({
  data,
  page,
  limit,
  onPageChange,
  onLimitChange,
}: {
  data: NonNullable<ReturnType<typeof useAlertsIncidentsReportQuery>['data']>
  page: number
  limit: number
  onPageChange: (page: number) => void
  onLimitChange: (limit: number) => void
}) {
  const alerts = data.alerts
  return (
    <div className="space-y-6">
      <KpiGrid>
        <KpiCard label="Alerts in period" value={alerts.total} icon={AlertTriangle} tone="accent" />
        <KpiCard label="Critical" value={alerts.critical} icon={AlertTriangle} tone={alerts.critical ? 'danger' : 'default'} />
        <KpiCard label="Open" value={alerts.open} icon={Activity} />
        <KpiCard label="Resolved" value={alerts.resolved} icon={Gauge} />
        <KpiCard label="Storm incidents in period" value={data.stormIncidents.total} icon={ShieldAlert} />
        <KpiCard label="Open storm incidents" value={data.stormIncidents.open} icon={ShieldAlert} />
      </KpiGrid>

      <Card className="glass rounded-xl">
        <CardHeader>
          <CardTitle>Alert volume</CardTitle>
          <CardDescription>Device alerts only. Storm incidents are counted separately and are not merged into MTTR.</CardDescription>
        </CardHeader>
        <CardContent>
          <ReportTrendChart
            data={alerts.trend as unknown as Array<Record<string, unknown>>}
            series={[
              { dataKey: 'count', name: 'Alerts', color: 'var(--color-primary)' },
              { dataKey: 'critical', name: 'Critical', color: 'var(--color-danger)' },
            ]}
            emptyLabel="No alerts in this period."
          />
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="glass rounded-xl">
          <CardHeader>
            <CardTitle>By severity</CardTitle>
          </CardHeader>
          <CardContent>
            <SimpleCountTable
              rows={alerts.bySeverity.map((row) => ({ name: row.severity, count: row.count }))}
              empty="No alerts."
            />
          </CardContent>
        </Card>
        <Card className="glass rounded-xl">
          <CardHeader>
            <CardTitle>Top devices</CardTitle>
          </CardHeader>
          <CardContent>
            <SimpleCountTable
              rows={alerts.topDevices.map((row) => ({
                name: `${row.hostname}${row.ipAddress ? ` (${row.ipAddress})` : ''}`,
                count: row.count,
              }))}
              empty="No alerting devices."
            />
          </CardContent>
        </Card>
      </div>

      <Card className="glass rounded-xl">
        <CardHeader>
          <CardTitle>Alert log</CardTitle>
        </CardHeader>
        <CardContent>
          {alerts.rows.length === 0 ? (
            <EmptyState title="No alerts" description="No device or storm alerts in this period." />
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Time</TableHead>
                    <TableHead>Device</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Family</TableHead>
                    <TableHead>Severity</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {alerts.rows.map((row) => (
                    <TableRow key={row.id}>
                      <TableCell>{formatDateTime(row.createdAt)}</TableCell>
                      <TableCell>
                        <div className="font-medium">{row.hostname}</div>
                        <div className="np-caption">{row.ipAddress || '—'}</div>
                      </TableCell>
                      <TableCell>{row.alertType}</TableCell>
                      <TableCell>{row.family}</TableCell>
                      <TableCell>
                        <StatusBadge status={row.severity} pulse={false} />
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={row.status} pulse={false} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <PaginationControls
                page={page}
                totalPages={alerts.pagination.totalPages}
                total={alerts.pagination.total}
                limit={limit}
                onPageChange={onPageChange}
                onLimitChange={onLimitChange}
              />
            </>
          )}
        </CardContent>
      </Card>
      <ReportLimitations items={[...data.limitations, ...Object.values(data.unavailable)]} />
    </div>
  )
}

function StormView({
  data,
  page,
  limit,
  onPageChange,
  onLimitChange,
  onOpenIncident,
}: {
  data: NonNullable<ReturnType<typeof useStormManagementReportQuery>['data']>
  page: number
  limit: number
  onPageChange: (page: number) => void
  onLimitChange: (limit: number) => void
  onOpenIncident: (id: string) => void
}) {
  const summary = data.summary
  return (
    <div className="space-y-6">
      <KpiGrid>
        <KpiCard label="Incidents in period" value={summary.totalIncidents} icon={ShieldAlert} tone="accent" />
        <KpiCard label="Open" value={summary.openIncidents} icon={Activity} />
        <KpiCard label="Resolved" value={summary.resolvedIncidents} icon={Gauge} />
        <KpiCard label="Escalated" value={summary.escalatedIncidents} icon={ShieldAlert} />
        <KpiCard label="Critical" value={summary.criticalIncidents} icon={AlertTriangle} tone={summary.criticalIncidents ? 'danger' : 'default'} />
        <KpiCard label="Average risk" value={summary.averageRiskScore ?? '—'} hint={`${summary.riskSamples} risk samples`} />
        <KpiCard label="Maximum risk" value={summary.maximumRiskScore ?? '—'} />
      </KpiGrid>

      <Card className="glass rounded-xl">
        <CardHeader>
          <CardTitle>Incident volume</CardTitle>
          <CardDescription>Server-side totals for the selected period. Not limited to the first 10 rows.</CardDescription>
        </CardHeader>
        <CardContent>
          <ReportTrendChart
            data={data.trend as unknown as Array<Record<string, unknown>>}
            series={[
              { dataKey: 'count', name: 'Incidents', color: 'var(--color-primary)' },
              { dataKey: 'critical', name: 'Critical', color: 'var(--color-danger)' },
            ]}
            emptyLabel="No storm incidents in this period."
          />
        </CardContent>
      </Card>

      <Card className="glass rounded-xl">
        <CardHeader>
          <CardTitle>Incidents</CardTitle>
          <CardDescription>Open a row for timeline, evidence, mitigation, and recovery.</CardDescription>
        </CardHeader>
        <CardContent>
          {data.incidents.length === 0 ? (
            <EmptyState title="No incidents" description="No storm incidents match this period and filter set." />
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Incident</TableHead>
                    <TableHead>Device</TableHead>
                    <TableHead>Interface</TableHead>
                    <TableHead>Risk</TableHead>
                    <TableHead>Started</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Mitigation</TableHead>
                    <TableHead>Recovery</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.incidents.map((row) => (
                    <TableRow
                      key={row.incidentId}
                      className="cursor-pointer"
                      onClick={() => onOpenIncident(row.incidentId)}
                    >
                      <TableCell className="font-medium">{row.incidentId}</TableCell>
                      <TableCell>{row.hostname}</TableCell>
                      <TableCell>{row.interface || '—'}</TableCell>
                      <TableCell>
                        {row.riskScore ?? '—'} {row.severity ? `· ${row.severity}` : ''}
                      </TableCell>
                      <TableCell>{formatDateTime(row.startTime)}</TableCell>
                      <TableCell>
                        <StatusBadge status={row.status} pulse={false} />
                      </TableCell>
                      <TableCell>{row.mitigationStatus || '—'}</TableCell>
                      <TableCell>{row.recoveryStatus || '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <PaginationControls
                page={page}
                totalPages={data.pagination.totalPages}
                total={data.pagination.total}
                limit={limit}
                onPageChange={onPageChange}
                onLimitChange={onLimitChange}
              />
            </>
          )}
        </CardContent>
      </Card>
      <ReportLimitations items={data.limitations} />
    </div>
  )
}

function SimpleCountTable({
  rows,
  empty,
}: {
  rows: Array<{ name: string; count: number }>
  empty: string
}) {
  if (!rows.length) return <p className="text-sm text-muted-foreground">{empty}</p>
  return (
    <div className="w-full max-w-full overflow-x-auto">
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead className="text-right">Count</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.name}>
            <TableCell>{row.name}</TableCell>
            <TableCell className="text-right">{row.count}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
    </div>
  )
}
