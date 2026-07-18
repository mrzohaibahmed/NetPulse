import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  acknowledgeAlert,
  createDevice,
  deleteDevice,
  dismissAlert,
  getAlerts,
  getDashboardStatistics,
  getDashboardSummary,
  getDeviceHistory,
  getDeviceStatus,
  getDeviceStatusChart,
  getDeviceTypeChart,
  getDevices,
  getHealth,
  getHistory,
  getNetworkHint,
  getRecentHistory,
  getResponseTimeChart,
  getScanActivityChart,
  getSettings,
  getUptimeReport,
  getUsers,
  importDevicesCsv,
  scanDevice,
  scanDeviceNmap,
  updateDevice,
  updateSettings,
  updateAccount,
  updateUser,
  discoverRange,
  exportDevicesReport,
  exportHistoryReport,
} from '@/api'
import { queryKeys } from '@/hooks/queryKeys'
import type { DevicePayload, PaginationParams } from '@/types'
import { toast } from 'sonner'

const DASHBOARD_INTERVAL = 10_000
const DEVICES_INTERVAL = 15_000
const HISTORY_INTERVAL = 20_000
const HEALTH_INTERVAL = 15_000

export function useHealthQuery() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: getHealth,
    refetchInterval: HEALTH_INTERVAL,
    retry: 1,
  })
}

export function useDashboardQuery() {
  const summary = useQuery({
    queryKey: queryKeys.dashboard.summary,
    queryFn: async () => (await getDashboardSummary()).summary,
    refetchInterval: DASHBOARD_INTERVAL,
  })
  const statistics = useQuery({
    queryKey: queryKeys.dashboard.statistics,
    queryFn: async () => (await getDashboardStatistics()).statistics,
    refetchInterval: DASHBOARD_INTERVAL,
  })
  const statusChart = useQuery({
    queryKey: queryKeys.dashboard.statusChart,
    queryFn: async () => (await getDeviceStatusChart()).chart,
    refetchInterval: DASHBOARD_INTERVAL,
  })
  const typeChart = useQuery({
    queryKey: queryKeys.dashboard.typeChart,
    queryFn: async () => (await getDeviceTypeChart()).chart,
    refetchInterval: DASHBOARD_INTERVAL,
  })
  const responseTime = useQuery({
    queryKey: queryKeys.dashboard.responseTime,
    queryFn: async () => (await getResponseTimeChart()).chart,
    refetchInterval: DASHBOARD_INTERVAL,
  })
  const scanActivity = useQuery({
    queryKey: queryKeys.dashboard.scanActivity,
    queryFn: async () => (await getScanActivityChart()).chart,
    refetchInterval: DASHBOARD_INTERVAL,
  })
  const devices = useQuery({
    queryKey: queryKeys.dashboard.deviceStatus,
    queryFn: async () => (await getDeviceStatus()).devices,
    refetchInterval: DASHBOARD_INTERVAL,
  })
  const history = useQuery({
    queryKey: queryKeys.dashboard.recentHistory,
    queryFn: async () => (await getRecentHistory()).history,
    refetchInterval: DASHBOARD_INTERVAL,
  })
  const alerts = useQuery({
    queryKey: queryKeys.alerts('active'),
    queryFn: async () => (await getAlerts({ status: 'active', limit: 10 })).data,
    refetchInterval: DASHBOARD_INTERVAL,
  })

  const isLoading =
    summary.isLoading ||
    statistics.isLoading ||
    statusChart.isLoading ||
    typeChart.isLoading ||
    devices.isLoading

  const error =
    summary.error ||
    statistics.error ||
    statusChart.error ||
    typeChart.error ||
    responseTime.error ||
    scanActivity.error ||
    devices.error ||
    history.error ||
    alerts.error

  const refetchAll = async () => {
    await Promise.all([
      summary.refetch(),
      statistics.refetch(),
      statusChart.refetch(),
      typeChart.refetch(),
      responseTime.refetch(),
      scanActivity.refetch(),
      devices.refetch(),
      history.refetch(),
      alerts.refetch(),
    ])
  }

  const dataUpdatedAt = Math.max(
    summary.dataUpdatedAt,
    statistics.dataUpdatedAt,
    devices.dataUpdatedAt,
    alerts.dataUpdatedAt,
  )

  return {
    summary: summary.data ?? null,
    statistics: statistics.data ?? null,
    statusChart: statusChart.data ?? [],
    typeChart: typeChart.data ?? [],
    responseTime: responseTime.data ?? [],
    scanActivity: scanActivity.data ?? [],
    devices: devices.data ?? [],
    history: history.data ?? [],
    alerts: alerts.data ?? [],
    isLoading,
    error: error instanceof Error ? error.message : error ? String(error) : null,
    refetchAll,
    dataUpdatedAt,
  }
}

export function useAlertsQuery(status = 'active', limit = 50) {
  return useQuery({
    queryKey: [...queryKeys.alerts(status), limit],
    queryFn: async () => getAlerts({ status, limit }),
    refetchInterval: DASHBOARD_INTERVAL,
  })
}

export function useDevicesQuery(params: PaginationParams) {
  return useQuery({
    queryKey: queryKeys.devices(params),
    queryFn: () => getDevices(params),
    refetchInterval: DEVICES_INTERVAL,
    placeholderData: (prev) => prev,
  })
}

