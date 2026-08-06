import { useState } from 'react'
import { AlertTriangle, ChevronDown, RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/shared/ui/button'

interface ErrorStateProps {
  title?: string
  message: string
  /** Optional technical details (collapsible) */
  details?: string
  onRetry?: () => void
  className?: string
}

export function ErrorState({
  title = 'Something went wrong',
  message,
  details,
  onRetry,
  className,
}: ErrorStateProps) {
  const [open, setOpen] = useState(false)

  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-4 rounded-xl border border-danger/30 bg-danger/10 px-4 py-10 text-center np-fade-in',
        className,
      )}
      role="alert"
    >
      <div
        className="flex h-12 w-12 items-center justify-center rounded-xl bg-danger/15"
        aria-hidden="true"
      >
        <AlertTriangle className="h-6 w-6 text-danger" />
      </div>
      <div className="space-y-1.5">
        <h3 className="np-h3 text-foreground">{title}</h3>
        <p className="mx-auto max-w-md text-sm text-muted-foreground">{message}</p>
      </div>
      {onRetry ? (
        <Button type="button" variant="secondary" size="sm" onClick={onRetry}>
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
          Retry
        </Button>
      ) : null}
      {details ? (
        <div className="w-full max-w-lg text-left">
          <button
            type="button"
            className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
          >
            <ChevronDown
              className={cn('h-3.5 w-3.5 transition-transform', open && 'rotate-180')}
              aria-hidden="true"
            />
            Technical details
          </button>
          {open ? (
            <pre className="mt-2 max-h-40 overflow-auto rounded-lg border border-border/60 bg-background/60 p-3 text-left font-mono text-[11px] leading-relaxed text-muted-foreground whitespace-pre-wrap">
              {details}
            </pre>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
