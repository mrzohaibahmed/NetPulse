import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, RefreshCw, Thermometer } from 'lucide-react'
import { CpuMemorySection } from '@/modules/storm/components/hardware/CpuMemorySection'
import { FansSection } from '@/modules/storm/components/hardware/FansSection'
import { HardwareEventsTable } from '@/modules/storm/components/hardware/HardwareEventsTable'
import { HardwareTrendChart } from '@/modules/storm/components/hardware/HardwareTrendChart'
import { InventorySection } from '@/modules/storm/components/hardware/InventorySection'
import { OutageHistoryTable } from '@/modules/storm/components/hardware/OutageHistoryTable'
import { PowerSuppliesSection } from '@/modules/storm/components/hardware/PowerSuppliesSection'
import { TemperatureSection } from '@/modules/storm/components/hardware/TemperatureSection'
import {
  formatHardwareValue,
  HardwareHealthBadge,
} from '@/modules/storm/components/hardware/hardwareStatus'
import {
  InterfaceStatusBadge,
  PortClassificationBadges,
  PortModeBadge,
  formatAllowedVlans,
  neighborRemotePort,
} from '@/modules/storm/components/InterfaceStatusBadge'
import { isManagedSwitch } from '@/modules/storm/components/stormShared'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { KpiCard } from '@/shared/components/KpiCard'
import { LoadingState, TableSkeleton } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { useAuth } from '@/shared/auth/AuthContext'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Checkbox } from '@/shared/ui/checkbox'
import { Input } from '@/shared/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/ui/alert-dialog'
import {
  useDeviceInterfacesQuery,
  useDeviceQuery,
  useDeviceRiskQuery,
  useSettingsMutation,
  useSettingsQuery,
  useSwitchHardwareCollectMutation,
  useSwitchHardwareEventsQuery,
  useSwitchHardwareHistoryQuery,
  useSwitchHardwareOutageQuery,
  useSwitchHardwareOutagesQuery,
  useSwitchHardwareQuery,
} from '@/hooks/queries'
import { formatDateTime, formatRelative } from '@/utils/format'
import type { NetworkInterface } from '@/types'
import type { SwitchOutageIncident } from '@/api/switchHardwareService'

const TAB_IDS = ['overview', 'hardware', 'interfaces', 'topology', 'outages', 'events'] as const
type TabId = (typeof TAB_IDS)[number]

function isTabId(value: string | null): value is TabId {
  return Boolean(value) && (TAB_IDS as readonly string[]).includes(value as string)
}

const TAB_LABELS: Record<TabId, string> = {
  overview: 'Overview',
  hardware: 'Hardware',
  interfaces: 'Interfaces',
  topology: 'Topology',
  outages: 'Outages',
  events: 'Events',
}

const RISK_RANK: Record<string, number> = {
  CRITICAL: 4,
  HIGH: 3,
  MEDIUM: 2,
  LOW: 1,
  UNKNOWN: 0,
}

function riskRank(value: string | null | undefined): number {
  return RISK_RANK[(value || 'UNKNOWN').toUpperCase()] ?? 0
}

