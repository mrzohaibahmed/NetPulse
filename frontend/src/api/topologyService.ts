import { apiRequest } from '@/shared/api/client'
import type { ApiItemResponse } from '@/types'

export interface TopologyNodeDetails {
  hostname: string
  ip: string
  type: string
  status: string
  vendor: string
  platform: string
  protocol: string
  managementAddress: string
  operatingSystem: string
  lastSeen: string
  systemDescription: string
  capabilities: string[]
  isKnownDevice: boolean
}

export interface TopologyNode {
  id: string
  hostname: string
  label: string
  ip: string
  mac?: string
  type: string
  status: string
  vendor: string
  platform: string
  protocol: string
  managementAddress: string
  isKnownDevice: boolean
  details: TopologyNodeDetails
}

export interface TopologyEdge {
  id: string
  source: string
  target: string
  label: string
  sourcePort: string
  targetPort: string
  isTrunk: boolean
  linkType: 'trunk' | 'access' | 'unknown' | string
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
