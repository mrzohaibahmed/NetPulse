import { apiRequest } from '@/shared/api/client'
import type { PaginationParams } from '@/types'

export type HardwareHealthStatus =
  | 'healthy'
  | 'warning'
  | 'critical'
  | 'unknown'
  | 'not_available'
  | string

export type CollectionStatus = 'success' | 'partial' | 'failed' | 'unknown' | string

export interface HardwareSensor {
  name: string
  sensorType?: string
  value: number | null
  unit: string | null
  status: HardwareHealthStatus
  warningThreshold?: number | null
  criticalThreshold?: number | null
  thresholdSource?: string | null
  source?: string
  available?: boolean
}

export interface HardwareFan {
  name: string
  status: HardwareHealthStatus
  speedRpm: number | null
  source?: string
  available?: boolean
}

export interface HardwarePowerSupply {
  name: string
  status: HardwareHealthStatus
  inputStatus?: string | null
  outputStatus?: string | null
  source?: string
  available?: boolean
}

export interface HardwareInventory {
  model: string | null
  serialNumber: string | null
  productId: string | null
  firmwareVersion: string | null
  iosVersion: string | null
  hostname: string | null
  uptime: string | null
  bootReason: string | null
  chassis: Array<Record<string, string | null | undefined>>
  modules: Array<Record<string, string | null | undefined>>
}

export interface HardwareAlarm {
  message: string
  severity?: string
  source?: string
}

export interface SwitchHardwareCurrent {
  deviceId: string
  vendor?: string | null
  platform: string | null
  overallHealth: HardwareHealthStatus
  collectionStatus: CollectionStatus
  inventory: HardwareInventory
  temperature: {
    status: HardwareHealthStatus
    sensors: HardwareSensor[]
  }
  fans: {
    count: number | null
    healthyCount: number | null
    failedCount: number | null
    items: HardwareFan[]
  }
  powerSupplies: {
    count: number | null
    healthyCount: number | null
    failedCount: number | null
    redundancy: string | null
    items: HardwarePowerSupply[]
  }
  cpu: {
    utilizationPercent: number | null
    status: HardwareHealthStatus
  }
  memory: {
    utilizationPercent: number | null
    status: HardwareHealthStatus
  }
  hardwareAlarms: HardwareAlarm[]
  availability: {
    snmp?: string
    ssh?: string
  }
  evidence?: {
    logEvidence?: Array<{ message?: string; source?: string }>
  }
  lastSuccessfulCollectionAt: string | null
  lastAttemptedCollectionAt: string | null
  lastError: string | null
  updatedAt?: string | null
}

export interface SwitchHardwareHistoryItem {
  deviceId: string
  timestamp: string
  overallHealth?: HardwareHealthStatus | null
  collectionStatus?: CollectionStatus | null
  platform?: string | null
  temperature?: SwitchHardwareCurrent['temperature'] | null
  fans?: SwitchHardwareCurrent['fans'] | null
  powerSupplies?: SwitchHardwareCurrent['powerSupplies'] | null
  cpu?: SwitchHardwareCurrent['cpu'] | null
  memory?: SwitchHardwareCurrent['memory'] | null
  hardwareAlarms?: HardwareAlarm[]
  source?: string
}

export interface SwitchHardwareEvent {
  id: string
  deviceId: string
  eventType: string
  severity: string
  component: string
  description: string
  source: string
  resolved: boolean
  metadata?: Record<string, unknown>
  timestamp: string
  resolvedAt?: string | null
}

export interface SwitchOutageIncident {
  id: string
  deviceId: string
  startedAt: string
  endedAt: string | null
  durationSeconds: number | null
  status: string
  evidence: {
    trigger?: string
    items?: string[]
    explanation?: string
    [key: string]: unknown
  }
  lastKnownHardware?: Partial<SwitchHardwareCurrent> | Record<string, unknown>
  recoveryHardware?: Partial<SwitchHardwareCurrent> | Record<string, unknown>
  rootCause: string
  confidence: 'confirmed' | 'suspected' | 'unknown' | string
  confirmed: boolean
  timeline: Array<Record<string, unknown>>
  createdAt?: string
  updatedAt?: string
}

export interface HardwareListResponse<T> {
  success: boolean
  items: T[]
  pagination: {
    page: number
    limit: number
    total: number
    totalPages: number
  }
}

function toQuery(params: object = {}) {
  const search = new URLSearchParams()
  Object.entries(params as Record<string, unknown>).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    search.set(key, String(value))
  })
  const q = search.toString()
  return q ? `?${q}` : ''
}

export function getSwitchHardware(deviceId: string) {
  return apiRequest<{ success: boolean; hardware: SwitchHardwareCurrent | null }>(
    `/api/switches/${deviceId}/hardware`,
  )
}

export function getSwitchHardwareHistory(
  deviceId: string,
  params: PaginationParams & { start?: string; end?: string } = {},
) {
  return apiRequest<HardwareListResponse<SwitchHardwareHistoryItem>>(
    `/api/switches/${deviceId}/hardware/history${toQuery(params)}`,
  )
}

export function getSwitchHardwareEvents(
  deviceId: string,
  params: PaginationParams & { eventType?: string } = {},
) {
  return apiRequest<HardwareListResponse<SwitchHardwareEvent>>(
    `/api/switches/${deviceId}/hardware/events${toQuery(params)}`,
  )
}

export function getSwitchOutages(
  deviceId: string,
  params: PaginationParams & { status?: string } = {},
) {
  return apiRequest<HardwareListResponse<SwitchOutageIncident>>(
    `/api/switches/${deviceId}/outages${toQuery(params)}`,
  )
}

export function getSwitchOutage(deviceId: string, incidentId: string) {
  return apiRequest<{ success: boolean; outage: SwitchOutageIncident }>(
    `/api/switches/${deviceId}/outages/${incidentId}`,
  )
}

export function collectSwitchHardware(deviceId: string) {
  return apiRequest<{
    success: boolean
    hardware: SwitchHardwareCurrent | null
    errors: string[]
  }>(`/api/switches/${deviceId}/hardware/collect`, {
    method: 'POST',
    body: {},
    timeoutMs: 60_000,
  })
}
