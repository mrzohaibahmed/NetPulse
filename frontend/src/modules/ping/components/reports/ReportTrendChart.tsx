import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { formatBucket } from './helpers'

interface Series {
  dataKey: string
  name: string
  color: string
  fillOpacity?: number
}

interface ReportTrendChartProps {
  data: Array<Record<string, unknown>>
  series: Series[]
  emptyLabel?: string
  yFormatter?: (value: number) => string
  height?: number
}

export function ReportTrendChart({
  data,
  series,
  emptyLabel = 'Not enough samples in this period.',
  yFormatter,
  height = 220,
}: ReportTrendChartProps) {
  const points = data.map((row) => ({
    ...row,
    label: formatBucket(typeof row.bucket === 'string' ? row.bucket : null),
  }))

  if (points.length < 2) {
    return <p className="py-10 text-center text-sm text-muted-foreground">{emptyLabel}</p>
  }

  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
          <XAxis dataKey="label" tick={{ fontSize: 10 }} minTickGap={16} />
          <YAxis
            tick={{ fontSize: 10 }}
            width={48}
            tickFormatter={yFormatter}
          />
          <Tooltip
            contentStyle={{
              background: 'hsl(var(--card))',
              border: '1px solid hsl(var(--border))',
              borderRadius: 8,
            }}
            formatter={(value, name) => [
              yFormatter && typeof value === 'number' ? yFormatter(value) : String(value ?? '—'),
              String(name),
            ]}
          />
          {series.map((item) => (
            <Area
              key={item.dataKey}
              type="monotone"
              dataKey={item.dataKey}
              name={item.name}
              stroke={item.color}
              fill={item.color}
              fillOpacity={item.fillOpacity ?? 0.18}
              strokeWidth={2}
              connectNulls
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
