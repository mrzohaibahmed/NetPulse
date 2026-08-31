import { useQuery } from '@tanstack/react-query'
import { getDeviceInterfaceStats } from '@/api'
import { queryKeys } from '@/hooks/queryKeys'
import { runWithConcurrency } from '@/utils/runWithConcurrency'
import type { InterfaceStat } from '@/types'

const STATS_FETCH_CONCURRENCY = 6
const STATS_REFETCH_INTERVAL = 20_000

export function useBatchedDeviceStats(deviceIds: string[]) {
  const sortedKey = [...deviceIds].sort().join('|')

  return useQuery({
    queryKey: [...queryKeys.interfaceStats('batched'), sortedKey],
    queryFn: async () => {
      const map = new Map<string, InterfaceStat[]>()
      await runWithConcurrency(deviceIds, STATS_FETCH_CONCURRENCY, async (deviceId) => {
        const response = await getDeviceInterfaceStats(deviceId)
        map.set(deviceId, response.data)
      })
      return map
    },
    enabled: deviceIds.length > 0,
    staleTime: 15_000,
    refetchInterval: STATS_REFETCH_INTERVAL,
  })
}
