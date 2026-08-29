import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getTopologySwitches,
  getLevel1Topology,
  getFullTopology,
  getTopologyLayout,
  saveTopologyLayout,
  type TopologyLayout,
} from '@/api/topologyService'

export function useTopologySwitches() {
  return useQuery({
    queryKey: ['topology', 'switches'],
    queryFn: async () => {
      const res = await getTopologySwitches()
      return res.data
    },
    refetchInterval: 30000,
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

export function useTopologyLayout(viewKey: string) {
  return useQuery({
    queryKey: ['topology', 'layout', viewKey],
    queryFn: async () => {
      const res = await getTopologyLayout(viewKey)
      return res.layout
    },
    staleTime: Infinity,
  })
}

export function useSaveTopologyLayoutMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: {
      viewKey: string
      nodes: TopologyLayout['nodes']
      edges: TopologyLayout['edges']
    }) => saveTopologyLayout(payload),
    onSuccess: (res) => {
      const key = res.layout?.viewKey
      if (key) {
        void qc.setQueryData(['topology', 'layout', key], res.layout)
      }
    },
  })
}
