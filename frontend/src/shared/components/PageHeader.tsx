import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface PageHeaderProps {
  title: string
  description?: string
  /** Primary actions (right-aligned) */
  actions?: ReactNode
  /** Secondary / tertiary actions shown before primary */
  secondaryActions?: ReactNode
  /** Optional left-side meta (badges, breadcrumbs) */
  meta?: ReactNode
  className?: string
}

export function PageHeader({
  title,
  description,
  actions,
  secondaryActions,
  meta,
  className,
}: PageHeaderProps) {
  const hasToolbar = Boolean(actions || secondaryActions)

  return (
    <header
      className={cn(
        'flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between',
        className,
      )}
    >
      <div className="min-w-0 space-y-2">
        {meta ? <div className="flex flex-wrap items-center gap-2">{meta}</div> : null}
        <h1 className="np-h1 text-foreground">{title}</h1>
        {description ? <p className="np-description max-w-3xl">{description}</p> : null}
      </div>
      {hasToolbar ? (
        <div
          className="np-toolbar shrink-0 sm:justify-end"
          role="toolbar"
          aria-label="Page actions"
        >
          {secondaryActions}
          {actions}
        </div>
      ) : null}
    </header>
  )
}
