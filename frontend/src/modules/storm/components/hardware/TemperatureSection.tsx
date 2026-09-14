import type { SwitchHardwareCurrent } from '@/api/switchHardwareService'
import { EmptyState } from '@/shared/components/EmptyState'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import {
  formatHardwareValue,
  HardwareHealthBadge,
} from '@/modules/storm/components/hardware/hardwareStatus'

interface TemperatureSectionProps {
  temperature: SwitchHardwareCurrent['temperature'] | null | undefined
}

export function TemperatureSection({ temperature }: TemperatureSectionProps) {
  const sensors = temperature?.sensors ?? []

  return (
    <Card className="border-border/70">
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3 space-y-0">
        <CardTitle className="text-base">Temperature</CardTitle>
        <HardwareHealthBadge status={temperature?.status} />
      </CardHeader>
      <CardContent>
        {sensors.length === 0 ? (
          <EmptyState
            title="No temperature sensors available"
            description="This switch did not report temperature telemetry via SNMP or SSH."
          />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Sensor</TableHead>
                  <TableHead>Reading</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Warning</TableHead>
                  <TableHead>Critical</TableHead>
                  <TableHead>Source</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sensors.map((sensor) => (
                  <TableRow key={sensor.name}>
                    <TableCell className="font-medium">{sensor.name}</TableCell>
                    <TableCell className="mono">
                      {sensor.value == null
                        ? 'Not available'
                        : `${sensor.value}${sensor.unit ? ` ${sensor.unit}` : ''}`}
                    </TableCell>
                    <TableCell>
                      <HardwareHealthBadge status={sensor.status} />
                    </TableCell>
                    <TableCell className="mono text-muted-foreground">
                      {formatHardwareValue(sensor.warningThreshold, sensor.unit ? ` ${sensor.unit}` : '')}
                    </TableCell>
                    <TableCell className="mono text-muted-foreground">
                      {formatHardwareValue(sensor.criticalThreshold, sensor.unit ? ` ${sensor.unit}` : '')}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{sensor.source || '—'}</TableCell>
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
