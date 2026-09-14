import type { SwitchHardwareCurrent } from '@/api/switchHardwareService'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import {
  formatHardwareValue,
  HardwareHealthBadge,
} from '@/modules/storm/components/hardware/hardwareStatus'
import { cn } from '@/lib/utils'

interface CpuMemorySectionProps {
  cpu: SwitchHardwareCurrent['cpu'] | null | undefined
  memory: SwitchHardwareCurrent['memory'] | null | undefined
}

function UtilizationMeter({
  label,
  percent,
  status,
}: {
  label: string
  percent: number | null | undefined
  status: string | null | undefined
}) {
  const value = percent == null ? null : Math.max(0, Math.min(100, percent))
  return (
    <div className="space-y-3 rounded-lg border border-border/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">{label}</p>
        <HardwareHealthBadge status={status} />
      </div>
      <p className="text-2xl font-semibold mono">
        {value == null ? 'Not available' : `${value.toFixed(1)}%`}
      </p>
      <div className="h-2 overflow-hidden rounded-full bg-muted">
        <div
          className={cn(
            'h-full rounded-full transition-all',
            (status || '').toLowerCase() === 'critical' && 'bg-danger',
            (status || '').toLowerCase() === 'warning' && 'bg-warning',
            (status || '').toLowerCase() === 'healthy' && 'bg-success',
            !['critical', 'warning', 'healthy'].includes((status || '').toLowerCase()) &&
              'bg-muted-foreground/50',
          )}
          style={{ width: value == null ? '0%' : `${value}%` }}
        />
      </div>
      <p className="text-xs text-muted-foreground">
        Status from backend health evaluator · threshold logic is not duplicated in the UI
      </p>
    </div>
  )
}

export function CpuMemorySection({ cpu, memory }: CpuMemorySectionProps) {
  return (
    <Card className="border-border/70">
      <CardHeader>
        <CardTitle className="text-base">CPU &amp; Memory</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-2">
        <UtilizationMeter
          label="CPU Utilization"
          percent={cpu?.utilizationPercent}
          status={cpu?.status}
        />
        <UtilizationMeter
          label="Memory Utilization"
          percent={memory?.utilizationPercent}
          status={memory?.status}
        />
        <div className="md:col-span-2 grid grid-cols-2 gap-3 text-sm text-muted-foreground sm:grid-cols-4">
          <div>
            <p>CPU reading</p>
            <p className="font-medium text-foreground">
              {formatHardwareValue(cpu?.utilizationPercent, '%')}
            </p>
          </div>
          <div>
            <p>CPU status</p>
            <p className="font-medium text-foreground">{formatHardwareValue(cpu?.status)}</p>
          </div>
          <div>
            <p>Memory reading</p>
            <p className="font-medium text-foreground">
              {formatHardwareValue(memory?.utilizationPercent, '%')}
            </p>
          </div>
          <div>
            <p>Memory status</p>
            <p className="font-medium text-foreground">{formatHardwareValue(memory?.status)}</p>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
