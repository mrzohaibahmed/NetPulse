import { apiRequest } from '@/shared/api/client'
import type { ApiItemResponse } from '@/types'

export interface TopologyNode {
  id: string
  label: string
  ip: string
  type: string
  status: string
  isKnownDevice: boolean
}

export interface TopologyEdge {
  id: string
  source: string
  target: string
  label: string
  protocol: string
  animated?: boolean
}

export interface TopologyData {
  nodes: TopologyNode[]
  edges: TopologyEdge[]
}

export const getTopologySwitches = () => {
  return apiRequest<ApiItemResponse<TopologyNode[]>>('/api/topology/switches')
}

export const getLevel1Topology = (deviceId: string) => {
  return apiRequest<ApiItemResponse<TopologyData>>(`/api/topology/switch/${deviceId}`)
}

export const getFullTopology = () => {
  return apiRequest<ApiItemResponse<TopologyData>>('/api/topology/full')
}
