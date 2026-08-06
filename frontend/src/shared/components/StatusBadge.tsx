import { statusTone } from '@/lib/status'
import { cn } from '@/lib/utils'
import { Badge } from '@/shared/ui/badge'
import type { VariantProps } from 'class-variance-authority'
import { badgeVariants } from '@/shared/ui/badge'

interface StatusBadgeProps {
  status: string | null | undefined
  className?: string
  pulse?: boolean
}

type BadgeVariant = NonNullable<VariantProps<typeof badgeVariants>['variant']>

function toneToVariant(tone: ReturnType<typeof statusTone>): BadgeVariant {
  if (tone === 'online') return 'online'
  if (tone === 'warn') return 'warning'
  if (tone === 'offline') return 'offline'
  return 'muted'
}

export function StatusBadge({ status, className, pulse = true }: StatusBadgeProps) {
  const label = status || 'Unknown'
  const tone = statusTone(label)
  const variant = toneToVariant(tone)

  return (
    <Badge variant={variant} className={cn('whitespace-nowrap', className)}>
      {pulse && tone === 'online' ? (
        <span className="relative flex h-2 w-2" aria-hidden="true">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-60" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-success" />
        </span>
      ) : (
        <span
          className={cn(
            'h-1.5 w-1.5 rounded-full',
            tone === 'warn' && 'bg-warning',
            tone === 'offline' && 'bg-danger',
            tone === 'unknown' && 'bg-slate-400',
          )}
          aria-hidden="true"
        />
      )}
      {label}
    </Badge>
  )
}
