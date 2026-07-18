import { useState } from 'react'
import { Download, FileSpreadsheet, Calculator } from 'lucide-react'
import { useExportReports, useUptimeReportQuery } from '@/hooks/queries'
import { DEVICE_TYPES } from '@/constants/devices'
import { formatPercent } from '@/utils/format'
import { EmptyState } from '@/components/shared/EmptyState'
import { ErrorState } from '@/components/shared/ErrorState'
import { LoadingState } from '@/components/shared/LoadingState'
import { PageHeader } from '@/components/shared/PageHeader'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

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

  const runExport = async (key: string, fn: () => Promise<void>) => {
    setBusy(key)
    try {
      await fn()
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Reports" description="Export device lists, status logs, and uptime metrics" />

      <Card className="glass">
        <CardHeader>
          <CardTitle>Filters</CardTitle>
          <CardDescription>Apply date and device filters to uptime calculations and exports.</CardDescription>
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

      <section className="grid gap-4 md:grid-cols-2">
        <Card className="glass hover:border-primary/40">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
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

        <Card className="glass hover:border-primary/40">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
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
                void runExport('history-csv', () =>
                  exportHistory({ ...params, format: 'csv' }),
                )
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
                void runExport('history-xlsx', () =>
                  exportHistory({ ...params, format: 'xlsx' }),
                )
              }
            >
              <Download className="h-4 w-4" />
              {busy === 'history-xlsx' ? 'Exporting…' : 'Excel'}
            </Button>
          </CardContent>
        </Card>
      </section>

      <Card className="glass">
        <CardHeader>
          <CardTitle>Uptime / downtime by device</CardTitle>
        </CardHeader>
        <CardContent>
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
                      {row.downtimePercentage === null ? '—' : formatPercent(row.downtimePercentage)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
