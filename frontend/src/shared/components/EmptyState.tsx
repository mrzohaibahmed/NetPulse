import { Inbox, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/shared/ui/button'

interface EmptyStateProps {
  title: string
  description?: string
  icon?: LucideIcon
  /** Preferred: custom primary action node */
  action?: ReactNode
  /** Legacy: label + handler */
  actionLabel?: string
  onAction?: () => void
  className?: string
}

export function EmptyState({
  title,
  description,
  icon: Icon = Inbox,
  action,
  actionLabel,
  onAction,
  className,
}: EmptyStateProps) {
  const primaryAction =
    action ??
    (actionLabel && onAction ? (
      <Button type="button" variant="secondary" size="sm" onClick={onAction}>
        {actionLabel}
      </Button>
    ) : null)

  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-4 px-4 py-14 text-center np-fade-in',
        className,
      )}
      role="status"
    >
      <div
        className="flex h-16 w-16 items-center justify-center rounded-2xl border border-dashed border-border bg-muted/40"
        aria-hidden="true"
      >
        <Icon className="h-7 w-7 text-muted-foreground" />
      </div>
      <div className="space-y-1.5">
        <h3 className="np-h3 text-foreground">{title}</h3>
        {description ? (
          <p className="mx-auto max-w-sm text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {primaryAction}
    </div>
  )
}
