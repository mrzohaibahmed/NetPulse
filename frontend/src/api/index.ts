import { apiRequest, downloadFile } from '@/shared/api/client'
import type {
  AlertItem,
  ApiItemResponse,
  ApiListResponse,
  AppSettings,
  ChartSlice,
  DashboardDeviceMetrics,
  DashboardStatistics,
  DashboardSummary,
  Device,
  DeviceHistoryResponse,
  DevicePayload,
  DeviceStatusRow,
  DiscoveryResult,
  DiscoveryDevice,
  EligibilityResult,
  InterfaceStat,
  IspConnection,
  IspConnectionPayload,
  NetworkHint,
  NetworkInterface,
  PaginationParams,
  PingHistory,
  ResponseTimeChartPoint,
  RiskResult,
  ConfirmationResult,
  ScanActivityChartPoint,
  SafetyResult,
  StormConfig,
  StormIncident,
  PrepareResult,
  MitigationLog,
  RecoveryLog,
  UptimeRow,
  AlertsIncidentsReport,
  AvailabilityReport,
  ExecutiveReport,
  PerformanceReport,
  ReportFilterOptions,
  StormIncidentDetail,
  StormManagementReport,
  User,
  UserRole,
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
  if (params.period) search.set('period', params.period)
  if (params.interface && params.interface !== 'all') search.set('interface', params.interface)
  if (params.alertType && params.alertType !== 'all') search.set('alertType', params.alertType)
  if (params.alertStatus && params.alertStatus !== 'all') {
    search.set('alertStatus', params.alertStatus)
  }
  if (params.incidentStatus && params.incidentStatus !== 'all') {
    search.set('incidentStatus', params.incidentStatus)
  }
  if (params.format) search.set('format', params.format)
  if (params.adminStatus && params.adminStatus !== 'all') {
    search.set('adminStatus', params.adminStatus)
  }
  if (params.operStatus && params.operStatus !== 'all') {
    search.set('operStatus', params.operStatus)
  }
  if (params.mode && params.mode !== 'all') search.set('mode', params.mode)
  if (params.eligible === true) search.set('eligible', 'true')
  if (params.eligible === false) search.set('eligible', 'false')
  if (params.critical === true) search.set('critical', 'true')
  if (params.critical === false) search.set('critical', 'false')
  if (params.severity && params.severity !== 'all') {
    search.set('severity', params.severity)
  }
  if (params.state && params.state !== 'all') {
    search.set('state', params.state)
  }
  if (params.safetyStatus && params.safetyStatus !== 'all') {
    search.set('status', params.safetyStatus)
  }
  if ((params as any).network && (params as any).network !== 'all') {
    search.set('network', (params as any).network)
  }

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
  payload: { username?: string; password?: string; role?: UserRole; active?: boolean },
) =>
  apiRequest<{ success: boolean; message: string; data: User }>(`/api/users/${id}`, {
    method: 'PUT',
    body: payload,
  })

export const createUser = (payload: {
  username: string
  password: string
  role: UserRole
  active: boolean
}) =>
  apiRequest<{ success: boolean; message: string; data: User }>('/api/users', {
    method: 'POST',
    body: payload,
  })

export const deleteUser = (id: string) =>
  apiRequest<{ success: boolean; message: string }>(`/api/users/${id}`, {
    method: 'DELETE',
  })

export const getDevices = (params: PaginationParams & { network?: string } = {}) =>
  apiRequest<ApiListResponse<Device>>(`/api/devices${toQuery(params)}`)

export const getDeviceNetworks = () =>
  apiRequest<{ success: boolean; data: string[] }>('/api/devices/networks')

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

export const pingAllDevices = () =>
  apiRequest<{
    success: boolean
    message: string
    total: number
    online: number
    failed: number
    errors: Array<{ ip: string; hostname?: string; error: string }>
  }>('/api/devices/ping-all', {
    method: 'POST',
    timeoutMs: 600000,
  })

export const scanDeviceNmap = (id: string) =>
  apiRequest<{ success: boolean; message: string; data: Device }>(`/api/devices/${id}/scan-details`, {
    method: 'POST',
    timeoutMs: 180000,
  })

