import { apiRequest, downloadFile } from './client'
import type {
  AlertItem,
  ApiItemResponse,
  ApiListResponse,
  AppSettings,
  ChartSlice,
  DashboardStatistics,
  DashboardSummary,
  Device,
  DeviceHistoryResponse,
  DevicePayload,
  DeviceStatusRow,
  DiscoveryResult,
  NetworkHint,
  PaginationParams,
  PingHistory,
  ResponseTimeChartPoint,
  ScanActivityChartPoint,
  UptimeRow,
  User,
} from '../types'

function toQuery(params: PaginationParams = {}): string {
  const search = new URLSearchParams()

  if (params.page) search.set('page', String(params.page))
  if (params.limit) search.set('limit', String(params.limit))
  if (params.q?.trim()) search.set('q', params.q.trim())
  if (params.status && params.status !== 'all') search.set('status', params.status)
  if (params.scanType && params.scanType !== 'all') search.set('scanType', params.scanType)
  if (params.deviceType && params.deviceType !== 'all') search.set('deviceType', params.deviceType)
  if (params.deviceId) search.set('deviceId', params.deviceId)
  if (params.startDate) search.set('startDate', params.startDate)
  if (params.endDate) search.set('endDate', params.endDate)

  const query = search.toString()
  return query ? `?${query}` : ''
}

export const login = (username: string, password: string) =>
  apiRequest<{
    success: boolean
    token: string
    expiresInHours: number
    user: User
    message?: string
  }>('/api/auth/login', {
    method: 'POST',
    body: { username, password },
    skipAuth: true,
  })

export const getMe = () =>
  apiRequest<{ success: boolean; user: User }>('/api/auth/me')

export const updateAccount = (payload: {
  currentPassword: string
  username?: string
  newPassword?: string
}) =>
  apiRequest<{
    success: boolean
    message: string
    token: string
    user: User
  }>('/api/auth/account', {
    method: 'PUT',
    body: payload,
  })

export const getUsers = () =>
  apiRequest<{ success: boolean; count: number; data: User[] }>('/api/users')

export const updateUser = (
  id: string,
  payload: { username?: string; password?: string },
) =>
  apiRequest<{ success: boolean; message: string; data: User }>(`/api/users/${id}`, {
    method: 'PUT',
    body: payload,
  })

export const getDevices = (params: PaginationParams = {}) =>
  apiRequest<ApiListResponse<Device>>(`/api/devices${toQuery(params)}`)

export const getDevice = (id: string) =>
  apiRequest<ApiItemResponse<Device>>(`/api/devices/${id}`)

export const createDevice = (payload: DevicePayload) =>
  apiRequest<ApiItemResponse<Device>>('/api/devices', {
    method: 'POST',
    body: payload,
  })

export const updateDevice = (id: string, payload: Partial<DevicePayload>) =>
  apiRequest<ApiItemResponse<Device>>(`/api/devices/${id}`, {
    method: 'PUT',
    body: payload,
  })

export const deleteDevice = (id: string) =>
  apiRequest<{ success: boolean; message: string }>(`/api/devices/${id}`, {
    method: 'DELETE',
  })

export const importDevicesCsv = (file: File) => {
  const form = new FormData()
  form.append('file', file)
  return apiRequest<{
    success: boolean
    message: string
    created: number
    skipped: number
    errors: Array<{ row: number; error: string }>
  }>('/api/devices/import', {
    method: 'POST',
    body: form,
    timeoutMs: 120000,
  })
}

export const scanDevice = (id: string) =>
  apiRequest<ApiItemResponse<Device>>(`/api/devices/${id}/scan`, {
    method: 'POST',
    timeoutMs: 30000,
  })

export const scanDeviceNmap = (id: string) =>
  apiRequest<{ success: boolean; message: string; data: Device }>(`/api/devices/${id}/scan-details`, {
    method: 'POST',
    timeoutMs: 180000,
  })

export const getHistory = (params: PaginationParams = {}) =>
  apiRequest<ApiListResponse<PingHistory>>(`/api/history${toQuery(params)}`)

