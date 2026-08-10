import { useQuery } from '@tanstack/react-query'
import { getTopologySwitches, getLevel1Topology, getFullTopology } from '@/api/topologyService'

export function useTopologySwitches() {
  return useQuery({
    queryKey: ['topology', 'switches'],
    queryFn: async () => {
      const res = await getTopologySwitches()
      return res.data
    },
    refetchInterval: 60000,
  })
}

export function useLevel1Topology(deviceId: string | null) {
  return useQuery({
    queryKey: ['topology', 'level1', deviceId],
    queryFn: async () => {
      if (!deviceId) return { nodes: [], edges: [] }
      const res = await getLevel1Topology(deviceId)
      return res.data
    },
    enabled: !!deviceId,
    refetchInterval: 30000,
  })
}

export function useFullTopology(enabled: boolean) {
  return useQuery({
    queryKey: ['topology', 'full'],
    queryFn: async () => {
      const res = await getFullTopology()
      return res.data
    },
    enabled,
    refetchInterval: 30000,
  })
}
