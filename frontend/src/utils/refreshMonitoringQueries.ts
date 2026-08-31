import type { QueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/hooks/queryKeys'

/** Query prefixes invalidated by the global navbar Refresh action. */
const MONITORING_QUERY_PREFIXES: readonly (readonly string[])[] = [
  queryKeys.health,
  queryKeys.dashboard.all,
  ['alerts'],
  ['devices'],
  ['device'],
  ['device-history'],
  ['interfaces'],
  ['device-interfaces'],
  ['interface-stats'],
  ['interface-history'],
  ['history'],
  queryKeys.isps,
  ['topology'],
  ['eligibility'],
  ['device-eligibility'],
  ['risk'],
  ['interface-risk'],
  ['confirmation'],
  ['safety'],
  ['storm-incidents'],
  ['storm-incident'],
  ['mitigation-history'],
  ['mitigation-detail'],
  ['recovery-history'],
  ['recovery-detail'],
  queryKeys.stormConfig,
  ['networks'],
  ['devices', 'networks'],
]

/**
 * Refetch monitoring-related caches without touching settings, users, reports, or auth.
 */
export async function refreshMonitoringQueries(queryClient: QueryClient) {
  await Promise.all(
    MONITORING_QUERY_PREFIXES.map((queryKey) =>
      queryClient.invalidateQueries({ queryKey }),
    ),
  )
}