export const getDeviceHistory = (
  id: string,
  params: { startDate?: string; endDate?: string; limit?: number } = {},
) => {
  const search = new URLSearchParams()
  if (params.startDate) search.set('startDate', params.startDate)
  if (params.endDate) search.set('endDate', params.endDate)
  if (params.limit) search.set('limit', String(params.limit))
  const q = search.toString()
  return apiRequest<DeviceHistoryResponse>(
    `/api/devices/${id}/history${q ? `?${q}` : ''}`,
  )
}

export const getNetworkHint = () =>
  apiRequest<{ success: boolean; hint: NetworkHint }>('/api/discovery/network-hint', {
    timeoutMs: 5000,
  })

export const discoverRange = (startIP: string, endIP: string) =>
  apiRequest<DiscoveryResult>('/api/discovery/scan-range', {
    method: 'POST',
    body: { startIP, endIP },
    timeoutMs: 300000,
  })

export const getDashboardSummary = () =>
  apiRequest<{ success: boolean; summary: DashboardSummary }>('/api/dashboard/summary')

export const getDashboardStatistics = () =>
  apiRequest<{ success: boolean; statistics: DashboardStatistics }>('/api/dashboard/statistics')

export const getRecentHistory = () =>
  apiRequest<{ success: boolean; count: number; history: PingHistory[] }>(
    '/api/dashboard/recent-history',
  )

export const getDeviceStatus = () =>
  apiRequest<{ success: boolean; count: number; devices: DeviceStatusRow[] }>(
    '/api/dashboard/device-status',
  )

export const getDeviceStatusChart = () =>
  apiRequest<{ success: boolean; chart: ChartSlice[] }>('/api/dashboard/charts/device-status')

export const getDeviceTypeChart = () =>
  apiRequest<{ success: boolean; chart: ChartSlice[] }>('/api/dashboard/charts/device-type')

export const getResponseTimeChart = () =>
  apiRequest<{ success: boolean; chart: ResponseTimeChartPoint[] }>(
    '/api/dashboard/charts/response-time',
  )

export const getScanActivityChart = () =>
  apiRequest<{ success: boolean; chart: ScanActivityChartPoint[] }>(
    '/api/dashboard/charts/scan-activity',
  )

export const getAlerts = (params: PaginationParams & { status?: string } = {}) =>
  apiRequest<ApiListResponse<AlertItem>>(`/api/alerts${toQuery(params)}`)

export const acknowledgeAlert = (id: string) =>
  apiRequest<ApiItemResponse<AlertItem>>(`/api/alerts/${id}/acknowledge`, {
    method: 'POST',
  })

export const dismissAlert = (id: string) =>
  apiRequest<ApiItemResponse<AlertItem>>(`/api/alerts/${id}/dismiss`, {
    method: 'POST',
  })

export const getSettings = () =>
  apiRequest<{ success: boolean; data: AppSettings }>('/api/settings')

export const updateSettings = (payload: Record<string, unknown>) =>
  apiRequest<{ success: boolean; message: string; data: AppSettings }>('/api/settings', {
    method: 'PUT',
    body: payload,
  })

export const getUptimeReport = (params: PaginationParams = {}) =>
  apiRequest<{ success: boolean; count: number; data: UptimeRow[] }>(
    `/api/reports/uptime${toQuery(params)}`,
  )

export const exportDevicesReport = (params: PaginationParams & { format?: string } = {}) => {
  const search = new URLSearchParams()
  if (params.deviceType && params.deviceType !== 'all') search.set('deviceType', params.deviceType)
  if (params.status && params.status !== 'all') search.set('status', params.status)
  if (params.format) search.set('format', params.format)
  const q = search.toString()
  return downloadFile(`/api/reports/export/devices${q ? `?${q}` : ''}`, 'devices.csv')
}

export const exportHistoryReport = (params: PaginationParams & { format?: string } = {}) => {
  const search = new URLSearchParams()
  if (params.deviceType && params.deviceType !== 'all') search.set('deviceType', params.deviceType)
  if (params.status && params.status !== 'all') search.set('status', params.status)
  if (params.startDate) search.set('startDate', params.startDate)
  if (params.endDate) search.set('endDate', params.endDate)
  if (params.format) search.set('format', params.format)
  const q = search.toString()
  return downloadFile(`/api/reports/export/history${q ? `?${q}` : ''}`, 'status_logs.csv')
}

export const getHealth = () =>
  apiRequest<{ server: string; database: string; error?: string }>('/health', {
    timeoutMs: 5000,
    skipAuth: true,
  })
