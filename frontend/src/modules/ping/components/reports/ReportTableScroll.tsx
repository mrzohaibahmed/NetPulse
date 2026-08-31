import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface ReportTableScrollProps {
  children: ReactNode
  /** Minimum table width before horizontal scroll inside this container. */
  minWidth?: string
  className?: string
}

export function ReportTableScroll({
  children,
  minWidth = '640px',
  className,
}: ReportTableScrollProps) {
  return (
    <div className={cn('w-full max-w-full overflow-x-auto', className)}>
      <div className="min-w-full" style={{ minWidth }}>
        {children}
      </div>
    </div>
  )
}
