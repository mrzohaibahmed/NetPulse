import type { ApiListResponse } from '@/types'

const DEFAULT_PAGE_LIMIT = 500

/**
 * Fetches every page from a paginated list API (backend max page size is typically 500).
 */
export async function fetchAllListPages<T>(
  fetchPage: (page: number, limit: number) => Promise<ApiListResponse<T>>,
  pageLimit = DEFAULT_PAGE_LIMIT,
): Promise<{ data: T[]; total: number }> {
  let page = 1
  const data: T[] = []
  let total = 0
  let totalPages = 1

  do {
    const response = await fetchPage(page, pageLimit)
    data.push(...response.data)
    total = response.total ?? response.count ?? data.length
    totalPages = response.totalPages ?? (total > 0 ? Math.ceil(total / pageLimit) : 1)
    page += 1
  } while (page <= totalPages)

  return { data, total }
}
