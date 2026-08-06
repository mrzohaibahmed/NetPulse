import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Network, Shield } from 'lucide-react'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { TableSkeleton } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { useAuth } from '@/shared/auth/AuthContext'
import { getStormIncident } from '@/api'
import { useClientPagination } from '@/hooks/useClientPagination'
import {
  useConfirmationMutations,
  useConfirmationQuery,
  useDevicesQuery,
  useEligibilityMutations,
  useEligibilityQuery,
  useInterfaceRiskQuery,
  useOrchestratorMutations,
  useRiskMutations,
  useRiskQuery,
  useSafetyMutations,
  useSafetyQuery,
  useStormConfigQuery,
  useStormIncidentsQuery,
  useMitigationHistoryQuery,
  useMitigationMutations,
  useRecoveryHistoryQuery,
  useRecoveryMutations,
  useSettingsQuery,
  useSettingsMutation,
} from '@/hooks/queries'
import type {
  MitigationLog,
  RecoveryLog,
  RiskResult,
  SafetyResult,
  StormIncident,
} from '@/types'
import { formatDateTime, formatRelative } from '@/utils/format'
import { StormOverview } from '@/modules/storm/components/StormOverview'
import { StormQuickActions } from '@/modules/storm/components/StormQuickActions'
import { StormSwitchCard } from '@/modules/storm/components/StormSwitchCard'
import {
  COLLAPSE_KEY,
  DEFAULT_SWITCHES_PER_PAGE,
  exportIncident,
  FETCH_LIMIT,
  isManagedSwitch,
  scrollToStormSection,
  summarizeSwitch,
  type SwitchStormSectionData,
} from '@/modules/storm/components/stormShared'

function matchesText(haystack: Array<string | null | undefined>, needle: string): boolean {
  if (!needle) return true
  const q = needle.toLowerCase()
  return haystack.some((part) => String(part || '').toLowerCase().includes(q))
}