const KNOWN_RISK_LEVELS = new Set(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'])

/**
 * Mirrors InterfacesEnterprisePage.tsx's normalizeRisk() exactly (that
 * function isn't exported, so its semantics are reproduced here locally
 * rather than importing across pages): a missing, empty, or unrecognized
 * severity normalizes to 'UNKNOWN' — never a default known level — so a
 * risk-engine record never displays a different verdict on this page than
 * it does on the fleet Interfaces page.
 */
function normalizeRiskSeverity(value: string | null | undefined): string {
  const normalized = String(value || '').toUpperCase()
  return KNOWN_RISK_LEVELS.has(normalized) ? normalized : 'UNKNOWN'
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-1.5 border-b-2 px-4 py-3 text-sm font-medium transition-colors ${
        active
          ? 'border-primary text-primary'
          : 'border-transparent text-muted-foreground hover:text-foreground'
      }`}
    >
      {children}
    </button>
  )
}

export function SwitchDetailPage() {
  const { deviceId = '' } = useParams()
  const navigate = useNavigate()
  const { isAdmin } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()

  const rawTab = searchParams.get('tab')
  const activeTab: TabId = isTabId(rawTab) ? rawTab : 'overview'

  const setActiveTab = (tab: TabId) => {
    const next = new URLSearchParams(searchParams)
    if (tab === 'overview') {
      next.delete('tab')
    } else {
      next.set('tab', tab)
    }
    setSearchParams(next, { replace: true })
  }

  // Topology has no separate view here — it belongs on the Network Topology
  // page's Level 1 neighborhood view for this switch, so any way of landing
  // on this tab (clicking it, or a deep link) redirects there instead of
  // rendering a second, disconnected topology of just this switch.
  useEffect(() => {
    if (activeTab === 'topology') {
      navigate(`/topology?switch=${deviceId}`, { replace: true })
    }
  }, [activeTab, deviceId, navigate])

  // ---------------------------------------------------------------------
  // Hardware monitoring toggle + Collect Now (shared: header + Overview + Hardware)
  // ---------------------------------------------------------------------
  const settingsQuery = useSettingsQuery(true)
  const settingsMutation = useSettingsMutation()
  const monitoringEnabled = Boolean(settingsQuery.data?.switchHardwareMonitoringEnabled)
  const [confirmCollect, setConfirmCollect] = useState(false)
  const collectMutation = useSwitchHardwareCollectMutation(deviceId)

  // ---------------------------------------------------------------------
  // Always-loaded: device + hardware current + interfaces + risk are all
  // needed by the default (Overview) tab. Outages list is small and also
  // feeds the Overview "active outage" summary, so it's loaded eagerly too
  // — the same query/cache is then reused by the Outages tab (no refetch).
  // ---------------------------------------------------------------------
  const deviceQuery = useDeviceQuery(deviceId)
  const hardwareQuery = useSwitchHardwareQuery(deviceId)
  const interfacesQuery = useDeviceInterfacesQuery(deviceId, { limit: 500 })
  const riskQuery = useDeviceRiskQuery(deviceId, { limit: 500 })

  const [outagesPage, setOutagesPage] = useState(1)
  const [outagesLimit, setOutagesLimit] = useState(10)
  const outagesQuery = useSwitchHardwareOutagesQuery(deviceId, {
    page: outagesPage,
    limit: outagesLimit,
  })
  const [selectedOutageId, setSelectedOutageId] = useState<string | null>(null)
  const outageDetailQuery = useSwitchHardwareOutageQuery(
    deviceId,
    selectedOutageId,
    Boolean(selectedOutageId),
  )

  // ---------------------------------------------------------------------
  // Lazy: only fetched once their tab has actually been opened.
  // ---------------------------------------------------------------------
  const historyQuery = useSwitchHardwareHistoryQuery(
    deviceId,
    { page: 1, limit: 100 },
    activeTab === 'hardware',
  )
  const [eventsPage, setEventsPage] = useState(1)
  const [eventsLimit, setEventsLimit] = useState(25)
  const eventsQuery = useSwitchHardwareEventsQuery(
    deviceId,
    { page: eventsPage, limit: eventsLimit },
    activeTab === 'events',
  )
  const device = deviceQuery.data
  const hardware = hardwareQuery.data
  const interfaceRows: NetworkInterface[] = interfacesQuery.data?.data ?? []
  const riskRows = riskQuery.data?.data ?? []

  const riskByInterface = useMemo(() => {
    const map = new Map<string, string>()
    for (const row of riskRows) {
      const key = String(row.interface || '').toLowerCase()
      if (!key) continue
      const level = normalizeRiskSeverity(row.severity)
      const existing = map.get(key)
      if (!existing || riskRank(level) > riskRank(existing)) {
        map.set(key, level)
      }
    }
    return map
  }, [riskRows])

  const interfaceSummary = useMemo(() => {
    let up = 0
    let down = 0
    let access = 0
    let trunk = 0
    let protectedCount = 0
    let critical = 0
    for (const row of interfaceRows) {
      if ((row.operStatus || '').toLowerCase() === 'up') up += 1
      else down += 1
      if (row.isAccess || row.portMode === 'access' || row.mode === 'access') access += 1
      if (row.isTrunk || row.portMode === 'trunk' || row.mode === 'trunk') trunk += 1
      if (row.isProtected) protectedCount += 1
      if ((riskByInterface.get(row.name.toLowerCase()) || 'LOW') === 'CRITICAL') critical += 1
    }
    return { total: interfaceRows.length, up, down, access, trunk, protectedCount, critical }
  }, [interfaceRows, riskByInterface])

  const activeOutage = useMemo(
    () => (outagesQuery.data?.items ?? []).find((item) => item.status === 'active') || null,
    [outagesQuery.data],
  )

  const [interfaceSearch, setInterfaceSearch] = useState('')
  const filteredInterfaces = useMemo(() => {
    const q = interfaceSearch.trim().toLowerCase()
    if (!q) return interfaceRows
    return interfaceRows.filter((row) =>
      [row.name, row.description, row.neighbor?.hostname]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(q),
    )
  }, [interfaceRows, interfaceSearch])

  if (!deviceId) {
    return (
      <EmptyState
        title="Missing switch"
        description="No device id was provided in the URL."
        action={
          <Button asChild variant="secondary" size="sm">
            <Link to="/switches">Back to Switches</Link>
          </Button>
        }
      />
    )
  }

  const headerLoading = deviceQuery.isLoading
  const headerError = deviceQuery.error

  return (
    <div className="np-page space-y-6">
      <PageHeader
        title={device?.hostname || 'Switch'}
        description={`${device?.ipAddress || '—'} · ${device?.deviceType || 'Managed switch'}`}
        meta={
          <div className="flex flex-wrap items-center gap-2">
            <Button asChild variant="ghost" size="sm">
              <Link to="/switches">
                <ArrowLeft className="h-4 w-4" />
                Switches
              </Link>
            </Button>
            {device ? <StatusBadge status={device.status} /> : null}
            <HardwareHealthBadge status={hardware?.overallHealth} />
            {hardware?.platform ? <Badge variant="secondary">{hardware.platform}</Badge> : null}
          </div>
        }
        actions={
          <div className="flex flex-wrap gap-2">
            {isAdmin ? (
              <label className="flex items-center gap-2 rounded-lg border border-border/70 bg-card px-3 py-2 text-sm">
                <Checkbox
                  checked={monitoringEnabled}
                  disabled={settingsQuery.isLoading || settingsMutation.isPending}
                  onCheckedChange={(checked) =>
                    settingsMutation.mutate({
                      switchHardwareMonitoringEnabled: Boolean(checked),
                    })
                  }
                />
                <span className="font-medium">
                  {monitoringEnabled ? 'Monitoring enabled' : 'Enable monitoring'}
                </span>
              </label>
            ) : null}
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => {
                void deviceQuery.refetch()
                void hardwareQuery.refetch()
                void interfacesQuery.refetch()
                void outagesQuery.refetch()
                if (activeTab === 'hardware') void historyQuery.refetch()
                if (activeTab === 'events') void eventsQuery.refetch()
              }}
            >
              <RefreshCw className="h-4 w-4" />
              Refresh
            </Button>
            {isAdmin ? (
              <Button
                type="button"
                size="sm"
                loading={collectMutation.isPending}
                disabled={!monitoringEnabled}
                onClick={() => setConfirmCollect(true)}
              >
                Collect Now
              </Button>
            ) : null}
          </div>
        }
      />

      {!monitoringEnabled ? (
        <div className="rounded-xl border border-warning/40 bg-warning/10 px-4 py-3 text-sm">
          <p className="font-medium">Hardware monitoring is disabled</p>
          <p className="mt-1 text-muted-foreground">
            Enable monitoring to run Collect Now and scheduled hardware polling.
          </p>
        </div>
      ) : null}

      {headerLoading ? (
        <LoadingState label="Loading switch…" />
      ) : headerError ? (
        <ErrorState
          title="Unable to load switch"
          message={headerError instanceof Error ? headerError.message : 'Request failed'}
          onRetry={() => void deviceQuery.refetch()}
        />
      ) : !device ? (
        <EmptyState title="Switch not found" description="This device could not be located." />
      ) : (
        <>
          <div className="overflow-x-auto border-b border-border/60">
            <div className="flex w-max min-w-full">
              {TAB_IDS.map((id) => (
                <TabButton key={id} active={activeTab === id} onClick={() => setActiveTab(id)}>
                  {TAB_LABELS[id]}
                </TabButton>
              ))}
            </div>
          </div>

          {activeTab === 'overview' ? (
            <OverviewTab
              device={device}
              hardware={hardware}
              hardwareLoading={hardwareQuery.isLoading}
              hardwareError={hardwareQuery.error}
              activeOutage={activeOutage}
              interfaceSummary={interfaceSummary}
              interfacesLoading={interfacesQuery.isLoading}
              onOpenTab={setActiveTab}
            />
          ) : null}

          {activeTab === 'hardware' ? (
            <HardwareTab
              hardware={hardware}
              loading={hardwareQuery.isLoading}
              error={hardwareQuery.error}
              onRetry={() => void hardwareQuery.refetch()}
              history={historyQuery.data?.items ?? []}
              historyLoading={historyQuery.isLoading}
            />
          ) : null}

          {activeTab === 'interfaces' ? (
            <InterfacesTab
              deviceId={deviceId}
              loading={interfacesQuery.isLoading}
              error={interfacesQuery.error}
              onRetry={() => void interfacesQuery.refetch()}
              rows={filteredInterfaces}
              total={interfaceRows.length}
              search={interfaceSearch}
              onSearchChange={setInterfaceSearch}
              riskByInterface={riskByInterface}
              onOpenInterface={(name) => navigate(`/interfaces/${deviceId}/${encodeURIComponent(name)}`)}
            />
          ) : null}

          {activeTab === 'outages' ? (
            <OutageHistoryTable
              outages={outagesQuery.data?.items ?? []}
              page={outagesPage}
              totalPages={outagesQuery.data?.pagination.totalPages ?? 0}
              total={outagesQuery.data?.pagination.total ?? 0}
              limit={outagesLimit}
              onPageChange={setOutagesPage}
              onLimitChange={(limit) => {
                setOutagesLimit(limit)
                setOutagesPage(1)
              }}
              onSelectOutage={setSelectedOutageId}
              selectedOutage={outageDetailQuery.data || null}
              detailLoading={outageDetailQuery.isLoading}
            />
          ) : null}

          {activeTab === 'events' ? (
            <HardwareEventsTable
              events={eventsQuery.data?.items ?? []}
              page={eventsPage}
              totalPages={eventsQuery.data?.pagination.totalPages ?? 0}
              total={eventsQuery.data?.pagination.total ?? 0}
              limit={eventsLimit}
              onPageChange={setEventsPage}
              onLimitChange={(limit) => {
                setEventsLimit(limit)
                setEventsPage(1)
              }}
              loading={eventsQuery.isLoading}
            />
          ) : null}
        </>
      )}

      <AlertDialog open={confirmCollect} onOpenChange={setConfirmCollect}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Collect hardware data now?</AlertDialogTitle>
            <AlertDialogDescription>
              NetPulse will run a read-only SNMP/SSH hardware collection against this switch.
              No configuration commands will be executed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirmCollect(false)
                collectMutation.mutate()
              }}
            >
              Collect Now
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// ===========================================================================
// Overview
// ===========================================================================

function OverviewTab({
  device,
  hardware,
  hardwareLoading,
  hardwareError,
  activeOutage,
  interfaceSummary,
  interfacesLoading,
  onOpenTab,
}: {
  device: NonNullable<ReturnType<typeof useDeviceQuery>['data']>
  hardware: ReturnType<typeof useSwitchHardwareQuery>['data']
  hardwareLoading: boolean
  hardwareError: unknown
  activeOutage: SwitchOutageIncident | null
  interfaceSummary: {
    total: number
    up: number
    down: number
    access: number
    trunk: number
    protectedCount: number
    critical: number
  }
  interfacesLoading: boolean
  onOpenTab: (tab: TabId) => void
}) {
  const isSwitchType = isManagedSwitch(device.deviceType)

  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label="Overall Health"
          value={(hardware?.overallHealth || 'unknown').toString()}
          icon={Thermometer}
          loading={hardwareLoading}
          tone={
            (hardware?.overallHealth || '').toLowerCase() === 'critical'
              ? 'danger'
              : (hardware?.overallHealth || '').toLowerCase() === 'warning'
                ? 'warning'
                : (hardware?.overallHealth || '').toLowerCase() === 'healthy'
                  ? 'success'
                  : 'default'
          }
          onClick={() => onOpenTab('hardware')}
        />
        <KpiCard
          label="Collection"
          value={hardware?.collectionStatus || 'unknown'}
          loading={hardwareLoading}
          hint={
            hardware?.lastSuccessfulCollectionAt
              ? `Last success ${formatRelative(hardware.lastSuccessfulCollectionAt)}`
              : 'No successful collection yet'
          }
          onClick={() => onOpenTab('hardware')}
        />
        <KpiCard
          label="SNMP / SSH"
          value={`${hardware?.availability?.snmp || 'unknown'} / ${hardware?.availability?.ssh || 'unknown'}`}
          loading={hardwareLoading}
          onClick={() => onOpenTab('hardware')}
        />
        <KpiCard
          label="Active Outage"
          value={activeOutage ? 'Yes' : 'No'}
          tone={activeOutage ? 'danger' : 'success'}
          hint={
            activeOutage
              ? `Since ${formatDateTime(activeOutage.startedAt)}`
              : 'No active ping-triggered outage'
          }
          onClick={() => onOpenTab('outages')}
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <KpiCard label="Interfaces" value={interfaceSummary.total} loading={interfacesLoading} onClick={() => onOpenTab('interfaces')} />
        <KpiCard label="Up" value={interfaceSummary.up} tone="success" loading={interfacesLoading} onClick={() => onOpenTab('interfaces')} />
        <KpiCard label="Down" value={interfaceSummary.down} tone={interfaceSummary.down > 0 ? 'warning' : 'default'} loading={interfacesLoading} onClick={() => onOpenTab('interfaces')} />
        <KpiCard label="Access" value={interfaceSummary.access} loading={interfacesLoading} onClick={() => onOpenTab('interfaces')} />
        <KpiCard label="Trunk" value={interfaceSummary.trunk} loading={interfacesLoading} onClick={() => onOpenTab('interfaces')} />
        <KpiCard
          label="Critical Storm Risk"
          value={interfaceSummary.critical}
          tone={interfaceSummary.critical > 0 ? 'danger' : 'default'}
          loading={interfacesLoading}
          onClick={() => onOpenTab('interfaces')}
        />
      </div>

      <Card className="border-border/70">
        <CardHeader>
          <CardTitle className="text-base">Device</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <p className="text-xs text-muted-foreground">Hostname</p>
            <p className="font-medium">{device.hostname}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">IP address</p>
            <p className="font-medium mono">{device.ipAddress}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Device type</p>
            <p className="font-medium">{device.deviceType || 'Unknown'}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Status</p>
            <p className="font-medium">{device.status}</p>
          </div>
          {!isSwitchType ? (
            <div className="sm:col-span-2 lg:col-span-4 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-muted-foreground">
              This device's type isn't recognized as a managed switch — Hardware and Topology
              tabs may have no data for it.
            </div>
          ) : null}
        </CardContent>
      </Card>

      {hardwareError ? (
        <ErrorState
          title="Unable to load hardware monitoring data"
          message={hardwareError instanceof Error ? hardwareError.message : 'Request failed'}
        />
      ) : !hardwareLoading && !hardware ? (
        <EmptyState
          title="Hardware data unavailable"
          description="No hardware collection exists for this switch yet. Confirm Vendor is Cisco (or blank with credentials), enable monitoring, then use Collect Now on the Hardware tab."
        />
      ) : hardware ? (
        <Card className="border-border/70">
          <CardHeader>
            <CardTitle className="text-base">Hardware summary</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <p className="text-xs text-muted-foreground">Model</p>
              <p className="font-medium">{formatHardwareValue(hardware.inventory?.model)}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">IOS / firmware</p>
              <p className="font-medium">
                {formatHardwareValue(
                  hardware.inventory?.iosVersion || hardware.inventory?.firmwareVersion,
                )}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Last successful collection</p>
              <p className="font-medium">
                {hardware.lastSuccessfulCollectionAt
                  ? formatDateTime(hardware.lastSuccessfulCollectionAt)
                  : 'Not available'}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Last sample collected</p>
              <p className="font-medium">
                {hardware.lastAttemptedCollectionAt
                  ? formatDateTime(hardware.lastAttemptedCollectionAt)
                  : 'Not available'}
              </p>
            </div>
            {hardware.lastError ? (
              <div className="sm:col-span-2 lg:col-span-4 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
                Last collection note: {hardware.lastError}
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}

// ===========================================================================
// Hardware
// ===========================================================================

function HardwareTab({
  hardware,
  loading,
  error,
  onRetry,
  history,
  historyLoading,
}: {
  hardware: ReturnType<typeof useSwitchHardwareQuery>['data']
  loading: boolean
  error: unknown
  onRetry: () => void
  history: Parameters<typeof HardwareTrendChart>[0]['history']
  historyLoading: boolean
}) {
  if (loading) return <LoadingState label="Loading hardware health…" />
  if (error) {
    return (
      <ErrorState
        title="Unable to load hardware monitoring data"
        message={error instanceof Error ? error.message : 'Request failed'}
        onRetry={onRetry}
      />
    )
  }
  if (!hardware) {
    return (
      <EmptyState
        title="Hardware data unavailable"
        description="No hardware collection exists for this switch yet. Confirm Vendor is Cisco (or blank with credentials), enable monitoring, then use Collect Now."
      />
    )
  }

  return (
    <div className="space-y-6">
      <TemperatureSection temperature={hardware.temperature} />
      <HardwareTrendChart history={history} loading={historyLoading} />
      <div className="grid gap-6 xl:grid-cols-2">
        <FansSection fans={hardware.fans} />
        <PowerSuppliesSection powerSupplies={hardware.powerSupplies} />
      </div>
      <CpuMemorySection cpu={hardware.cpu} memory={hardware.memory} />
      <InventorySection
        inventory={hardware.inventory}
        vendor={hardware.vendor}
        platform={hardware.platform}
      />
    </div>
  )
}

// ===========================================================================
// Interfaces (device-scoped — GET /api/interfaces/:device_id)
// ===========================================================================

function InterfacesTab({
  loading,
  error,
  onRetry,
  rows,
  total,
  search,
  onSearchChange,
  riskByInterface,
  onOpenInterface,
}: {
  deviceId: string
  loading: boolean
  error: unknown
  onRetry: () => void
  rows: NetworkInterface[]
  total: number
  search: string
  onSearchChange: (value: string) => void
  riskByInterface: Map<string, string>
  onOpenInterface: (name: string) => void
}) {
  if (loading) return <TableSkeleton rows={6} />
  if (error) {
    return (
      <ErrorState
        title="Unable to load interfaces"
        message={error instanceof Error ? error.message : 'Request failed'}
        onRetry={onRetry}
      />
    )
  }
  if (total === 0) {
    return (
      <EmptyState
        title="No interfaces found"
        description="No interfaces have been discovered for this switch yet."
      />
    )
  }

  return (
    <Card className="border-border/70">
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3 space-y-0">
        <CardTitle className="text-base">Interfaces</CardTitle>
        <Input
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="Search name, description, neighbor…"
          className="max-w-xs"
        />
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <EmptyState title="No matching interfaces" description="Try a different search term." />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Interface</TableHead>
                  <TableHead>Admin</TableHead>
                  <TableHead>Oper</TableHead>
                  <TableHead>Mode</TableHead>
                  <TableHead>Classification</TableHead>
                  <TableHead>Speed / Duplex</TableHead>
                  <TableHead>VLAN</TableHead>
                  <TableHead>Neighbor</TableHead>
                  <TableHead>Storm Risk</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow
                    key={row._id}
                    className="cursor-pointer"
                    onClick={() => onOpenInterface(row.name)}
                  >
                    <TableCell className="font-medium">
                      <div>{row.name}</div>
                      {row.description ? (
                        <div className="text-xs text-muted-foreground">{row.description}</div>
                      ) : null}
                    </TableCell>
                    <TableCell>
                      <InterfaceStatusBadge status={row.adminStatus} kind="admin" />
                    </TableCell>
                    <TableCell>
                      <InterfaceStatusBadge status={row.operStatus} kind="oper" />
                    </TableCell>
                    <TableCell>
                      <PortModeBadge mode={row.portMode || row.mode} />
                    </TableCell>
                    <TableCell>
                      <PortClassificationBadges iface={row} />
                    </TableCell>
                    <TableCell className="mono text-sm">
                      {row.speed || '—'}
                      {row.duplex ? ` / ${row.duplex}` : ''}
                    </TableCell>
                    <TableCell className="mono text-sm">
                      {row.accessVlan ?? formatAllowedVlans(row.allowedVlans)}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {row.neighbor?.hostname
                        ? `${row.neighbor.hostname}${
                            neighborRemotePort(row.neighbor) ? ` (${neighborRemotePort(row.neighbor)})` : ''
                          }`
                        : '—'}
                    </TableCell>
                    <TableCell>
                      {(() => {
                        const level = riskByInterface.get(row.name.toLowerCase())
                        if (!level) return <span className="text-muted-foreground">—</span>
                        return (
                          <Badge
                            variant={
                              level === 'CRITICAL'
                                ? 'danger'
                                : level === 'HIGH' || level === 'MEDIUM'
                                  ? 'warning'
                                  : 'success'
                            }
                          >
                            {level}
                          </Badge>
                        )
                      })()}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