export const scanAllDevicesNmap = () =>
  apiRequest<{
    success: boolean
    message: string
    total: number
    scanned: number
    failed: number
    errors: Array<{ ip: string; error: string }>
  }>('/api/devices/scan-all-details', {
    method: 'POST',
    timeoutMs: 900000,
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

export const getDashboardDeviceMetrics = () =>
  apiRequest<{ success: boolean; metrics: DashboardDeviceMetrics }>(
    '/api/dashboard/device-metrics',
  )

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

export type HistoryDeletionScope = 'ping' | 'telemetry' | 'incidents' | 'all'

export const deleteHistory = (scope: HistoryDeletionScope) =>
  apiRequest<{
    success: boolean
    message: string
    scope: HistoryDeletionScope
    deleted: Record<string, number>
    totalDeleted: number
    failed?: Record<string, string>
  }>('/api/settings/history', {
    method: 'DELETE',
    body: { scope },
  })

export const getUptimeReport = (params: PaginationParams = {}) =>
  apiRequest<{ success: boolean; count: number; data: UptimeRow[] }>(
    `/api/reports/uptime${toQuery(params)}`,
  )

const REPORT_TIMEOUT_MS = 60_000

export const getReportFilters = (deviceId?: string) =>
  apiRequest<ReportFilterOptions>(
    `/api/reports/filters${deviceId ? `?deviceId=${encodeURIComponent(deviceId)}` : ''}`,
  )

export const getExecutiveReport = (params: PaginationParams = {}) =>
  apiRequest<ExecutiveReport>(`/api/reports/executive${toQuery(params)}`, {
    timeoutMs: REPORT_TIMEOUT_MS,
  })

export const getAvailabilityReport = (params: PaginationParams = {}) =>
  apiRequest<AvailabilityReport>(`/api/reports/availability${toQuery(params)}`, {
    timeoutMs: REPORT_TIMEOUT_MS,
  })

export const getPerformanceReport = (params: PaginationParams = {}) =>
  apiRequest<PerformanceReport>(`/api/reports/performance${toQuery(params)}`, {
    timeoutMs: REPORT_TIMEOUT_MS,
  })

export const getAlertsIncidentsReport = (params: PaginationParams = {}) =>
  apiRequest<AlertsIncidentsReport>(`/api/reports/alerts-incidents${toQuery(params)}`, {
    timeoutMs: REPORT_TIMEOUT_MS,
  })

export const getStormManagementReport = (params: PaginationParams = {}) =>
  apiRequest<StormManagementReport>(`/api/reports/storm${toQuery(params)}`, {
    timeoutMs: REPORT_TIMEOUT_MS,
  })

export const getStormIncidentReportDetail = (incidentId: string) =>
  apiRequest<StormIncidentDetail>(
    `/api/reports/storm/incidents/${encodeURIComponent(incidentId)}`,
  )

export const exportManagementReport = (
  reportType: string,
  params: PaginationParams & { format?: string } = {},
) => {
  const kind = reportType === 'alerts' ? 'alerts' : reportType
  return downloadFile(
    `/api/reports/export/${kind}${toQuery(params)}`,
    `report_${kind}.csv`,
  )
}

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

export const exportStormIncidentsReport = (params: PaginationParams & { format?: string } = {}) => {
  const search = new URLSearchParams()
  if (params.limit) search.set('limit', String(params.limit))
  if (params.format) search.set('format', params.format)
  const q = search.toString()
  return downloadFile(`/api/reports/export/storm/incidents${q ? `?${q}` : ''}`, 'storm_incidents.csv')
}

export const exportStormMitigationsReport = (params: PaginationParams & { format?: string } = {}) => {
  const search = new URLSearchParams()
  if (params.limit) search.set('limit', String(params.limit))
  if (params.format) search.set('format', params.format)
  const q = search.toString()
  return downloadFile(
    `/api/reports/export/storm/mitigations${q ? `?${q}` : ''}`,
    'storm_mitigations.csv',
  )
}

export const exportStormRecoveriesReport = (params: PaginationParams & { format?: string } = {}) => {
  const search = new URLSearchParams()
  if (params.limit) search.set('limit', String(params.limit))
  if (params.format) search.set('format', params.format)
  const q = search.toString()
  return downloadFile(`/api/reports/export/storm/recoveries${q ? `?${q}` : ''}`, 'storm_recoveries.csv')
}

export const getHealth = () =>
  apiRequest<{ server: string; database: string; error?: string }>('/health', {
    timeoutMs: 5000,
    skipAuth: true,
  })

export const getIsps = () =>
  apiRequest<{ success: boolean; count: number; data: IspConnection[] }>('/api/isps')

export const updateIsp = (id: string, payload: Partial<IspConnectionPayload>) =>
  apiRequest<{ success: boolean; message: string; data: IspConnection }>(`/api/isps/${id}`, {
    method: 'PUT',
    body: payload,
  })

export const scanIsp = (id: string) =>
  apiRequest<{ success: boolean; message: string; data: IspConnection }>(
    `/api/isps/${id}/scan`,
    {
      method: 'POST',
      timeoutMs: 30000,
    },
  )

export const getInterfaces = (params: PaginationParams = {}) =>
  apiRequest<ApiListResponse<NetworkInterface>>(`/api/interfaces${toQuery(params)}`)

export const getDeviceInterfaces = (deviceId: string, params: PaginationParams = {}) =>
  apiRequest<
    ApiListResponse<NetworkInterface> & {
      deviceId: string
      hostname?: string
      ipAddress?: string
    }
  >(`/api/interfaces/${deviceId}${toQuery(params)}`)

export const getDeviceInterfaceStats = (deviceId: string) =>
  apiRequest<{
    success: boolean
    deviceId: string
    hostname?: string
    ipAddress?: string
    count: number
    data: InterfaceStat[]
  }>(`/api/interfaces/${deviceId}/stats`)

export const getInterfaceHistory = (
  deviceId: string,
  interfaceName: string,
  params: PaginationParams = {},
) => {
  const encoded = encodeURIComponent(interfaceName)
  return apiRequest<
    ApiListResponse<InterfaceStat> & {
      deviceId: string
      interfaceName: string
      hostname?: string
      ipAddress?: string
    }
  >(`/api/interfaces/${deviceId}/${encoded}/history${toQuery(params)}`)
}

export const discoverDeviceInterfaces = (deviceId: string) =>
  apiRequest<{
    success: boolean
    message: string
    summary?: unknown
    count?: number
    data?: NetworkInterface[]
  }>(`/api/interfaces/discover/${deviceId}`, {
    method: 'POST',
    timeoutMs: 180000,
  })

export const discoverAllInterfaces = () =>
  apiRequest<{
    success: boolean
    message: string
    total: number
    discoveredDevices: number
    failed: number
    interfaceCount: number
    errors: Array<{ ip?: string; deviceId?: string; error?: string }>
  }>('/api/interfaces/discover-all', {
    method: 'POST',
    timeoutMs: 300000,
  })

export const collectDeviceInterfaceStats = (deviceId: string) =>
  apiRequest<{
    success: boolean
    message: string
    summary?: unknown
    count?: number
    data?: InterfaceStat[]
  }>(`/api/interfaces/${deviceId}/stats/collect`, {
    method: 'POST',
    timeoutMs: 180000,
  })

export const setInterfaceMonitoring = (payload: {
  deviceId: string
  interfaceName: string
  enabled?: boolean
  monitoringMode?: 'AUTO' | 'DISABLED_BY_USER'
}) => {
  const encoded = encodeURIComponent(payload.interfaceName)
  return apiRequest<{
    success: boolean
    message: string
    data: NetworkInterface
  }>(`/api/interfaces/${payload.deviceId}/${encoded}/monitoring`, {
    method: 'POST',
    body: {
      ...(payload.monitoringMode ? { monitoringMode: payload.monitoringMode } : {}),
      ...(payload.enabled !== undefined ? { enabled: payload.enabled } : {}),
    },
  })
}

export const manualShutdownInterface = (payload: {
  deviceId: string
  interfaceName: string
  confirm: boolean
  reason: string
}) => {
  const encoded = encodeURIComponent(payload.interfaceName)
  const reason = payload.reason.trim()
  return apiRequest<{
    success: boolean
    message: string
    incidentId: string
    status: string
    commandsExecuted?: string[]
  }>(`/api/interfaces/${payload.deviceId}/${encoded}/manual-shutdown`, {
    method: 'POST',
    body: { confirm: payload.confirm, reason },
    timeoutMs: 180000,
  })
}

export const manualRecoverInterface = (payload: {
  deviceId: string
  interfaceName: string
  confirm: boolean
  incidentId?: string
}) => {
  const encoded = encodeURIComponent(payload.interfaceName)
  return apiRequest<{
    success: boolean
    message: string
    incidentId: string
    status: string
    retryCount?: number
  }>(`/api/interfaces/${payload.deviceId}/${encoded}/manual-recover`, {
    method: 'POST',
    body: {
      confirm: payload.confirm,
      ...(payload.incidentId ? { incidentId: payload.incidentId } : {}),
    },
    timeoutMs: 180000,
  })
}

export const collectAllInterfaceStats = () =>
  apiRequest<{
    success: boolean
    message: string
    total: number
    succeeded: number
    failed: number
    samples: number
    errors: Array<{ ip?: string; deviceId?: string; error?: string }>
  }>('/api/interfaces/stats/collect-all', {
    method: 'POST',
    timeoutMs: 300000,
  })

export const getEligibilityResults = (params: PaginationParams = {}) =>
  apiRequest<ApiListResponse<EligibilityResult>>(
    `/api/storm/eligibility${toQuery(params)}`,
  )

export const getDeviceEligibility = (deviceId: string, params: PaginationParams = {}) =>
  apiRequest<ApiListResponse<EligibilityResult>>(
    `/api/storm/eligibility/${deviceId}${toQuery(params)}`,
  )

export const getInterfaceEligibility = (
  deviceId: string,
  interfaceName: string,
  params: PaginationParams = {},
) => {
  const encoded = encodeURIComponent(interfaceName)
  return apiRequest<ApiItemResponse<EligibilityResult>>(
    `/api/storm/eligibility/${deviceId}/${encoded}${toQuery(params)}`,
  )
}

export const evaluateInterfaceEligibility = (payload: {
  deviceId?: string
  interface?: string
  name?: string
  [key: string]: unknown
}) =>
  apiRequest<ApiItemResponse<EligibilityResult>>('/api/storm/eligibility/evaluate', {
    method: 'POST',
    body: payload,
  })

export const evaluateAllEligibility = () =>
  apiRequest<{
    success: boolean
    message: string
    total: number
    eligible: number
    ineligible: number
    errors: number
    skipped: boolean
  }>('/api/storm/eligibility/evaluate-all', {
    method: 'POST',
    timeoutMs: 300000,
  })

export const getStormConfig = () =>
  apiRequest<ApiItemResponse<StormConfig>>('/api/storm/config')

export const getRiskResults = (params: PaginationParams = {}) =>
  apiRequest<ApiListResponse<RiskResult>>(`/api/storm/risk${toQuery(params)}`)

export const getDeviceRisk = (deviceId: string, params: PaginationParams = {}) =>
  apiRequest<ApiListResponse<RiskResult>>(
    `/api/storm/risk/${deviceId}${toQuery(params)}`,
  )

export const getInterfaceRisk = (
  deviceId: string,
  interfaceName: string,
  params: PaginationParams & { history?: boolean } = {},
) => {
  const encoded = encodeURIComponent(interfaceName)
  const search = new URLSearchParams()
  if (params.page) search.set('page', String(params.page))
  if (params.limit) search.set('limit', String(params.limit))
  if (params.history) search.set('history', 'true')
  const query = search.toString()
  return apiRequest<
    ApiItemResponse<RiskResult> & {
      history?: RiskResult[]
      total?: number
      page?: number
      limit?: number
      totalPages?: number
    }
  >(`/api/storm/risk/${deviceId}/${encoded}${query ? `?${query}` : ''}`)
}

export const calculateInterfaceRisk = (payload: {
  deviceId: string
  interface: string
  eligible?: boolean
}) =>
  apiRequest<ApiItemResponse<RiskResult>>('/api/storm/risk/calculate', {
    method: 'POST',
    body: payload,
  })

export const calculateAllRisk = () =>
  apiRequest<{
    success: boolean
    message: string
    total: number
    scored: number
    skipped: number
    errors: number
    disabled: boolean
  }>('/api/storm/risk/calculate-all', {
    method: 'POST',
    timeoutMs: 300000,
  })

export const getConfirmationResults = (params: PaginationParams = {}) =>
  apiRequest<ApiListResponse<ConfirmationResult>>(
    `/api/storm/confirmation${toQuery(params)}`,
  )

export const getDeviceConfirmation = (
  deviceId: string,
  params: PaginationParams = {},
) =>
  apiRequest<ApiListResponse<ConfirmationResult>>(
    `/api/storm/confirmation/${deviceId}${toQuery(params)}`,
  )

export const getInterfaceConfirmation = (
  deviceId: string,
  interfaceName: string,
  params: PaginationParams & { history?: boolean } = {},
) => {
  const encoded = encodeURIComponent(interfaceName)
  const search = new URLSearchParams()
  if (params.page) search.set('page', String(params.page))
  if (params.limit) search.set('limit', String(params.limit))
  if (params.history) search.set('history', 'true')
  const query = search.toString()
  return apiRequest<
    ApiItemResponse<ConfirmationResult> & {
      history?: ConfirmationResult[]
      total?: number
      page?: number
      limit?: number
      totalPages?: number
    }
  >(`/api/storm/confirmation/${deviceId}/${encoded}${query ? `?${query}` : ''}`)
}

export const evaluateAllConfirmation = () =>
  apiRequest<{
    success: boolean
    message: string
    total: number
    confirmed: number
    pending: number
    notConfirmed: number
    errors: number
    disabled: boolean
  }>('/api/storm/confirmation/evaluate-all', {
    method: 'POST',
    timeoutMs: 300000,
  })

export const getSafetyResults = (params: PaginationParams = {}) =>
  apiRequest<ApiListResponse<SafetyResult>>(`/api/storm/safety${toQuery(params)}`)

export const evaluateAllSafety = (payload: { probeSsh?: boolean } = {}) =>
  apiRequest<{
    success: boolean
    message: string
    total: number
    safe: number
    unsafe: number
    waiting: number
    errors: number
    disabled: boolean
  }>('/api/storm/safety/evaluate-all', {
    method: 'POST',
    body: payload,
    timeoutMs: 300000,
  })

export const getStormIncidents = (params: PaginationParams = {}) =>
  apiRequest<ApiListResponse<StormIncident>>(`/api/storm/incidents${toQuery(params)}`)

export const getStormIncident = (incidentId: string) =>
  apiRequest<ApiItemResponse<StormIncident>>(
    `/api/storm/incidents/${encodeURIComponent(incidentId)}`,
  )

export const prepareStormMitigation = (payload: {
  deviceId: string
  interface: string
  probeSsh?: boolean
}) =>
  apiRequest<ApiItemResponse<PrepareResult>>('/api/storm/orchestrator/prepare', {
    method: 'POST',
    body: payload,
    timeoutMs: 180000,
  })

export const prepareAllStormMitigation = (payload: { probeSsh?: boolean } = {}) =>
  apiRequest<{
    success: boolean
    message: string
    total: number
    ready: number
    blocked: number
    errors: number
  }>('/api/storm/orchestrator/prepare-all', {
    method: 'POST',
    body: payload,
    timeoutMs: 300000,
  })

export const getMitigationHistory = (params: PaginationParams = {}) =>
  apiRequest<ApiListResponse<MitigationLog>>(`/api/storm/mitigation/history${toQuery(params)}`)

export const getMitigationHistoryDetail = (incidentId: string) =>
  apiRequest<{ success: boolean; count: number; data: MitigationLog[] }>(
    `/api/storm/mitigation/history/${encodeURIComponent(incidentId)}`,
  )

export const executeStormMitigation = (payload: { incidentId: string; strategy?: string }) =>
  apiRequest<{ success: boolean; status: string; incidentId: string; error?: string }>(
    '/api/storm/mitigation/execute',
    {
      method: 'POST',
      body: payload,
      timeoutMs: 180000,
    },
  )

export const rollbackStormMitigation = (payload: { incidentId: string }) =>
  apiRequest<{ success: boolean; status: string; incidentId: string; error?: string }>(
    '/api/storm/mitigation/rollback',
    {
      method: 'POST',
      body: payload,
      timeoutMs: 180000,
    },
  )

export const getRecoveryHistory = (params: PaginationParams = {}) =>
  apiRequest<ApiListResponse<RecoveryLog>>(`/api/storm/recovery/history${toQuery(params)}`)

export const getRecoveryHistoryDetail = (incidentId: string) =>
  apiRequest<{ success: boolean; count: number; data: RecoveryLog[] }>(
    `/api/storm/recovery/history/${encodeURIComponent(incidentId)}`,
  )

export const executeStormRecovery = (payload: { incidentId: string; force?: boolean }) =>
  apiRequest<{ success: boolean; status: string; incidentId: string; error?: string }>(
    '/api/storm/recovery/execute',
    {
      method: 'POST',
      body: payload,
      timeoutMs: 180000,
    },
  )

export const retryStormRecovery = (payload: { incidentId: string }) =>
  apiRequest<{ success: boolean; status: string; incidentId: string; error?: string }>(
    '/api/storm/recovery/retry',
    {
      method: 'POST',
      body: payload,
      timeoutMs: 180000,
    },
  )

// ── Custom Networks API ──────────────────────────────────────────────────────

import type { NetworkProfile } from '@/types'

export const getNetworks = () =>
  apiRequest<{ success: boolean; data: NetworkProfile[] }>('/api/networks')

export const createNetwork = (payload: Partial<NetworkProfile>) =>
  apiRequest<{ success: boolean; message: string; data: NetworkProfile }>('/api/networks', {
    method: 'POST',
    body: payload,
  })

export const updateNetwork = (id: string, payload: Partial<NetworkProfile>) =>
  apiRequest<{ success: boolean; message: string; data: NetworkProfile }>(`/api/networks/${id}`, {
    method: 'PUT',
    body: payload,
  })

export const deleteNetwork = (id: string) =>
  apiRequest<{ success: boolean; message: string }>(`/api/networks/${id}`, {
    method: 'DELETE',
  })

export const scanNetworks = (payload: {
  networkIds?: string[]
  scanAllEnabled?: boolean
}) =>
  apiRequest<{
    success: boolean
    scanId: string
    status: string
    message: string
  }>('/api/discovery/scan-networks', {
    method: 'POST',
    body: payload,
    timeoutMs: 30000,
  })

export const discoverDevice = (ipAddress: string) =>
  apiRequest<DiscoveryResult>('/api/discovery/discover-device', {
    method: 'POST',
    body: { ipAddress },
    timeoutMs: 60000,
  })

export const getDiscoveryScanProgress = (scanId: string) =>
  apiRequest<{
    success: boolean
    progress: {
      scanId: string
      status: 'pending' | 'running' | 'complete' | 'failed'
      total: number
      completed: number
      online: number
      newlySaved: number
      elapsedSeconds: number
      percent: number
      error?: string | null
      summary?: {
        totalScanned: number
        online: number
        offline: number
        newlySaved: number
        newlySavedIps?: string[]
      }
      devices?: DiscoveryDevice[]
    }
  }>(`/api/discovery/scan-progress/${scanId}`, {
    timeoutMs: 15000,
  })

export const getDiscoveryEnrichmentStatus = (ipAddresses: string[]) =>
  apiRequest<{
    success: boolean
    devices: Array<
      Pick<
        DiscoveryDevice,
        | 'ipAddress'
        | 'hostname'
        | 'deviceType'
        | 'vendor'
        | 'operatingSystem'
        | 'classificationConfidence'
        | 'classificationMethod'
        | 'discoveryStatus'
      > & { discoveryEnrichmentError?: string | null }
    >
  }>('/api/discovery/enrichment-status', {
    method: 'POST',
    body: { ipAddresses },
  })

