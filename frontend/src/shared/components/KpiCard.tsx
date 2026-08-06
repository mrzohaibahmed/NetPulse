import type { ReactNode } from 'react'
import { motion } from 'framer-motion'
import { Activity, ArrowDownRight, ArrowUpRight, Minus, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Skeleton } from '@/shared/ui/skeleton'

interface KpiCardProps {
  label: string
  value: string | number
  icon?: LucideIcon
  tone?: 'default' | 'success' | 'warning' | 'danger' | 'accent'
  trend?: number | null
  hint?: string
  loading?: boolean
  className?: string
}

const toneStyles = {
  default: 'from-slate-500/10 to-transparent border-l-slate-400',
  success: 'from-success/15 to-transparent border-l-success',
  warning: 'from-warning/15 to-transparent border-l-warning',
  danger: 'from-danger/15 to-transparent border-l-danger',
  accent: 'from-primary/15 to-transparent border-l-primary',
}

const iconTone = {
  default: 'bg-slate-500/15 text-slate-300',
  success: 'bg-success/15 text-success',
  warning: 'bg-warning/15 text-warning',
  danger: 'bg-danger/15 text-danger',
  accent: 'bg-primary/15 text-primary',
}

export function KpiCard({
  label,
  value,
  icon: Icon = Activity,
  tone = 'default',
  trend,
  hint,
  loading = false,
  className,
}: KpiCardProps) {
  const TrendIcon =
    trend == null || trend === 0 ? Minus : trend > 0 ? ArrowUpRight : ArrowDownRight

  if (loading) {
    return (
      <div
        className={cn(
          'glass relative flex min-h-[7.5rem] flex-col justify-between overflow-hidden rounded-xl border-l-[3px] border-l-border bg-gradient-to-br from-muted/20 to-transparent p-4',
          className,
        )}
        aria-busy="true"
        aria-label={`Loading ${label}`}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-8 w-24" />
            <Skeleton className="h-3 w-16" />
          </div>
          <Skeleton className="h-10 w-10 rounded-lg" />
        </div>
      </div>
    )
  }

  return (
    <motion.article
      whileHover={{ y: -2 }}
      transition={{ type: 'spring', stiffness: 400, damping: 28 }}
      className={cn(
        'glass relative flex min-h-[7.5rem] flex-col justify-between overflow-hidden rounded-xl border-l-[3px] bg-gradient-to-br p-4',
        toneStyles[tone],
        className,
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="np-label">{label}</p>
          <motion.p
            key={String(value)}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2 }}
            className="np-metric mt-1.5 truncate"
          >
            {value}
          </motion.p>
          {hint ? <p className="np-caption mt-1.5 line-clamp-2">{hint}</p> : null}
        </div>
        <div
          className={cn('np-icon-box', iconTone[tone])}
          aria-hidden="true"
        >
          <Icon className="h-5 w-5" />
        </div>
      </div>
      {trend != null ? (
        <div
          className={cn(
            'mt-3 inline-flex items-center gap-1 text-xs font-medium',
            trend > 0 ? 'text-success' : trend < 0 ? 'text-danger' : 'text-muted-foreground',
          )}
        >
          <TrendIcon className="h-3.5 w-3.5" aria-hidden="true" />
          {trend === 0 ? 'No change' : `${Math.abs(trend)} vs last`}
        </div>
      ) : (
        <div className="mt-3 h-4" aria-hidden="true" />
      )}
    </motion.article>
  )
}

export function KpiGrid({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'grid gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-4',
        className,
      )}
    >
      {children}
    </div>
  )
}
