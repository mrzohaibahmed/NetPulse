import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Skeleton } from '@/shared/ui/skeleton'

interface LoadingStateProps {
  label?: string
  className?: string
}

export function LoadingState({ label = 'Loading…', className }: LoadingStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-3 py-16 text-muted-foreground',
        className,
      )}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <Loader2 className="h-6 w-6 animate-spin text-primary" aria-hidden="true" />
      <span className="text-sm">{label}</span>
    </div>
  )
}

/** Shared KPI row skeleton — matches KpiCard min-height */
export function KpiSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} className="min-h-[7.5rem] rounded-xl" />
      ))}
    </div>
  )
}

export function DashboardSkeleton() {
  return (
    <div className="np-page" aria-busy="true" aria-label="Loading dashboard">
      <div className="space-y-2">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-4 w-80 max-w-full" />
      </div>
      <KpiSkeleton count={4} />
      <div className="grid gap-4 lg:grid-cols-3">
        <Skeleton className="h-72 rounded-xl lg:col-span-1" />
        <Skeleton className="h-72 rounded-xl lg:col-span-2" />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Skeleton className="h-64 rounded-xl" />
        <Skeleton className="h-64 rounded-xl" />
      </div>
    </div>
  )
}

export function TableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div
      className="np-table-shell space-y-0 overflow-hidden p-0"
      aria-busy="true"
      aria-label="Loading table"
    >
      <div className="np-table-toolbar">
        <Skeleton className="h-9 w-48" />
        <Skeleton className="h-9 w-32" />
        <Skeleton className="ml-auto h-9 w-24" />
      </div>
      <div className="space-y-0 p-4 pt-2">
        <Skeleton className="mb-2 h-11 w-full" />
        {Array.from({ length: rows }).map((_, i) => (
          <Skeleton key={i} className="mb-2 h-12 w-full last:mb-0" />
        ))}
      </div>
    </div>
  )
}

export function PageSkeleton() {
  return (
    <div className="np-page" aria-busy="true" aria-label="Loading page">
      <div className="flex flex-col gap-4 sm:flex-row sm:justify-between">
        <div className="space-y-2">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-4 w-72 max-w-full" />
        </div>
        <div className="flex gap-2">
          <Skeleton className="h-10 w-24" />
          <Skeleton className="h-10 w-28" />
        </div>
      </div>
      <KpiSkeleton count={4} />
      <TableSkeleton rows={5} />
    </div>
  )
}
