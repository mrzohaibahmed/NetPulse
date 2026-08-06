import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface SectionHeadingProps {
  title: string
  description?: string
  actions?: ReactNode
  className?: string
  as?: 'h2' | 'h3'
}

/** Shared section title used inside page bodies */
export function SectionHeading({
  title,
  description,
  actions,
  className,
  as: Tag = 'h2',
}: SectionHeadingProps) {
  return (
    <div
      className={cn(
        'flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between',
        className,
      )}
    >
      <div className="min-w-0 space-y-1">
        <Tag className={Tag === 'h2' ? 'np-h2' : 'np-h3'}>{title}</Tag>
        {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="np-toolbar shrink-0">{actions}</div> : null}
    </div>
  )
}
