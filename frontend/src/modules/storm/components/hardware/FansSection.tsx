import type { SwitchHardwareCurrent } from '@/api/switchHardwareService'
import { EmptyState } from '@/shared/components/EmptyState'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import {
  formatHardwareValue,
  HardwareHealthBadge,
} from '@/modules/storm/components/hardware/hardwareStatus'

interface FansSectionProps {
  fans: SwitchHardwareCurrent['fans'] | null | undefined
}

export function FansSection({ fans }: FansSectionProps) {
  const items = fans?.items ?? []

  return (
    <Card className="border-border/70">
      <CardHeader className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle className="text-base">Fans</CardTitle>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <div className="rounded-lg border border-border/60 px-3 py-2">
            <p className="text-xs text-muted-foreground">Total</p>
            <p className="text-lg font-semibold">{formatHardwareValue(fans?.count)}</p>
          </div>
          <div className="rounded-lg border border-border/60 px-3 py-2">
            <p className="text-xs text-muted-foreground">Healthy</p>
            <p className="text-lg font-semibold text-success">
              {formatHardwareValue(fans?.healthyCount)}
            </p>
          </div>
          <div className="rounded-lg border border-border/60 px-3 py-2">
            <p className="text-xs text-muted-foreground">Failed</p>
            <p className="text-lg font-semibold text-danger">
              {formatHardwareValue(fans?.failedCount)}
            </p>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <EmptyState
            title="No fan telemetry available"
            description="Fan status was not reported by this switch."
          />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Fan</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>RPM</TableHead>
                  <TableHead>Source</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((fan) => (
                  <TableRow key={fan.name}>
                    <TableCell className="font-medium">{fan.name}</TableCell>
                    <TableCell>
                      <HardwareHealthBadge status={fan.status} />
                    </TableCell>
                    <TableCell className="mono">
                      {fan.speedRpm == null ? 'Not available' : `${fan.speedRpm} RPM`}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{fan.source || '—'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
