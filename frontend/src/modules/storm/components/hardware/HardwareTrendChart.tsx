import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { SwitchHardwareHistoryItem } from '@/api/switchHardwareService'
import { EmptyState } from '@/shared/components/EmptyState'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { formatDateTime } from '@/utils/format'

const CHART_COLORS = {
  grid: 'var(--color-border)',
  tick: 'var(--color-muted-foreground)',
  temp: 'var(--color-warning)',
  cpu: 'var(--color-primary)',
  memory: 'var(--color-info)',
} as const

interface HardwareTrendChartProps {
  history: SwitchHardwareHistoryItem[]
  loading?: boolean
}

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean
  payload?: Array<{ name?: string; value?: number; color?: string }>
  label?: string
}) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-border bg-popover px-3 py-2 text-xs shadow-xl">
      <p className="mb-1 font-medium text-foreground">{formatDateTime(label)}</p>
      {payload.map((entry) => (
        <p key={entry.name} style={{ color: entry.color }} className="mono">
          {entry.name}: {entry.value == null ? 'N/A' : `${entry.value}`}
        </p>
      ))}
    </div>
  )
}

export function HardwareTrendChart({ history, loading = false }: HardwareTrendChartProps) {
  const points = [...history]
    .reverse()
    .map((item) => {
      const sensors = item.temperature?.sensors ?? []
      const temps = sensors
        .map((sensor) => sensor.value)
        .filter((value): value is number => typeof value === 'number')
      const avgTemp =
        temps.length > 0 ? temps.reduce((sum, value) => sum + value, 0) / temps.length : null
      return {
        timestamp: item.timestamp,
        temperature: avgTemp,
        cpu: item.cpu?.utilizationPercent ?? null,
        memory: item.memory?.utilizationPercent ?? null,
      }
    })
    .filter(
      (point) =>
        point.temperature != null || point.cpu != null || point.memory != null,
    )

  return (
    <Card className="border-border/70">
      <CardHeader>
        <CardTitle className="text-base">Hardware Trends</CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading history…</p>
        ) : points.length === 0 ? (
          <EmptyState
            title="No historical readings"
            description="Temperature, CPU, and memory trends appear after successful collections."
          />
        ) : (
          <div className="h-72 w-full min-w-0">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={points} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid stroke={CHART_COLORS.grid} strokeDasharray="3 3" />
                <XAxis
                  dataKey="timestamp"
                  tick={{ fill: CHART_COLORS.tick, fontSize: 10 }}
                  tickFormatter={(value) => formatDateTime(value).split(',')[0] || value}
                  minTickGap={28}
                />
                <YAxis tick={{ fill: CHART_COLORS.tick, fontSize: 11 }} width={40} />
                <RechartsTooltip content={<ChartTooltip />} />
                <Legend />
                <Area
                  type="monotone"
                  dataKey="temperature"
                  name="Temp °C"
                  stroke={CHART_COLORS.temp}
                  fill={CHART_COLORS.temp}
                  fillOpacity={0.15}
                  connectNulls={false}
                />
                <Area
                  type="monotone"
                  dataKey="cpu"
                  name="CPU %"
                  stroke={CHART_COLORS.cpu}
                  fill={CHART_COLORS.cpu}
                  fillOpacity={0.12}
                  connectNulls={false}
                />
                <Area
                  type="monotone"
                  dataKey="memory"
                  name="Memory %"
                  stroke={CHART_COLORS.memory}
                  fill={CHART_COLORS.memory}
                  fillOpacity={0.1}
                  connectNulls={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
