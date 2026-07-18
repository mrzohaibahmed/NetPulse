import { motion } from 'framer-motion'
import { computeNetworkHealth, healthColor, type HealthLabel } from '@/lib/health'
import type { DashboardSummary } from '@/types'
import { cn } from '@/lib/utils'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface HealthGaugeProps {
  summary: DashboardSummary | null | undefined
  className?: string
}

export function HealthGauge({ summary, className }: HealthGaugeProps) {
  const { score, label } = computeNetworkHealth(summary)
  const color = healthColor(label)
  const radius = 70
  const circumference = 2 * Math.PI * radius
  const offset = circumference - (score / 100) * circumference

  return (
    <Card className={cn('glass overflow-hidden', className)}>
      <CardHeader>
        <CardTitle>Network Health</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col items-center gap-4 pb-6">
        <div className="relative flex h-44 w-44 items-center justify-center">
          <svg className="-rotate-90" width="176" height="176" viewBox="0 0 176 176" aria-hidden="true">
            <circle cx="88" cy="88" r={radius} fill="none" stroke="currentColor" strokeWidth="12" className="text-muted/60" />
            <motion.circle
              cx="88"
              cy="88"
              r={radius}
              fill="none"
              stroke={color}
              strokeWidth="12"
              strokeLinecap="round"
              strokeDasharray={circumference}
              initial={{ strokeDashoffset: circumference }}
              animate={{ strokeDashoffset: offset }}
              transition={{ duration: 1, ease: 'easeOut' }}
            />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <motion.span
              key={score}
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              className="text-4xl font-bold tracking-tight"
            >
              {score}%
            </motion.span>
            <span className="text-xs text-muted-foreground">Overall health</span>
          </div>
        </div>
        <HealthBadge label={label} />
      </CardContent>
    </Card>
  )
}

function HealthBadge({ label }: { label: HealthLabel }) {
  const styles: Record<HealthLabel, string> = {
    Excellent: 'bg-success/15 text-success border-success/30',
    Good: 'bg-primary/15 text-primary border-primary/30',
    Warning: 'bg-warning/15 text-warning border-warning/30',
    Critical: 'bg-danger/15 text-danger border-danger/30',
  }
  return (
    <span className={cn('rounded-full border px-3 py-1 text-sm font-semibold', styles[label])}>{label}</span>
  )
}
