import { useCallback, useMemo, useState } from 'react'

export function useClientPagination<T>(items: T[], initialLimit = 25) {
  const [page, setPageState] = useState(1)
  const [limit, setLimitState] = useState(initialLimit)

  const total = items.length
  const totalPages = total > 0 ? Math.ceil(total / limit) : 0
  const safePage = totalPages === 0 ? 1 : Math.min(page, totalPages)

  const pageItems = useMemo(() => {
    const start = (safePage - 1) * limit
    return items.slice(start, start + limit)
  }, [items, safePage, limit])

  const setPage = useCallback((next: number) => {
    setPageState(Math.max(1, next))
  }, [])

  const setLimit = useCallback((next: number) => {
    setLimitState(next)
    setPageState(1)
  }, [])

  const reset = useCallback(() => {
    setPageState(1)
  }, [])

  return {
    page: safePage,
    limit,
    total,
    totalPages,
    pageItems,
    setPage,
    setLimit,
    reset,
  }
}
