import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  acknowledgeAlert,
  collectAllInterfaceStats,
  collectDeviceInterfaceStats,
  createDevice,
  deleteDevice,
  dismissAlert,
  discoverAllInterfaces,
  discoverDeviceInterfaces,
  evaluateAllEligibility,
  calculateAllRisk,
  evaluateAllConfirmation,
  evaluateAllSafety,
  getAlerts,
  getConfirmationResults,
  getDashboardStatistics,
  getDashboardSummary,
  getDeviceHistory,
  getDeviceInterfaceStats,
  getDeviceInterfaces,
  getDeviceStatus,
  getDeviceStatusChart,
  getDeviceTypeChart,
  getDevices,
  getEligibilityResults,
  getHealth,
  getHistory,
  getInterfaceHistory,
  getInterfaceRisk,
  getInterfaces,
  getNetworkHint,
  getRecentHistory,
  getResponseTimeChart,
  getRiskResults,
  getSafetyResults,
  getScanActivityChart,
  getSettings,
  getStormConfig,
  getStormIncidents,
  prepareAllStormMitigation,
  getMitigationHistory,
  getMitigationHistoryDetail,
  executeStormMitigation,
  rollbackStormMitigation,
  getRecoveryHistory,
  getRecoveryHistoryDetail,
  executeStormRecovery,
  retryStormRecovery,
  getUptimeReport,
  getUsers,
  importDevicesCsv,
  manualRecoverInterface,
  manualShutdownInterface,
  setInterfaceMonitoring,
  scanDevice,
  scanDeviceNmap,
  scanAllDevicesNmap,
  updateDevice,
  updateSettings,
  updateAccount,
  updateUser,
  discoverRange,
  exportDevicesReport,
  exportHistoryReport,
} from '@/api'
import { queryKeys } from '@/hooks/queryKeys'
import type { DevicePayload, PaginationParams, UserRole } from '@/types'
import { toast } from 'sonner'

const DASHBOARD_INTERVAL = 10_000
const DEVICES_INTERVAL = 15_000
const HISTORY_INTERVAL = 20_000
const HEALTH_INTERVAL = 15_000
const INTERFACES_INTERVAL = 15_000
const INTERFACE_STATS_INTERVAL = 20_000
const INTERFACE_HISTORY_INTERVAL = 30_000
const ELIGIBILITY_INTERVAL = 20_000

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

export function useInterfacesQuery(params: PaginationParams) {
  return useQuery({
    queryKey: queryKeys.interfaces(params),
    queryFn: () => getInterfaces(params),
    refetchInterval: INTERFACES_INTERVAL,
    placeholderData: (prev) => prev,
  })
}

export function useDeviceInterfacesQuery(deviceId: string, params: PaginationParams = {}, enabled = true) {
  return useQuery({
    queryKey: queryKeys.deviceInterfaces(deviceId, params),
    queryFn: () => getDeviceInterfaces(deviceId, params),
    enabled: Boolean(deviceId) && enabled,
    refetchInterval: INTERFACES_INTERVAL,
    placeholderData: (prev) => prev,
  })
}

export function useDeviceInterfaceStatsQuery(deviceId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.interfaceStats(deviceId),
    queryFn: async () => (await getDeviceInterfaceStats(deviceId)).data,
    enabled: Boolean(deviceId) && enabled,
    refetchInterval: INTERFACE_STATS_INTERVAL,
    placeholderData: (prev) => prev,
  })
}

export function useInterfaceHistoryQuery(
  deviceId: string,
  interfaceName: string,
  params: PaginationParams = {},
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.interfaceHistory(deviceId, interfaceName, params),
    queryFn: () => getInterfaceHistory(deviceId, interfaceName, params),
    enabled: Boolean(deviceId) && Boolean(interfaceName) && enabled,
    refetchInterval: INTERFACE_HISTORY_INTERVAL,
    placeholderData: (prev) => prev,
  })
}