export function useDeviceHistoryQuery(
  deviceId: string,
  params: { startDate?: string; endDate?: string; limit?: number } = {},
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.deviceHistory(deviceId, params),
    queryFn: () => getDeviceHistory(deviceId, { limit: 300, ...params }),
    enabled: Boolean(deviceId) && enabled,
  })
}

export function useHistoryQuery(params: PaginationParams) {
  return useQuery({
    queryKey: queryKeys.history(params),
    queryFn: () => getHistory(params),
    refetchInterval: HISTORY_INTERVAL,
    placeholderData: (prev) => prev,
  })
}

export function useSettingsQuery(enabled = true) {
  return useQuery({
    queryKey: queryKeys.settings,
    queryFn: async () => (await getSettings()).data,
    enabled,
  })
}

export function useUsersQuery(enabled = true) {
  return useQuery({
    queryKey: queryKeys.users,
    queryFn: async () => (await getUsers()).data,
    enabled,
  })
}

export function useNetworkHintQuery(enabled = true) {
  return useQuery({
    queryKey: queryKeys.networkHint,
    queryFn: async () => (await getNetworkHint()).hint,
    enabled,
    retry: false,
  })
}

export function useUptimeReportQuery(params: PaginationParams, enabled = false) {
  return useQuery({
    queryKey: queryKeys.uptimeReport(params),
    queryFn: async () => (await getUptimeReport(params)).data,
    enabled,
  })
}

export function useAlertMutations() {
  const qc = useQueryClient()
  const invalidate = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: ['alerts'] }),
      qc.invalidateQueries({ queryKey: queryKeys.dashboard.all }),
    ])

  const acknowledge = useMutation({
    mutationFn: (id: string) => acknowledgeAlert(id),
    onSuccess: async () => {
      toast.success('Alert acknowledged')
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const dismiss = useMutation({
    mutationFn: (id: string) => dismissAlert(id),
    onSuccess: async () => {
      toast.success('Alert dismissed')
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  return { acknowledge, dismiss }
}

export function useDeviceMutations() {
  const qc = useQueryClient()
  const invalidate = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: ['devices'] }),
      qc.invalidateQueries({ queryKey: queryKeys.dashboard.all }),
    ])

  const create = useMutation({
    mutationFn: (payload: DevicePayload) => createDevice(payload),
    onSuccess: async (_, vars) => {
      toast.success(`Created ${vars.hostname}`)
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const update = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Partial<DevicePayload> }) =>
      updateDevice(id, payload),
    onSuccess: async () => {
      toast.success('Device updated')
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const remove = useMutation({
    mutationFn: ({ id }: { id: string; hostname: string }) => deleteDevice(id),
    onSuccess: async (_, vars) => {
      toast.success(`Deleted ${vars.hostname}`)
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const scan = useMutation({
    mutationFn: (id: string) => scanDevice(id),
    onSuccess: async (res) => {
      toast.success(res.message ?? 'Ping complete')
      await invalidate()
      await qc.invalidateQueries({ queryKey: ['device-history'] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const importCsv = useMutation({
    mutationFn: (file: File) => importDevicesCsv(file),
    onSuccess: async (res) => {
      toast.success(res.message)
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  return { create, update, remove, scan, importCsv }
}

export function useNmapScanMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => scanDeviceNmap(id),
    onSuccess: async (res) => {
      toast.success(res.message ?? 'Nmap scan complete')
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['devices'] }),
        qc.invalidateQueries({ queryKey: ['device-history'] }),
        qc.invalidateQueries({ queryKey: queryKeys.dashboard.all }),
      ])
    },
    onError: (err: Error) => toast.error(err.message),
  })
}


export function useSettingsMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: Record<string, unknown>) => updateSettings(payload),
    onSuccess: async (res) => {
      toast.success(res.message || 'Settings saved')
      await qc.invalidateQueries({ queryKey: queryKeys.settings })
    },
    onError: (err: Error) => toast.error(err.message),
  })
}

export function useAccountMutation() {
  return useMutation({
    mutationFn: (payload: { currentPassword: string; username?: string; newPassword?: string }) =>
      updateAccount(payload),
    onError: (err: Error) => toast.error(err.message),
  })
}

export function useUserMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: { username?: string; password?: string } }) =>
      updateUser(id, payload),
    onSuccess: async (res) => {
      toast.success(res.message)
      await qc.invalidateQueries({ queryKey: queryKeys.users })
    },
    onError: (err: Error) => toast.error(err.message),
  })
}

export function useDiscoveryMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ startIP, endIP }: { startIP: string; endIP: string }) =>
      discoverRange(startIP, endIP),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['devices'] })
      await qc.invalidateQueries({ queryKey: queryKeys.dashboard.all })
    },
    onError: (err: Error) => toast.error(err.message),
  })
}

export function useExportReports() {
  return {
    exportDevices: async (params: PaginationParams & { format?: string }) => {
      try {
        await exportDevicesReport(params)
        toast.success('Devices export started')
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Export failed')
      }
    },
    exportHistory: async (params: PaginationParams & { format?: string }) => {
      try {
        await exportHistoryReport(params)
        toast.success('History export started')
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Export failed')
      }
    },
  }
}
