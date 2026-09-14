import { useState } from 'react'
import type { SwitchOutageIncident } from '@/api/switchHardwareService'
import { EmptyState } from '@/shared/components/EmptyState'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import {
  confidenceBadgeVariant,
  formatConfidence,
  formatDurationSeconds,
  formatRootCause,
} from '@/modules/storm/components/hardware/hardwareStatus'
import { formatDateTime } from '@/utils/format'

interface OutageHistoryTableProps {
  outages: SwitchOutageIncident[]
  page: number
  totalPages: number
  total: number
  limit: number
  onPageChange: (page: number) => void
  onLimitChange: (limit: number) => void
  onSelectOutage?: (outageId: string) => void
  selectedOutage?: SwitchOutageIncident | null
  detailLoading?: boolean
}

export function OutageHistoryTable({
  outages,
  page,
  totalPages,
  total,
  limit,
  onPageChange,
  onLimitChange,
  onSelectOutage,
  selectedOutage,
  detailLoading = false,
}: OutageHistoryTableProps) {
  const [open, setOpen] = useState(false)

  const openDetail = (outage: SwitchOutageIncident) => {
    onSelectOutage?.(outage.id)
    setOpen(true)
  }

  const evidenceItems =
    (selectedOutage?.evidence?.items as string[] | undefined) ||
    (Array.isArray(selectedOutage?.evidence)
      ? (selectedOutage?.evidence as unknown as string[])
      : [])

  return (
    <Card className="border-border/70">
      <CardHeader>
        <CardTitle className="text-base">Outage History</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {outages.length === 0 ? (
          <EmptyState
            title="No outage history"
            description="Outages are opened from ping Offline transitions for eligible Cisco switches."
          />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Started</TableHead>
                  <TableHead>Ended</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Root cause</TableHead>
                  <TableHead>Confidence</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {outages.map((outage) => (
                  <TableRow key={outage.id}>
                    <TableCell className="whitespace-nowrap text-sm">
                      {formatDateTime(outage.startedAt)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm">
                      {outage.endedAt ? formatDateTime(outage.endedAt) : 'Active'}
                    </TableCell>
                    <TableCell className="mono text-sm">
                      {formatDurationSeconds(outage.durationSeconds)}
                    </TableCell>
                    <TableCell>
                      <Badge variant={outage.status === 'active' ? 'danger' : 'secondary'}>
                        {outage.status}
                      </Badge>
                    </TableCell>
                    <TableCell>{formatRootCause(outage.rootCause)}</TableCell>
                    <TableCell>
                      <Badge variant={confidenceBadgeVariant(outage.confidence)}>
                        {formatConfidence(outage.confidence)}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Button variant="ghost" size="sm" onClick={() => openDetail(outage)}>
                        Details
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}

        {total > 0 ? (
          <PaginationControls
            page={page}
            totalPages={totalPages}
            total={total}
            limit={limit}
            onPageChange={onPageChange}
            onLimitChange={onLimitChange}
            limitOptions={[10, 25, 50]}
          />
        ) : null}

        <Dialog open={open} onOpenChange={setOpen}>
          <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
            <DialogHeader>
              <DialogTitle>Outage detail</DialogTitle>
              <DialogDescription>
                Evidence-based root cause analysis. Suspected is not the same as confirmed.
              </DialogDescription>
            </DialogHeader>
            {detailLoading || !selectedOutage ? (
              <p className="text-sm text-muted-foreground">Loading outage detail…</p>
            ) : (
              <div className="space-y-4 text-sm">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <p className="text-muted-foreground">Root cause</p>
                    <p className="font-semibold">{formatRootCause(selectedOutage.rootCause)}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Confidence</p>
                    <Badge variant={confidenceBadgeVariant(selectedOutage.confidence)}>
                      {formatConfidence(selectedOutage.confidence)}
                      {selectedOutage.confirmed ? ' · Confirmed flag' : ''}
                    </Badge>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Started</p>
                    <p>{formatDateTime(selectedOutage.startedAt)}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Ended</p>
                    <p>
                      {selectedOutage.endedAt
                        ? formatDateTime(selectedOutage.endedAt)
                        : 'Still active'}
                    </p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Duration</p>
                    <p>{formatDurationSeconds(selectedOutage.durationSeconds)}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Status</p>
                    <p>{selectedOutage.status}</p>
                  </div>
                </div>

                <div>
                  <p className="mb-2 font-medium">Explanation</p>
                  <p className="text-muted-foreground">
                    {(selectedOutage.evidence?.explanation as string) ||
                      'No explanation provided.'}
                  </p>
                </div>

                <div>
                  <p className="mb-2 font-medium">Evidence</p>
                  {evidenceItems.length === 0 ? (
                    <p className="text-muted-foreground">
                      No verified power, thermal, or reboot evidence was available.
                    </p>
                  ) : (
                    <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
                      {evidenceItems.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  )}
                </div>

                <div>
                  <p className="mb-2 font-medium">Timeline</p>
                  {(selectedOutage.timeline || []).length === 0 ? (
                    <p className="text-muted-foreground">No timeline entries.</p>
                  ) : (
                    <ul className="space-y-2">
                      {selectedOutage.timeline.map((entry, index) => (
                        <li
                          key={`${String(entry.at)}-${index}`}
                          className="rounded-md border border-border/60 px-3 py-2"
                        >
                          <p className="font-medium">{String(entry.event || 'event')}</p>
                          <p className="text-xs text-muted-foreground">
                            {entry.at ? formatDateTime(String(entry.at)) : '—'}
                            {entry.source ? ` · ${String(entry.source)}` : ''}
                          </p>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            )}
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  )
}
