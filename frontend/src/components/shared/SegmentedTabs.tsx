import { cn } from '@/lib/utils'

export interface SegmentedTabOption<T extends string> {
  value: T
  label: string
  icon?: React.ComponentType<{ className?: string }>
  count?: number
}

interface SegmentedTabsProps<T extends string> {
  options: SegmentedTabOption<T>[]
  value: T
  onChange: (value: T) => void
  className?: string
}

/**
 * Lightweight two-or-more-way tab switcher built on theme tokens only
 * (no Radix Tabs dependency in this project yet). Used for the
 * Device/Storm split on AlertsPage and the Monitoring/Storm split on
 * SettingsPage.
 */
export function SegmentedTabs<T extends string>({ options, value, onChange, className }: SegmentedTabsProps<T>) {
  return (
    <div className={cn('inline-flex items-center gap-1 rounded-lg border border-border bg-secondary/70 p-1 dark:bg-secondary/40', className)} role="tablist">
      {options.map((option) => {
        const Icon = option.icon
        const active = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(option.value)}
            className={cn(
              'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
              active
                ? 'bg-primary text-primary-foreground shadow-sm'
                : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
            )}
          >
            {Icon ? <Icon className="h-3.5 w-3.5" /> : null}
            {option.label}
            {option.count != null ? (
              <span
                className={cn(
                  'ml-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-semibold',
                  active ? 'bg-primary-foreground/20' : 'bg-muted',
                )}
              >
                {option.count}
              </span>
            ) : null}
          </button>
        )
      })}
    </div>
  )
}
