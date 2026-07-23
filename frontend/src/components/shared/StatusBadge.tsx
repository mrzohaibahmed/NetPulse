import { statusTone } from '@/lib/status'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'

interface StatusBadgeProps {
  status: string | null | undefined
  className?: string
  pulse?: boolean
}

export function StatusBadge({ status, className, pulse = true }: StatusBadgeProps) {
  const label = status || 'Unknown'
  const tone = statusTone(label)

  const variant =
    tone === 'online' ? 'success' : tone === 'warn' ? 'warning' : tone === 'offline' ? 'danger' : 'muted'

  return (
    <Badge variant={variant} className={cn('whitespace-nowrap font-semibold', className)}>
      {pulse && tone === 'online' ? (
        <span className="relative flex h-2 w-2">
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
        />
      )}
      {label}
    </Badge>
  )
}
