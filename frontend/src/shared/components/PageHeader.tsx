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
        // flex-wrap: when the action toolbar is wide, it drops below the title
        // instead of crushing the title column to a few pixels.
        'flex flex-wrap items-start justify-between gap-x-6 gap-y-4',
        className,
      )}
    >
      <div className="min-w-[min(100%,20rem)] max-w-3xl flex-1 space-y-2">
        {meta ? <div className="flex flex-wrap items-center gap-2">{meta}</div> : null}
        <h1 className="np-h1 text-foreground">{title}</h1>
        {description ? <p className="np-description">{description}</p> : null}
      </div>
      {hasToolbar ? (
        <div
          className="np-toolbar ml-auto w-full max-w-full flex-wrap justify-start sm:w-auto sm:justify-end"
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
