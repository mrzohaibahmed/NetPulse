import { Search, X } from 'lucide-react'

import { Input } from '@/shared/ui/input'
import { cn } from '@/lib/utils'

type TopologyCanvasSearchBarProps = {
  searchInput: string
  exactSearch: boolean
  searchMatchCount: number
  placeholder?: string
  onSearchInputChange: (value: string) => void
  onExactSearchChange: (exact: boolean) => void
  onClear: () => void
  className?: string
}

export function TopologyCanvasSearchBar({
  searchInput,
  exactSearch,
  searchMatchCount,
  placeholder = 'Search IP, port, device… (Enter for exact)',
  onSearchInputChange,
  onExactSearchChange,
  onClear,
  className,
}: TopologyCanvasSearchBarProps) {
  const hasActiveSearch = Boolean(searchInput.trim())

  return (
    <div className={cn('flex w-72 flex-col gap-1', className)}>
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 z-10 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          type="text"
          inputMode="search"
          autoComplete="off"
          spellCheck={false}
          placeholder={placeholder}
          value={searchInput}
          onChange={(e) => {
            onSearchInputChange(e.target.value)
            onExactSearchChange(false)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              onExactSearchChange(true)
            }
            if (e.key === 'Escape') {
              onClear()
            }
          }}
          className="h-8 border-border/60 bg-background/80 pl-8 pr-9 text-xs"
        />
        {hasActiveSearch ? (
          <button
            type="button"
            className="absolute right-1 top-1/2 z-10 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            onClick={onClear}
            aria-label="Clear search"
            title="Clear search"
          >
            <X className="h-4 w-4" />
          </button>
        ) : null}
      </div>
      {hasActiveSearch ? (
        <span className="px-0.5 text-[10px] text-muted-foreground">
          {searchMatchCount > 0
            ? `${searchMatchCount} match${searchMatchCount === 1 ? '' : 'es'}${exactSearch ? ' (exact)' : ''}`
            : `No matches${exactSearch ? ' (exact)' : ''}`}
        </span>
      ) : null}
    </div>
  )
}