export function StormProtectionPage() {
  const { isAdmin } = useAuth()
  const [searchParams] = useSearchParams()
  const eligibilityMutations = useEligibilityMutations()
  const riskMutations = useRiskMutations()
  const confirmationMutations = useConfirmationMutations()
  const safetyMutations = useSafetyMutations()
  const orchestratorMutations = useOrchestratorMutations()
  const stormConfig = useStormConfigQuery()
  const settingsQuery = useSettingsQuery()
  const settingsMutation = useSettingsMutation()
  const mitigationMutations = useMitigationMutations()
  const recoveryMutations = useRecoveryMutations()

  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [switchFilter, setSwitchFilter] = useState('all')
  const [severityFilter, setSeverityFilter] = useState('all')
  const [eligibleFilter, setEligibleFilter] = useState('all')
  const [confirmStateFilter, setConfirmStateFilter] = useState('all')
  const [safetyStatusFilter, setSafetyStatusFilter] = useState('all')
  const [incidentStatusFilter, setIncidentStatusFilter] = useState('all')

  const [selectedRisk, setSelectedRisk] = useState<RiskResult | null>(null)
  const [selectedSafety, setSelectedSafety] = useState<SafetyResult | null>(null)
  const [selectedIncident, setSelectedIncident] = useState<StormIncident | null>(null)
  const [selectedMitigation, setSelectedMitigation] = useState<MitigationLog | null>(null)
  const [selectedRecovery, setSelectedRecovery] = useState<RecoveryLog | null>(null)
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({})

  const [collapsedDeviceIds, setCollapsedDeviceIds] = useState<Set<string>>(() => {
    try {
      const raw = sessionStorage.getItem(COLLAPSE_KEY)
      if (!raw) return new Set<string>()
      const parsed = JSON.parse(raw)
      return Array.isArray(parsed)
        ? new Set(parsed.filter((v) => typeof v === 'string'))
        : new Set<string>()
    } catch {
      return new Set<string>()
    }
  })

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 300)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => {
    sessionStorage.setItem(COLLAPSE_KEY, JSON.stringify([...collapsedDeviceIds]))
  }, [collapsedDeviceIds])

  const viewParam = (searchParams.get('view') || '').trim().toLowerCase()

  const devicesQuery = useDevicesQuery({ page: 1, limit: FETCH_LIMIT })
  const devices = devicesQuery.data?.data ?? []
  const switchDevices = useMemo(
    () => devices.filter((device) => isManagedSwitch(device.deviceType)),
    [devices],
  )

  const eligibilityQuery = useEligibilityQuery({ page: 1, limit: FETCH_LIMIT })
  const riskListQuery = useRiskQuery({ page: 1, limit: FETCH_LIMIT })
  const confirmationQuery = useConfirmationQuery({ page: 1, limit: FETCH_LIMIT })
  const safetyListQuery = useSafetyQuery({ page: 1, limit: FETCH_LIMIT })
  const incidentsQuery = useStormIncidentsQuery({ page: 1, limit: FETCH_LIMIT })
  const mitigationHistoryQuery = useMitigationHistoryQuery({ page: 1, limit: FETCH_LIMIT })
  const recoveryHistoryQuery = useRecoveryHistoryQuery({ page: 1, limit: FETCH_LIMIT })

  const selectedHistoryQuery = useInterfaceRiskQuery(
    selectedRisk?.deviceId || '',
    selectedRisk?.interface || '',
    Boolean(selectedRisk),
  )

  const requiredConfirmations =
    stormConfig.data?.confirmation?.requiredConfirmations ?? 2

  const eligibilityRows = eligibilityQuery.data?.data ?? []
  const riskRows = riskListQuery.data?.data ?? []
  const confirmRows = confirmationQuery.data?.data ?? []
  const safetyRows = safetyListQuery.data?.data ?? []
  const incidentRows = incidentsQuery.data?.data ?? []
  const mitigationRows = mitigationHistoryQuery.data?.data ?? []
  const recoveryRows = recoveryHistoryQuery.data?.data ?? []

  // Deep-link from Alerts: /storm?incident=<incidentId>
  useEffect(() => {
    const incidentParam = (searchParams.get('incident') || '').trim()
    if (!incidentParam) return
    if (selectedIncident?.incidentId === incidentParam) return

    const fromList = incidentRows.find((row) => row.incidentId === incidentParam)
    if (fromList) {
      setSelectedIncident(fromList)
      setExpandedSections({})
      setCollapsedDeviceIds((prev) => {
        if (!fromList.deviceId || !prev.has(fromList.deviceId)) return prev
        const next = new Set(prev)
        next.delete(fromList.deviceId)
        return next
      })
      if (fromList.deviceId) setSwitchFilter(fromList.deviceId)
      return
    }

    let cancelled = false
    void getStormIncident(incidentParam)
      .then((res) => {
        if (cancelled || !res?.data) return
        setSelectedIncident(res.data)
        setExpandedSections({})
        if (res.data.deviceId) {
          setSwitchFilter(res.data.deviceId)
          setCollapsedDeviceIds((prev) => {
            if (!prev.has(res.data.deviceId)) return prev
            const next = new Set(prev)
            next.delete(res.data.deviceId)
            return next
          })
        }
      })
      .catch(() => {
        /* ignore missing incident */
      })
    return () => {
      cancelled = true
    }
  }, [searchParams, incidentRows, selectedIncident?.incidentId])

  const filteredEligibility = useMemo(() => {
    return eligibilityRows.filter((row) => {
      if (eligibleFilter === 'eligible' && !row.eligible) return false
      if (eligibleFilter === 'ineligible' && row.eligible) return false
      return matchesText(
        [row.interface, row.hostname, row.ipAddress, row.reason, row.failedRule, row.deviceId],
        debouncedQuery,
      )
    })
  }, [eligibilityRows, eligibleFilter, debouncedQuery])

  const filteredRisk = useMemo(() => {
    return riskRows.filter((row) => {
      if (
        severityFilter !== 'all' &&
        String(row.severity).toUpperCase() !== severityFilter.toUpperCase()
      ) {
        return false
      }
      return matchesText(
        [
          row.interface,
          row.hostname,
          row.ipAddress,
          row.severity,
          row.sourceClassification,
          row.deviceId,
        ],
        debouncedQuery,
      )
    })
  }, [riskRows, severityFilter, debouncedQuery])

  const filteredConfirm = useMemo(() => {
    return confirmRows.filter((row) => {
      if (
        confirmStateFilter !== 'all' &&
        String(row.state).toUpperCase() !== confirmStateFilter.toUpperCase()
      ) {
        return false
      }
      return matchesText(
        [row.interface, row.hostname, row.ipAddress, row.reason, row.state, row.deviceId],
        debouncedQuery,
      )
    })
  }, [confirmRows, confirmStateFilter, debouncedQuery])

  const filteredSafety = useMemo(() => {
    return safetyRows.filter((row) => {
      if (
        safetyStatusFilter !== 'all' &&
        String(row.status).toUpperCase() !== safetyStatusFilter.toUpperCase()
      ) {
        return false
      }
      return matchesText(
        [row.interface, row.hostname, row.ipAddress, row.reason, row.failedRule, row.deviceId],
        debouncedQuery,
      )
    })
  }, [safetyRows, safetyStatusFilter, debouncedQuery])

  const filteredIncidents = useMemo(() => {
    return incidentRows.filter((row) => {
      if (
        incidentStatusFilter !== 'all' &&
        String(row.status).toUpperCase() !== incidentStatusFilter.toUpperCase()
      ) {
        return false
      }
      return matchesText(
        [
          row.incidentId,
          row.interface,
          row.hostname,
          row.ipAddress,
          row.status,
          row.severity,
          row.deviceId,
        ],
        debouncedQuery,
      )
    })
  }, [incidentRows, incidentStatusFilter, debouncedQuery])

  const filteredMitigation = useMemo(() => {
    return mitigationRows.filter((row) =>
      matchesText(
        [row.incidentId, row.interface, row.deviceId, row.strategy, row.status, row.operator],
        debouncedQuery,
      ),
    )
  }, [mitigationRows, debouncedQuery])

  const filteredRecovery = useMemo(() => {
    return recoveryRows.filter((row) =>
      matchesText(
        [row.incidentId, row.interface, row.deviceId, row.recoveryStatus],
        debouncedQuery,
      ),
    )
  }, [recoveryRows, debouncedQuery])

  const rowFiltersActive =
    Boolean(debouncedQuery.trim()) ||
    severityFilter !== 'all' ||
    eligibleFilter !== 'all' ||
    confirmStateFilter !== 'all' ||
    safetyStatusFilter !== 'all' ||
    incidentStatusFilter !== 'all'

  const hasActiveFilters = rowFiltersActive || switchFilter !== 'all'

  const allGroupedSwitches = useMemo<SwitchStormSectionData[]>(() => {
    const groups = new Map<string, SwitchStormSectionData>()

    for (const device of switchDevices) {
      groups.set(device._id, {
        deviceId: device._id,
        hostname: device.hostname || 'Unknown switch',
        ipAddress: device.ipAddress || '—',
        vendor: device.credentials?.sshVendor || 'Unknown',
        status: device.status || 'Unknown',
        monitor: device.monitor ?? true,
        eligibility: [],
        risk: [],
        confirmation: [],
        safety: [],
        incidents: [],
        mitigation: [],
        recovery: [],
      })
    }

    const ensure = (
      deviceId: string,
      hint?: { hostname?: string | null; ipAddress?: string | null },
    ) => {
      if (!deviceId || groups.has(deviceId)) return groups.get(deviceId)
      const device = switchDevices.find((d) => d._id === deviceId)
      if (!device) return undefined
      const section: SwitchStormSectionData = {
        deviceId,
        hostname: hint?.hostname || device.hostname || 'Unknown switch',
        ipAddress: hint?.ipAddress || device.ipAddress || '—',
        vendor: device.credentials?.sshVendor || 'Unknown',
        status: device.status || 'Unknown',
        monitor: device.monitor ?? true,
        eligibility: [],
        risk: [],
        confirmation: [],
        safety: [],
        incidents: [],
        mitigation: [],
        recovery: [],
      }
      groups.set(deviceId, section)
      return section
    }

    for (const row of filteredEligibility) {
      ensure(row.deviceId, row)?.eligibility.push(row)
    }
    for (const row of filteredRisk) {
      ensure(row.deviceId, row)?.risk.push(row)
    }
    for (const row of filteredConfirm) {
      ensure(row.deviceId, row)?.confirmation.push(row)
    }
    for (const row of filteredSafety) {
      ensure(row.deviceId, row)?.safety.push(row)
    }
    for (const row of filteredIncidents) {
      ensure(row.deviceId, row)?.incidents.push(row)
    }
    for (const row of filteredMitigation) {
      if (row.deviceId) ensure(row.deviceId)?.mitigation.push(row)
    }
    for (const row of filteredRecovery) {
      if (row.deviceId) ensure(row.deviceId)?.recovery.push(row)
    }

    return [...groups.values()].sort((a, b) => a.hostname.localeCompare(b.hostname))
  }, [
    switchDevices,
    filteredEligibility,
    filteredRisk,
    filteredConfirm,
    filteredSafety,
    filteredIncidents,
    filteredMitigation,
    filteredRecovery,
  ])

  const groupedSwitches = useMemo(() => {
    return allGroupedSwitches.filter((section) => {
      if (switchFilter !== 'all' && section.deviceId !== switchFilter) return false
      if (!rowFiltersActive) return true
      return (
        section.eligibility.length > 0 ||
        section.risk.length > 0 ||
        section.confirmation.length > 0 ||
        section.safety.length > 0 ||
        section.incidents.length > 0 ||
        section.mitigation.length > 0 ||
        section.recovery.length > 0
      )
    })
  }, [allGroupedSwitches, switchFilter, rowFiltersActive])

  const switchChips = useMemo(() => {
    return allGroupedSwitches.map((device) => {
      const summary = summarizeSwitch(device)
      return {
        deviceId: device.deviceId,
        hostname: device.hostname,
        ipAddress: device.ipAddress,
        critical: summary.critical,
        confirmed: summary.confirmed,
        openIncidents: summary.openIncidents,
      }
    })
  }, [allGroupedSwitches])

  const switchPagination = useClientPagination(groupedSwitches, DEFAULT_SWITCHES_PER_PAGE)
  const pagedSwitches = switchPagination.pageItems

  useEffect(() => {
    switchPagination.reset()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset when filter set changes
  }, [
    debouncedQuery,
    switchFilter,
    severityFilter,
    eligibleFilter,
    confirmStateFilter,
    safetyStatusFilter,
    incidentStatusFilter,
  ])

  const fleetKpis = useMemo(() => {
    return {
      switches: allGroupedSwitches.length,
      eligible: filteredEligibility.filter((r) => r.eligible).length,
      critical: filteredRisk.filter((r) => String(r.severity).toUpperCase() === 'CRITICAL').length,
      confirmed: filteredConfirm.filter((r) => String(r.state).toUpperCase() === 'CONFIRMED')
        .length,
      safe: filteredSafety.filter((r) => String(r.status).toUpperCase() === 'SAFE').length,
      incidents: filteredIncidents.length,
    }
  }, [
    allGroupedSwitches.length,
    filteredEligibility,
    filteredRisk,
    filteredConfirm,
    filteredSafety,
    filteredIncidents,
  ])

  const trendData = useMemo(() => {
    const history = selectedHistoryQuery.data?.history ?? []
    return [...history].reverse().map((point) => ({
      time: formatDateTime(point.timestamp) || '',
      label: formatRelative(point.timestamp) || '',
      riskScore: point.riskScore,
      severity: point.severity,
    }))
  }, [selectedHistoryQuery.data?.history])

  const isBusy =
    eligibilityMutations.evaluateAll.isPending ||
    riskMutations.calculateAll.isPending ||
    confirmationMutations.evaluateAll.isPending ||
    safetyMutations.evaluateAll.isPending ||
    orchestratorMutations.prepareAll.isPending ||
    mitigationMutations.execute.isPending ||
    mitigationMutations.rollback.isPending ||
    recoveryMutations.execute.isPending ||
    recoveryMutations.retry.isPending

  const refreshAll = () => {
    void devicesQuery.refetch()
    void eligibilityQuery.refetch()
    void riskListQuery.refetch()
    void confirmationQuery.refetch()
    void safetyListQuery.refetch()
    void incidentsQuery.refetch()
    void mitigationHistoryQuery.refetch()
    void recoveryHistoryQuery.refetch()
    if (selectedRisk) void selectedHistoryQuery.refetch()
  }

  const toggleDevice = (deviceId: string) => {
    setCollapsedDeviceIds((prev) => {
      const next = new Set(prev)
      if (next.has(deviceId)) next.delete(deviceId)
      else next.add(deviceId)
      return next
    })
  }

  const toggleSection = (key: string) => {
    setExpandedSections((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  const isLoading =
    devicesQuery.isLoading ||
    eligibilityQuery.isLoading ||
    riskListQuery.isLoading ||
    confirmationQuery.isLoading ||
    safetyListQuery.isLoading ||
    incidentsQuery.isLoading

  const isError =
    devicesQuery.isError ||
    eligibilityQuery.isError ||
    riskListQuery.isError ||
    confirmationQuery.isError ||
    safetyListQuery.isError ||
    incidentsQuery.isError

  const noSwitchesConfigured = !devicesQuery.isLoading && switchDevices.length === 0

  const showSwitchControls =
    !noSwitchesConfigured && !isLoading && !isError && groupedSwitches.length > 0

  // Frontend-only deep links from sidebar: /storm?view=incidents|pipeline|overview
  useEffect(() => {
    const normalized =
      !viewParam || viewParam === 'overview'
        ? 'overview'
        : viewParam === 'incidents' || viewParam === 'pipeline'
          ? viewParam
          : null

    if (!normalized) return

    if (normalized === 'overview') {
      scrollToStormSection('overview-section')
      return
    }

    // Expand switch cards so pipeline / incident subsections are visible.
    setCollapsedDeviceIds((prev) => (prev.size === 0 ? prev : new Set()))

    if (isLoading || noSwitchesConfigured) return

    const targetId =
      normalized === 'incidents' ? 'incidents-section' : 'pipeline-section'

    let attempts = 0
    let timer: number | undefined

    const tryScroll = () => {
      attempts += 1
      if (document.getElementById(targetId)) {
        scrollToStormSection(targetId)
        return
      }
      if (attempts < 12) {
        timer = window.setTimeout(tryScroll, 100)
      }
    }

    timer = window.setTimeout(tryScroll, 50)
    return () => {
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [viewParam, isLoading, noSwitchesConfigured, pagedSwitches.length])

  return (
    <div id="overview-section" className="np-page scroll-mt-24">
      <PageHeader
        title="Storm Protection"
        description="Per-switch eligibility → risk → confirmation → safety → diagnostics → mitigation → recovery."
        actions={
          <StormQuickActions
            isAdmin={isAdmin}
            isBusy={isBusy}
            enableEligibility={stormConfig.data?.enableEligibility}
            enableRisk={stormConfig.data?.risk?.enableRisk}
            confirmationEnabled={stormConfig.data?.confirmation?.confirmationEnabled}
            safetyEnabled={stormConfig.data?.safety?.safetyEnabled}
            eligibilityPending={eligibilityMutations.evaluateAll.isPending}
            riskPending={riskMutations.calculateAll.isPending}
            confirmationPending={confirmationMutations.evaluateAll.isPending}
            safetyPending={safetyMutations.evaluateAll.isPending}
            preparePending={orchestratorMutations.prepareAll.isPending}
            onEvaluateEligibility={() => eligibilityMutations.evaluateAll.mutate()}
            onCalculateRisk={() => riskMutations.calculateAll.mutate()}
            onEvaluateConfirmation={() => confirmationMutations.evaluateAll.mutate()}
            onEvaluateSafety={() => safetyMutations.evaluateAll.mutate()}
            onPrepareIncidents={() => orchestratorMutations.prepareAll.mutate()}
            onRefresh={refreshAll}
          />
        }
      />

      <StormOverview
        fleetKpis={fleetKpis}
        isAdmin={isAdmin}
        settings={{
          mitigationMode: settingsQuery.data?.mitigationMode,
          autoRecovery: settingsQuery.data?.autoRecovery,
        }}
        settingsPending={settingsMutation.isPending}
        onSetMitigationMode={(mode) => settingsMutation.mutate({ mitigationMode: mode })}
        onSetAutoRecovery={(enabled) => settingsMutation.mutate({ autoRecovery: enabled })}
        query={query}
        onQueryChange={setQuery}
        severityFilter={severityFilter}
        onSeverityFilterChange={setSeverityFilter}
        eligibleFilter={eligibleFilter}
        onEligibleFilterChange={setEligibleFilter}
        confirmStateFilter={confirmStateFilter}
        onConfirmStateFilterChange={setConfirmStateFilter}
        safetyStatusFilter={safetyStatusFilter}
        onSafetyStatusFilterChange={setSafetyStatusFilter}
        incidentStatusFilter={incidentStatusFilter}
        onIncidentStatusFilterChange={setIncidentStatusFilter}
        switchFilter={switchFilter}
        onSwitchFilterChange={setSwitchFilter}
        switchChips={switchChips}
        showSwitchControls={showSwitchControls}
        switchPagination={{
          page: switchPagination.page,
          totalPages: switchPagination.totalPages,
          total: switchPagination.total,
          limit: switchPagination.limit,
          setPage: switchPagination.setPage,
          setLimit: switchPagination.setLimit,
        }}
      />

      {noSwitchesConfigured ? (
        <EmptyState
          icon={Network}
          title="No managed switches"
          description="Add a managed switch from Device Inventory to view per-switch storm protection details."
        />
      ) : isLoading ? (
        <TableSkeleton rows={8} />
      ) : isError ? (
        <ErrorState
          title="Unable to load storm protection data"
          message="One or more storm datasets failed to load."
          onRetry={refreshAll}
        />
      ) : groupedSwitches.length === 0 ? (
        <EmptyState
          icon={Shield}
          title={hasActiveFilters ? 'No matching switch storm data' : 'No storm data yet'}
          description={
            hasActiveFilters
              ? 'Adjust filters to see more switches.'
              : 'Run eligibility and risk evaluation after interface discovery and stats collection.'
          }
        />
      ) : (
        <div className="space-y-4">
          {pagedSwitches.map((device, index) => (
            <StormSwitchCard
              key={device.deviceId}
              device={device}
              collapsed={collapsedDeviceIds.has(device.deviceId)}
              isAdmin={isAdmin}
              requiredConfirmations={requiredConfirmations}
              pipelineSectionId={index === 0 ? 'pipeline-section' : undefined}
              incidentsSectionId={index === 0 ? 'incidents-section' : undefined}
              selectedRisk={
                selectedRisk?.deviceId === device.deviceId ? selectedRisk : null
              }
              selectedSafety={
                selectedSafety?.deviceId === device.deviceId ? selectedSafety : null
              }
              selectedIncident={
                selectedIncident?.deviceId === device.deviceId ? selectedIncident : null
              }
              selectedMitigation={
                selectedMitigation?.deviceId === device.deviceId
                  ? selectedMitigation
                  : null
              }
              selectedRecovery={
                selectedRecovery?.deviceId === device.deviceId ? selectedRecovery : null
              }
              expandedSections={expandedSections}
              trendData={selectedRisk?.deviceId === device.deviceId ? trendData : []}
              trendLoading={
                selectedRisk?.deviceId === device.deviceId && selectedHistoryQuery.isLoading
              }
              mitigationPending={mitigationMutations.execute.isPending}
              rollbackPending={mitigationMutations.rollback.isPending}
              recoveryPending={recoveryMutations.execute.isPending}
              retryPending={recoveryMutations.retry.isPending}
              onToggle={() => toggleDevice(device.deviceId)}
              onSelectRisk={setSelectedRisk}
              onSelectSafety={setSelectedSafety}
              onSelectIncident={(row) => {
                setSelectedIncident(row)
                setExpandedSections({})
              }}
              onViewIncident={(row) => {
                setSelectedIncident(row)
                setExpandedSections({
                  interface: true,
                  switchport: true,
                  mac: true,
                  neighbor: true,
                  timeline: true,
                })
              }}
              onSelectMitigation={setSelectedMitigation}
              onSelectRecovery={setSelectedRecovery}
              onToggleJsonSection={toggleSection}
              onExportIncident={exportIncident}
              onExecuteMitigation={(incidentId, strategy) =>
                mitigationMutations.execute.mutate({ incidentId, strategy })
              }
              onRollback={(incidentId) =>
                mitigationMutations.rollback.mutate({ incidentId })
              }
              onRetryRecovery={(incidentId) =>
                recoveryMutations.retry.mutate({ incidentId })
              }
              onForceRecovery={(incidentId) =>
                recoveryMutations.execute.mutate({ incidentId, force: true })
              }
            />
          ))}
        </div>
      )}
    </div>
  )
}
