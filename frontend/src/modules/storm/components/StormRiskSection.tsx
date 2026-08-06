import { Link } from 'react-router-dom'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import { useClientPagination } from '@/hooks/useClientPagination'
import { cn } from '@/lib/utils'
import type { RiskResult } from '@/types'
import { formatRelative } from '@/utils/format'
import {
  DEFAULT_SECTION_ROWS_PER_PAGE,
  formatRate,
  MetricCell,
  SeverityBadge,
  severityTone,
  SourceBadge,
  Subsection,
} from './stormShared'

type StormRiskSectionProps = {
  rows: RiskResult[]
  selectedRisk: RiskResult | null
  onSelectRisk: (row: RiskResult) => void
  trendData: Array<{ time: string; label: string; riskScore: number; severity: string }>
  trendLoading: boolean
  sectionId?: string
  isLoading?: boolean
}

export function StormRiskSection({
  rows,
  selectedRisk,
  onSelectRisk,
  trendData,
  trendLoading,
  sectionId,
  isLoading = false,
}: StormRiskSectionProps) {
  const riskPagination = useClientPagination(rows, DEFAULT_SECTION_ROWS_PER_PAGE)

  return (
    <Subsection
      id={sectionId}
      title="Risk Score"
      description="Rate-based storm probability for eligible access ports on this switch."
      loading={isLoading}
    >
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No risk scores for this switch yet.</p>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(280px,1fr)]">
          <div className="space-y-3">
            <div className="overflow-x-auto rounded-md border border-border/60">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Interface</TableHead>
                    <TableHead>Risk</TableHead>
                    <TableHead>Severity</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead>Broadcast</TableHead>
                    <TableHead>Util</TableHead>
                    <TableHead>Updated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {riskPagination.pageItems.map((row) => {
                    const active =
                      selectedRisk?.deviceId === row.deviceId &&
                      selectedRisk?.interface === row.interface
                    const tone = severityTone(row.severity)
                    return (
                      <TableRow
                        key={`${row.deviceId}-${row.interface}-${row._id ?? ''}`}
                        className={cn('cursor-pointer', active && 'bg-primary/10')}
                        onClick={() => onSelectRisk(row)}
                      >
                        <TableCell className="font-medium">
                          <Link
                            to={`/interfaces/${row.deviceId}/${encodeURIComponent(row.interface)}`}
                            className="text-primary hover:underline"
                            onClick={(e) => e.stopPropagation()}
                          >
                            {row.interface}
                          </Link>
                        </TableCell>
                        <TableCell>
                          <div className="flex min-w-[88px] items-center gap-2">
                            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-secondary">
                              <div
                                className={cn('h-full rounded-full', tone.bar)}
                                style={{
                                  width: `${Math.min(100, Math.max(0, row.riskScore))}%`,
                                }}
                              />
                            </div>
                            <span
                              className={cn(
                                'mono w-10 text-right text-xs font-semibold',
                                tone.text,
                              )}
                            >
                              {row.riskScore.toFixed(0)}
                            </span>
                          </div>
                        </TableCell>
                        <TableCell>
                          <SeverityBadge severity={String(row.severity)} />
                        </TableCell>
                        <TableCell>
                          <SourceBadge
                            classification={row.sourceClassification}
                            confidence={row.sourceConfidence}
                          />
                        </TableCell>
                        <TableCell className="mono text-xs">
                          {formatRate(row.broadcastRate)}
                        </TableCell>
                        <TableCell className="mono text-xs">
                          {row.utilization == null
                            ? '—'
                            : `${Number(row.utilization).toFixed(1)}%`}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                          {formatRelative(row.timestamp) || '—'}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </div>
            {riskPagination.totalPages > 1 ? (
              <PaginationControls
                page={riskPagination.page}
                totalPages={Math.max(riskPagination.totalPages, 1)}
                total={riskPagination.total}
                limit={riskPagination.limit}
                onPageChange={riskPagination.setPage}
                onLimitChange={riskPagination.setLimit}
                limitOptions={[5, 10, 25, 50]}
                unitLabel="Risk rows"
              />
            ) : null}
          </div>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">
                {selectedRisk ? `${selectedRisk.interface} detail` : 'Select an interface'}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {!selectedRisk ? (
                <p className="text-sm text-muted-foreground">
                  Click a row to inspect contributors and risk trend.
                </p>
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={cn(
                        'text-2xl font-bold',
                        severityTone(selectedRisk.severity).text,
                      )}
                    >
                      {selectedRisk.riskScore.toFixed(1)}
                    </span>
                    <SeverityBadge severity={String(selectedRisk.severity)} />
                    <SourceBadge
                      classification={selectedRisk.sourceClassification}
                      confidence={selectedRisk.sourceConfidence}
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    <MetricCell
                      label="Broadcast"
                      value={formatRate(selectedRisk.broadcastRate)}
                    />
                    <MetricCell
                      label="Multicast"
                      value={formatRate(selectedRisk.multicastRate)}
                    />
                    <MetricCell
                      label="Utilization"
                      value={
                        selectedRisk.utilization == null
                          ? '—'
                          : `${Number(selectedRisk.utilization).toFixed(1)}%`
                      }
                    />
                    <MetricCell label="Errors" value={formatRate(selectedRisk.errorRate)} />
                  </div>
                  <div className="h-40">
                    {trendLoading ? (
                      <p className="text-sm text-muted-foreground">Loading history…</p>
                    ) : trendData.length < 2 ? (
                      <p className="text-sm text-muted-foreground">
                        Not enough history points yet.
                      </p>
                    ) : (
                      <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={trendData}>
                          <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                          <XAxis dataKey="label" tick={{ fontSize: 10 }} />
                          <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} />
                          <Tooltip
                            contentStyle={{
                              background: 'hsl(var(--card))',
                              border: '1px solid hsl(var(--border))',
                              borderRadius: 8,
                            }}
                          />
                          <Area
                            type="monotone"
                            dataKey="riskScore"
                            stroke="hsl(var(--primary))"
                            fill="hsl(var(--primary) / 0.2)"
                            strokeWidth={2}
                          />
                        </AreaChart>
                      </ResponsiveContainer>
                    )}
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </Subsection>
  )
}
