import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface PageHeaderProps {
  title: string
  description?: string
  align?: 'start' | 'center'
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
  align = 'start',
  actions,
  secondaryActions,
  meta,
  className,
}: PageHeaderProps) {
  const hasToolbar = Boolean(actions || secondaryActions)
  const isCentered = align === 'center'

  return (
    <header
      className={cn(
        // flex-wrap: when the action toolbar is wide, it drops below the title
        // instead of crushing the title column to a few pixels.
        isCentered
          ? 'grid grid-cols-1 items-start gap-x-6 gap-y-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,3fr)_minmax(0,1fr)]'
          : 'flex flex-wrap items-start justify-between gap-x-6 gap-y-4',
        className,
      )}
    >
      <div
        className={cn(
          'max-w-3xl space-y-2',
          isCentered
            ? 'w-full min-w-0 justify-self-center text-center sm:col-start-2'
            : 'min-w-[min(100%,20rem)] flex-1',
        )}
      >
        {meta ? <div className="flex flex-wrap items-center gap-2">{meta}</div> : null}
        <h1 className="np-h1 text-foreground">{title}</h1>
        {description ? <p className="np-description">{description}</p> : null}
      </div>
      {hasToolbar ? (
        <div
          className={cn(
            'np-toolbar w-full max-w-full flex-wrap sm:w-auto',
            isCentered
              ? 'justify-center sm:col-start-3 sm:row-start-1 sm:justify-self-end sm:justify-end'
              : 'ml-auto justify-start sm:justify-end',
          )}
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
