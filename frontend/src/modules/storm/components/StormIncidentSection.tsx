import { FileJson } from 'lucide-react'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import { useClientPagination } from '@/hooks/useClientPagination'
import { cn } from '@/lib/utils'
import type { StormIncident } from '@/types'
import { formatRelative } from '@/utils/format'
import {
  DEFAULT_SECTION_ROWS_PER_PAGE,
  IncidentStatusBadge,
  IncidentTypeBadge,
  JsonSection,
  SeverityBadge,
  Subsection,
} from './stormShared'

type StormIncidentSectionProps = {
  rows: StormIncident[]
  selectedIncident: StormIncident | null
  expandedSections: Record<string, boolean>
  isAdmin: boolean
  mitigationPending: boolean
  rollbackPending: boolean
  recoveryPending: boolean
  retryPending: boolean
  sectionId?: string
  isLoading?: boolean
  onSelectIncident: (row: StormIncident) => void
  onViewIncident: (row: StormIncident) => void
  onExportIncident: (incident: StormIncident) => void
  onToggleJsonSection: (key: string) => void
  onExecuteMitigation: (incidentId: string, strategy: string) => void
  onRollback: (incidentId: string) => void
  onRetryRecovery: (incidentId: string) => void
  onForceRecovery: (incidentId: string) => void
}

