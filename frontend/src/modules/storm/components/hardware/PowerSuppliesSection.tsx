import type { SwitchHardwareCurrent } from '@/api/switchHardwareService'
import { EmptyState } from '@/shared/components/EmptyState'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import {
  formatHardwareValue,
  HardwareHealthBadge,
} from '@/modules/storm/components/hardware/hardwareStatus'

interface PowerSuppliesSectionProps {
  powerSupplies: SwitchHardwareCurrent['powerSupplies'] | null | undefined
}

export function PowerSuppliesSection({ powerSupplies }: PowerSuppliesSectionProps) {
  const items = powerSupplies?.items ?? []

  return (
    <Card className="border-border/70">
      <CardHeader className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle className="text-base">Power Supplies</CardTitle>
          <p className="text-sm text-muted-foreground">
            Redundancy: {formatHardwareValue(powerSupplies?.redundancy)}
          </p>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <div className="rounded-lg border border-border/60 px-3 py-2">
            <p className="text-xs text-muted-foreground">Total</p>
            <p className="text-lg font-semibold">{formatHardwareValue(powerSupplies?.count)}</p>
          </div>
          <div className="rounded-lg border border-border/60 px-3 py-2">
            <p className="text-xs text-muted-foreground">Healthy</p>
            <p className="text-lg font-semibold text-success">
              {formatHardwareValue(powerSupplies?.healthyCount)}
            </p>
          </div>
          <div className="rounded-lg border border-border/60 px-3 py-2">
            <p className="text-xs text-muted-foreground">Failed</p>
            <p className="text-lg font-semibold text-danger">
              {formatHardwareValue(powerSupplies?.failedCount)}
            </p>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <EmptyState
            title="No power supply telemetry available"
            description="PSU status was not reported by this switch."
          />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Power Supply</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Input</TableHead>
                  <TableHead>Output</TableHead>
                  <TableHead>Source</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((psu) => (
                  <TableRow key={psu.name}>
                    <TableCell className="font-medium">{psu.name}</TableCell>
                    <TableCell>
                      <HardwareHealthBadge status={psu.status} />
                    </TableCell>
                    <TableCell>{formatHardwareValue(psu.inputStatus)}</TableCell>
                    <TableCell>{formatHardwareValue(psu.outputStatus)}</TableCell>
                    <TableCell className="text-muted-foreground">{psu.source || '—'}</TableCell>
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
