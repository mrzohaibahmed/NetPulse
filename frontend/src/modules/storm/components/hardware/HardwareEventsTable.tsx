import type { SwitchHardwareEvent } from '@/api/switchHardwareService'
import { EmptyState } from '@/shared/components/EmptyState'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { Badge } from '@/shared/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import {
  eventSeverityVariant,
  isRecoveryEvent,
} from '@/modules/storm/components/hardware/hardwareStatus'
import { formatDateTime } from '@/utils/format'

interface HardwareEventsTableProps {
  events: SwitchHardwareEvent[]
  page: number
  totalPages: number
  total: number
  limit: number
  onPageChange: (page: number) => void
  onLimitChange: (limit: number) => void
  loading?: boolean
}

export function HardwareEventsTable({
  events,
  page,
  totalPages,
  total,
  limit,
  onPageChange,
  onLimitChange,
  loading = false,
}: HardwareEventsTableProps) {
  return (
    <Card className="border-border/70">
      <CardHeader>
        <CardTitle className="text-base">Hardware Events</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {events.length === 0 && !loading ? (
          <EmptyState
            title="No hardware events"
            description="State transitions such as fan failures or temperature warnings will appear here."
          />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Severity</TableHead>
                  <TableHead>Component</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>State</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {events.map((event) => (
                  <TableRow key={event.id}>
                    <TableCell className="whitespace-nowrap text-sm">
                      {formatDateTime(event.timestamp)}
                    </TableCell>
                    <TableCell className="mono text-xs">{event.eventType}</TableCell>
                    <TableCell>
                      <Badge variant={eventSeverityVariant(event.severity)}>
                        {isRecoveryEvent(event.eventType)
                          ? 'Recovery'
                          : event.severity || 'Info'}
                      </Badge>
                    </TableCell>
                    <TableCell>{event.component}</TableCell>
                    <TableCell className="max-w-[24rem] break-words text-sm text-muted-foreground">
                      {event.description}
                    </TableCell>
                    <TableCell>
                      <Badge variant={event.resolved ? 'success' : 'warning'}>
                        {event.resolved ? 'Resolved' : 'Active'}
                      </Badge>
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
            limitOptions={[25, 50, 100]}
          />
        ) : null}
      </CardContent>
    </Card>
  )
}
