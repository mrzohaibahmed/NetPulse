import { motion } from 'framer-motion'
import { Activity, ArrowDownRight, ArrowUpRight, Minus, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

interface KpiCardProps {
  label: string
  value: string | number
  icon?: LucideIcon
  tone?: 'default' | 'success' | 'warning' | 'danger' | 'accent'
  trend?: number | null
  hint?: string
  className?: string
}

// Light mode gets a deliberately stronger tint (via the `light:` variant,
// scoped to `html.light` — see index.css) than dark mode: on a near-black
// card a 15% tint already reads clearly, but on the new off-white card
// surface the same 15% all but disappears. Bumping it to ~26% for light
// mode only keeps the tone clearly legible as a category cue without
// tipping into a garish, saturated block of color. Dark mode's classes are
// left byte-for-byte identical to before.
const toneStyles = {
  default: 'from-slate-500/10 to-transparent border-l-slate-500 dark:border-l-slate-400',
  success: 'from-success/15 light:from-success/25 to-transparent border-l-success',
  warning: 'from-warning/15 light:from-warning/25 to-transparent border-l-warning',
  danger: 'from-danger/15 light:from-danger/25 to-transparent border-l-danger',
  accent: 'from-primary/15 light:from-primary/25 to-transparent border-l-primary',
}

const iconTone = {
  default: 'bg-slate-500/15 light:bg-slate-500/20 text-slate-600 dark:text-slate-300',
  success: 'bg-success/15 light:bg-success/20 text-success',
  warning: 'bg-warning/15 light:bg-warning/20 text-warning',
  danger: 'bg-danger/15 light:bg-danger/20 text-danger',
  accent: 'bg-primary/15 light:bg-primary/20 text-primary',
}

export function KpiCard({
  label,
  value,
  icon: Icon = Activity,
  tone = 'default',
  trend,
  hint,
  className,
}: KpiCardProps) {
  const TrendIcon =
    trend == null || trend === 0 ? Minus : trend > 0 ? ArrowUpRight : ArrowDownRight

  return (
    <motion.article
      whileHover={{ y: -2, scale: 1.01 }}
      transition={{ type: 'spring', stiffness: 400, damping: 28 }}
      className={cn(
        'glass relative overflow-hidden rounded-xl border-l-[3px] bg-gradient-to-br p-4',
        toneStyles[tone],
        className,
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{label}</p>
          <motion.p
            key={String(value)}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-1 truncate text-2xl font-bold tracking-tight"
          >
            {value}
          </motion.p>
          {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
        </div>
        <div className={cn('flex h-10 w-10 shrink-0 items-center justify-center rounded-lg', iconTone[tone])}>
          <Icon className="h-5 w-5" aria-hidden="true" />
        </div>
      </div>
      {trend != null ? (
        <div
          className={cn(
            'mt-3 inline-flex items-center gap-1 text-xs font-medium',
            trend > 0 ? 'text-success' : trend < 0 ? 'text-danger' : 'text-muted-foreground',
          )}
        >
          <TrendIcon className="h-3.5 w-3.5" />
          {trend === 0 ? 'No change' : `${Math.abs(trend)} vs last`}
        </div>
      ) : null}
    </motion.article>
  )
}