export function useInterfaceMutations() {
  const qc = useQueryClient()
  const invalidate = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: ['interfaces'] }),
      qc.invalidateQueries({ queryKey: ['device-interfaces'] }),
      qc.invalidateQueries({ queryKey: ['interface-stats'] }),
      qc.invalidateQueries({ queryKey: ['interface-history'] }),
      qc.invalidateQueries({ queryKey: ['storm-incidents'] }),
      qc.invalidateQueries({ queryKey: ['mitigation-history'] }),
      qc.invalidateQueries({ queryKey: ['recovery-history'] }),
    ])

  const discoverAll = useMutation({
    mutationFn: () => discoverAllInterfaces(),
    onSuccess: async (res) => {
      if ((res.discoveredDevices ?? 0) === 0 && (res.failed ?? 0) > 0) {
        const detail = res.errors?.[0]?.error
        toast.error(
          detail
            ? `Discovery failed: ${detail}`
            : res.message || 'Interface discovery failed for all devices',
        )
      } else if ((res.failed ?? 0) > 0) {
        toast.warning(
          `${res.message || 'Discovery finished'} (${res.failed} failed)`,
        )
      } else {
        toast.success(res.message || 'Interface discovery complete')
      }
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const discoverDevice = useMutation({
    mutationFn: (deviceId: string) => discoverDeviceInterfaces(deviceId),
    onSuccess: async (res) => {
      toast.success(res.message || 'Interface discovery complete')
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const collectAll = useMutation({
    mutationFn: () => collectAllInterfaceStats(),
    onSuccess: async (res) => {
      if ((res.succeeded ?? 0) === 0 && (res.failed ?? 0) > 0) {
        const detail = res.errors?.[0]?.error
        toast.error(
          detail
            ? `Stats collection failed: ${detail}`
            : res.message || 'Stats collection failed for all devices',
        )
      } else if ((res.failed ?? 0) > 0) {
        toast.warning(
          `${res.message || 'Stats collection finished'} (${res.failed} failed)`,
        )
      } else {
        toast.success(res.message || 'Stats collection complete')
      }
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const collectDevice = useMutation({
    mutationFn: (deviceId: string) => collectDeviceInterfaceStats(deviceId),
    onSuccess: async (res) => {
      toast.success(res.message || 'Stats collection complete')
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const manualShutdown = useMutation({
    mutationFn: (payload: {
      deviceId: string
      interfaceName: string
      confirm: boolean
      reason?: string
    }) => manualShutdownInterface(payload),
    onSuccess: async (res) => {
      toast.success(res.message || 'Manual shutdown complete')
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const manualRecover = useMutation({
    mutationFn: (payload: {
      deviceId: string
      interfaceName: string
      confirm: boolean
      incidentId?: string
    }) => manualRecoverInterface(payload),
    onSuccess: async (res) => {
      toast.success(res.message || 'Manual recovery complete')
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const setMonitoring = useMutation({
    mutationFn: (payload: {
      deviceId: string
      interfaceName: string
      enabled: boolean
    }) => setInterfaceMonitoring(payload),
    onSuccess: async (res) => {
      toast.success(res.message || 'Monitoring preference updated')
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  return {
    discoverAll,
    discoverDevice,
    collectAll,
    collectDevice,
    manualShutdown,
    manualRecover,
    setMonitoring,
  }
}

export function useEligibilityQuery(params: PaginationParams) {
  return useQuery({
    queryKey: queryKeys.eligibility(params),
    queryFn: () => getEligibilityResults(params),
    refetchInterval: ELIGIBILITY_INTERVAL,
    placeholderData: (prev) => prev,
  })
}

export function useStormConfigQuery(enabled = true) {
  return useQuery({
    queryKey: queryKeys.stormConfig,
    queryFn: async () => (await getStormConfig()).data,
    enabled,
  })
}

export function useEligibilityMutations() {
  const qc = useQueryClient()
  const invalidate = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: ['eligibility'] }),
      qc.invalidateQueries({ queryKey: ['device-eligibility'] }),
      qc.invalidateQueries({ queryKey: queryKeys.stormConfig }),
    ])

  const evaluateAll = useMutation({
    mutationFn: () => evaluateAllEligibility(),
    onSuccess: async (res) => {
      if (res.skipped) {
        toast.warning(res.message || 'Eligibility evaluation is disabled')
      } else if ((res.errors ?? 0) > 0) {
        toast.warning(
          `${res.message || 'Eligibility finished'} (${res.errors} error(s))`,
        )
      } else {
        toast.success(res.message || 'Eligibility evaluation complete')
      }
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  return { evaluateAll }
}

export function useRiskQuery(params: PaginationParams) {
  return useQuery({
    queryKey: queryKeys.risk(params),
    queryFn: () => getRiskResults(params),
    refetchInterval: ELIGIBILITY_INTERVAL,
    placeholderData: (prev) => prev,
  })
}

export function useInterfaceRiskQuery(
  deviceId: string,
  interfaceName: string,
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.interfaceRisk(deviceId, interfaceName, { history: true }),
    queryFn: () =>
      getInterfaceRisk(deviceId, interfaceName, { history: true, limit: 40 }),
    enabled: Boolean(deviceId) && Boolean(interfaceName) && enabled,
    refetchInterval: ELIGIBILITY_INTERVAL,
  })
}

export function useRiskMutations() {
  const qc = useQueryClient()
  const invalidate = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: ['risk'] }),
      qc.invalidateQueries({ queryKey: ['interface-risk'] }),
    ])

  const calculateAll = useMutation({
    mutationFn: () => calculateAllRisk(),
    onSuccess: async (res) => {
      if (res.disabled) {
        toast.warning(res.message || 'Risk scoring is disabled')
      } else if ((res.errors ?? 0) > 0) {
        toast.warning(
          `${res.message || 'Risk scoring finished'} (${res.errors} error(s))`,
        )
      } else {
        toast.success(res.message || 'Risk scoring complete')
      }
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  return { calculateAll }
}

export function useConfirmationQuery(params: PaginationParams) {
  return useQuery({
    queryKey: queryKeys.confirmation(params),
    queryFn: () => getConfirmationResults(params),
    refetchInterval: ELIGIBILITY_INTERVAL,
    placeholderData: (prev) => prev,
  })
}

export function useConfirmationMutations() {
  const qc = useQueryClient()
  const invalidate = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: ['confirmation'] }),
    ])

  const evaluateAll = useMutation({
    mutationFn: () => evaluateAllConfirmation(),
    onSuccess: async (res) => {
      if (res.disabled) {
        toast.warning(res.message || 'Confirmation is disabled')
      } else if ((res.errors ?? 0) > 0) {
        toast.warning(
          `${res.message || 'Confirmation finished'} (${res.errors} error(s))`,
        )
      } else {
        toast.success(res.message || 'Confirmation evaluation complete')
      }
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  return { evaluateAll }
}

export function useSafetyQuery(params: PaginationParams) {
  return useQuery({
    queryKey: queryKeys.safety(params),
    queryFn: () => getSafetyResults(params),
    refetchInterval: ELIGIBILITY_INTERVAL,
    placeholderData: (prev) => prev,
  })
}

export function useSafetyMutations() {
  const qc = useQueryClient()
  const invalidate = () =>
    Promise.all([qc.invalidateQueries({ queryKey: ['safety'] })])

  const evaluateAll = useMutation({
    mutationFn: () => evaluateAllSafety({ probeSsh: true }),
    onSuccess: async (res) => {
      if (res.disabled) {
        toast.warning(res.message || 'Safety evaluation is disabled')
      } else if ((res.errors ?? 0) > 0) {
        toast.warning(
          `${res.message || 'Safety finished'} (${res.errors} error(s))`,
        )
      } else {
        toast.success(res.message || 'Safety evaluation complete')
      }
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  return { evaluateAll }
}

export function useStormIncidentsQuery(params: PaginationParams) {
  return useQuery({
    queryKey: queryKeys.incidents(params),
    queryFn: () => getStormIncidents(params),
    refetchInterval: ELIGIBILITY_INTERVAL,
    placeholderData: (prev) => prev,
  })
}

export function useOrchestratorMutations() {
  const qc = useQueryClient()
  const invalidate = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: ['storm-incidents'] }),
      qc.invalidateQueries({ queryKey: ['safety'] }),
    ])

  const prepareAll = useMutation({
    mutationFn: () => prepareAllStormMitigation({ probeSsh: true }),
    onSuccess: async (res) => {
      if ((res.errors ?? 0) > 0) {
        toast.warning(
          `${res.message || 'Prepare finished'} (${res.errors} error(s))`,
        )
      } else {
        toast.success(res.message || 'Mitigation preparation complete')
      }
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  return { prepareAll }
}

export function useMitigationHistoryQuery(params: PaginationParams) {
  return useQuery({
    queryKey: queryKeys.mitigationHistory(params),
    queryFn: () => getMitigationHistory(params),
    refetchInterval: 10_000,
    placeholderData: (prev) => prev,
  })
}

export function useMitigationDetailQuery(incidentId: string, enabled = false) {
  return useQuery({
    queryKey: queryKeys.mitigationDetail(incidentId),
    queryFn: () => getMitigationHistoryDetail(incidentId),
    enabled: enabled && !!incidentId,
  })
}

export function useMitigationMutations() {
  const qc = useQueryClient()
  const invalidate = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: ['storm-incidents'] }),
      qc.invalidateQueries({ queryKey: ['mitigation-history'] }),
      qc.invalidateQueries({ queryKey: ['safety'] }),
    ])

  const execute = useMutation({
    mutationFn: (payload: { incidentId: string; strategy?: string }) =>
      executeStormMitigation(payload),
    onSuccess: async (res) => {
      if (res.success) {
        toast.success(`Mitigation completed successfully!`)
      } else {
        toast.error(`Mitigation failed: ${res.error || 'Check history'}`)
      }
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const rollback = useMutation({
    mutationFn: (payload: { incidentId: string }) =>
      rollbackStormMitigation(payload),
    onSuccess: async (res) => {
      if (res.success) {
        toast.success(`Rollback completed successfully!`)
      } else {
        toast.error(`Rollback failed: ${res.error || 'Check history'}`)
      }
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  return { execute, rollback }
}

export function useRecoveryHistoryQuery(params: PaginationParams) {
  return useQuery({
    queryKey: queryKeys.recoveryHistory(params),
    queryFn: () => getRecoveryHistory(params),
    refetchInterval: 10_000,
    placeholderData: (prev) => prev,
  })
}

export function useRecoveryDetailQuery(incidentId: string, enabled = false) {
  return useQuery({
    queryKey: queryKeys.recoveryDetail(incidentId),
    queryFn: () => getRecoveryHistoryDetail(incidentId),
    enabled: enabled && !!incidentId,
  })
}

export function useRecoveryMutations() {
  const qc = useQueryClient()
  const invalidate = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: ['storm-incidents'] }),
      qc.invalidateQueries({ queryKey: ['recovery-history'] }),
      qc.invalidateQueries({ queryKey: ['safety'] }),
    ])

  const execute = useMutation({
    mutationFn: (payload: { incidentId: string; force?: boolean }) =>
      executeStormRecovery(payload),
    onSuccess: async (res) => {
      if (res.success) {
        toast.success(`Recovery triggered successfully!`)
      } else {
        toast.error(`Recovery failed: ${res.error || 'Check history'}`)
      }
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const retry = useMutation({
    mutationFn: (payload: { incidentId: string }) =>
      retryStormRecovery(payload),
    onSuccess: async (res) => {
      if (res.success) {
        toast.success(`Recovery retry triggered successfully!`)
      } else {
        toast.error(`Retry failed: ${res.error || 'Check history'}`)
      }
      await invalidate()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  return { execute, retry }
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

export function useNmapScanAllMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => scanAllDevicesNmap(),
    onSuccess: async (res) => {
      const failed = res.failed ?? 0
      if (failed > 0) {
        toast.warning(
          res.message ??
            `Nmap bulk scan finished: ${res.scanned}/${res.total} scanned, ${failed} failed`,
        )
      } else {
        toast.success(res.message ?? 'Nmap bulk scan complete')
      }
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
    mutationFn: ({
      id,
      payload,
    }: {
      id: string
      payload: { username?: string; password?: string; role?: UserRole }
    }) => updateUser(id, payload),
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