export function StormIncidentSection({
  rows,
  selectedIncident,
  expandedSections,
  isAdmin,
  mitigationPending,
  rollbackPending,
  recoveryPending,
  retryPending,
  sectionId,
  isLoading = false,
  onSelectIncident,
  onViewIncident,
  onExportIncident,
  onToggleJsonSection,
  onExecuteMitigation,
  onRollback,
  onRetryRecovery,
  onForceRecovery,
}: StormIncidentSectionProps) {
  const incidentsPagination = useClientPagination(rows, DEFAULT_SECTION_ROWS_PER_PAGE)

  return (
    <Subsection
      id={sectionId}
      title="Storm Incidents"
      description="Pre-mitigation evidence packages for this switch."
      loading={isLoading}
    >
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No storm incidents for this switch.</p>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(280px,1fr)]">
          <div className="space-y-3">
            <div className="overflow-x-auto rounded-md border border-border/60">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Incident</TableHead>
                    <TableHead>Interface</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Severity</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead>Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {incidentsPagination.pageItems.map((row) => {
                    const active = selectedIncident?.incidentId === row.incidentId
                    return (
                      <TableRow
                        key={row.incidentId}
                        className={cn('cursor-pointer', active && 'bg-primary/10')}
                        onClick={() => onSelectIncident(row)}
                      >
                        <TableCell className="mono text-xs font-medium">
                          {row.incidentId}
                        </TableCell>
                        <TableCell className="font-medium">{row.interface}</TableCell>
                        <TableCell>
                          <IncidentTypeBadge incidentType={row.incidentType || row.type} />
                        </TableCell>
                        <TableCell>
                          <SeverityBadge severity={row.severity} />
                        </TableCell>
                        <TableCell>
                          <IncidentStatusBadge status={row.status} />
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                          {formatRelative(row.createdAt) || '—'}
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-wrap gap-1">
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              onClick={(e) => {
                                e.stopPropagation()
                                onViewIncident(row)
                              }}
                            >
                              View
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="secondary"
                              onClick={(e) => {
                                e.stopPropagation()
                                onExportIncident(row)
                              }}
                            >
                              Export
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </div>
            {incidentsPagination.totalPages > 1 ? (
              <PaginationControls
                page={incidentsPagination.page}
                totalPages={Math.max(incidentsPagination.totalPages, 1)}
                total={incidentsPagination.total}
                limit={incidentsPagination.limit}
                onPageChange={incidentsPagination.setPage}
                onLimitChange={incidentsPagination.setLimit}
                limitOptions={[5, 10, 25, 50]}
                unitLabel="Incident rows"
              />
            ) : null}
          </div>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">
                {selectedIncident ? selectedIncident.incidentId : 'Select an incident'}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {!selectedIncident ? (
                <p className="text-sm text-muted-foreground">
                  Click a row to inspect evidence snapshots.
                </p>
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <SeverityBadge severity={selectedIncident.severity} />
                    <IncidentStatusBadge status={selectedIncident.status} />
                  </div>
                  <p className="text-sm text-muted-foreground">{selectedIncident.interface}</p>
                  <JsonSection
                    title="Interface Snapshot"
                    open={Boolean(expandedSections.interface)}
                    onToggle={() => onToggleJsonSection('interface')}
                    data={selectedIncident.interfaceSnapshot}
                  />
                  <JsonSection
                    title="Switchport Snapshot"
                    open={Boolean(expandedSections.switchport)}
                    onToggle={() => onToggleJsonSection('switchport')}
                    data={selectedIncident.switchportSnapshot}
                  />
                  <JsonSection
                    title="MAC Table"
                    open={Boolean(expandedSections.mac)}
                    onToggle={() => onToggleJsonSection('mac')}
                    data={selectedIncident.macTable}
                  />
                  <JsonSection
                    title="Neighbor"
                    open={Boolean(expandedSections.neighbor)}
                    onToggle={() => onToggleJsonSection('neighbor')}
                    data={selectedIncident.neighbor}
                  />
                  <JsonSection
                    title="Timeline"
                    open={Boolean(expandedSections.timeline)}
                    onToggle={() => onToggleJsonSection('timeline')}
                    data={selectedIncident.timeline}
                  />
                  {isAdmin ? (
                    <div className="space-y-2 border-t border-border/50 pt-3">
                      {[
                        'READY_FOR_MITIGATION',
                        'PREPARED',
                        'OPEN',
                        'MITIGATION_FAILED',
                      ].includes(selectedIncident.status) ? (
                        <Button
                          type="button"
                          className="w-full bg-destructive text-destructive-foreground hover:bg-destructive/90"
                          disabled={mitigationPending}
                          onClick={() =>
                            onExecuteMitigation(selectedIncident.incidentId || '', 'SHUTDOWN')
                          }
                        >
                          {mitigationPending ? 'Executing Shutdown…' : 'Execute Shutdown'}
                        </Button>
                      ) : null}
                      {selectedIncident.status === 'MITIGATED' ? (
                        <div className="flex gap-2">
                          <Button
                            type="button"
                            variant="outline"
                            className="flex-1 border-destructive text-destructive hover:bg-destructive/10"
                            disabled={rollbackPending}
                            onClick={() => onRollback(selectedIncident.incidentId || '')}
                          >
                            {rollbackPending ? 'Rolling back…' : 'Rollback'}
                          </Button>
                          <Button
                            type="button"
                            variant="secondary"
                            className="flex-1"
                            disabled={mitigationPending}
                            onClick={() =>
                              onExecuteMitigation(
                                selectedIncident.incidentId || '',
                                'NO_SHUTDOWN',
                              )
                            }
                          >
                            {mitigationPending ? 'Recovering…' : 'Recover Port'}
                          </Button>
                        </div>
                      ) : null}
                      {['MITIGATED', 'RECOVERY_FAILED', 'MITIGATION_FAILED'].includes(
                        selectedIncident.status,
                      ) ? (
                        <div className="flex gap-2">
                          <Button
                            type="button"
                            variant="secondary"
                            className="flex-1"
                            disabled={retryPending}
                            onClick={() => onRetryRecovery(selectedIncident.incidentId || '')}
                          >
                            {retryPending ? 'Retrying…' : 'Retry Recovery'}
                          </Button>
                          <Button
                            type="button"
                            variant="outline"
                            className="flex-1"
                            disabled={recoveryPending}
                            onClick={() => onForceRecovery(selectedIncident.incidentId || '')}
                          >
                            {recoveryPending ? 'Recovering…' : 'Force Recovery'}
                          </Button>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                  <Button
                    type="button"
                    variant="outline"
                    className="w-full"
                    onClick={() => onExportIncident(selectedIncident)}
                  >
                    <FileJson className="mr-2 h-4 w-4" />
                    Export Incident
                  </Button>
                </>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </Subsection>
  )
}
