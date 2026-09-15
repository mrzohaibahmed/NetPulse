import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  RefreshCw,
  Thermometer,
} from 'lucide-react'
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
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorState } from '@/shared/components/ErrorState'
import { KpiCard } from '@/shared/components/KpiCard'
import { LoadingState } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { useAuth } from '@/shared/auth/AuthContext'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Checkbox } from '@/shared/ui/checkbox'
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
  useDeviceQuery,
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

export function SwitchHardwareDetailPage() {
  const { deviceId = '' } = useParams()
  const { isAdmin } = useAuth()
  const settingsQuery = useSettingsQuery(true)
  const settingsMutation = useSettingsMutation()
  const monitoringEnabled = Boolean(settingsQuery.data?.switchHardwareMonitoringEnabled)
  const [confirmCollect, setConfirmCollect] = useState(false)
  const [eventsPage, setEventsPage] = useState(1)
  const [eventsLimit, setEventsLimit] = useState(25)
  const [outagesPage, setOutagesPage] = useState(1)
  const [outagesLimit, setOutagesLimit] = useState(10)
  const [selectedOutageId, setSelectedOutageId] = useState<string | null>(null)

  const deviceQuery = useDeviceQuery(deviceId)
  const hardwareQuery = useSwitchHardwareQuery(deviceId)
  const historyQuery = useSwitchHardwareHistoryQuery(deviceId, { page: 1, limit: 100 })
  const eventsQuery = useSwitchHardwareEventsQuery(deviceId, {
    page: eventsPage,
    limit: eventsLimit,
  })
  const outagesQuery = useSwitchHardwareOutagesQuery(deviceId, {
    page: outagesPage,
    limit: outagesLimit,
  })
  const outageDetailQuery = useSwitchHardwareOutageQuery(
    deviceId,
    selectedOutageId,
    Boolean(selectedOutageId),
  )
  const collectMutation = useSwitchHardwareCollectMutation(deviceId)

  const device = deviceQuery.data
  const hardware = hardwareQuery.data

  const overviewLoading = deviceQuery.isLoading || hardwareQuery.isLoading
  const overviewError = deviceQuery.error || hardwareQuery.error

  const activeOutage = useMemo(
    () => (outagesQuery.data?.items ?? []).find((item) => item.status === 'active') || null,
    [outagesQuery.data],
  )

  if (!deviceId) {
    return (
      <EmptyState
        title="Missing switch"
        description="No device id was provided in the URL."
        action={
          <Button asChild variant="secondary" size="sm">
            <Link to="/switches/hardware">Back to Hardware Health</Link>
          </Button>
        }
      />
    )
  }

  return (
    <div className="np-page space-y-6">
      <PageHeader
        title={device?.hostname || 'Switch Hardware'}
        description={`${device?.ipAddress || '—'} · Cisco chassis health and outage evidence`}
        meta={
          <div className="flex flex-wrap items-center gap-2">
            <Button asChild variant="ghost" size="sm">
              <Link to="/switches/hardware">
                <ArrowLeft className="h-4 w-4" />
                Hardware Health
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
                void hardwareQuery.refetch()
                void historyQuery.refetch()
                void eventsQuery.refetch()
                void outagesQuery.refetch()
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

      {overviewLoading ? (
        <LoadingState label="Loading hardware health…" />
      ) : overviewError ? (
        <ErrorState
          title="Unable to load hardware monitoring data"
          message={overviewError instanceof Error ? overviewError.message : 'Request failed'}
          onRetry={() => void hardwareQuery.refetch()}
        />
      ) : !hardware ? (
        <EmptyState
          title="Hardware data unavailable"
          description="No hardware collection exists for this switch yet. Confirm Vendor is Cisco (or blank with credentials), enable monitoring, then use Collect Now."
        />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <KpiCard
              label="Overall Health"
              value={(hardware.overallHealth || 'unknown').toString()}
              icon={Thermometer}
              tone={
                (hardware.overallHealth || '').toLowerCase() === 'critical'
                  ? 'danger'
                  : (hardware.overallHealth || '').toLowerCase() === 'warning'
                    ? 'warning'
                    : (hardware.overallHealth || '').toLowerCase() === 'healthy'
                      ? 'success'
                      : 'default'
              }
            />
            <KpiCard
              label="Collection"
              value={hardware.collectionStatus || 'unknown'}
              hint={
                hardware.lastSuccessfulCollectionAt
                  ? `Last success ${formatRelative(hardware.lastSuccessfulCollectionAt)}`
                  : 'No successful collection yet'
              }
            />
            <KpiCard
              label="SNMP / SSH"
              value={`${hardware.availability?.snmp || 'unknown'} / ${hardware.availability?.ssh || 'unknown'}`}
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
            />
          </div>

          <Card className="border-border/70">
            <CardHeader>
              <CardTitle className="text-base">Overview</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <p className="text-xs text-muted-foreground">Model</p>
                <p className="font-medium">{formatHardwareValue(hardware.inventory?.model)}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Serial</p>
                <p className="font-medium mono">
                  {formatHardwareValue(hardware.inventory?.serialNumber)}
                </p>
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

          <TemperatureSection temperature={hardware.temperature} />
          <HardwareTrendChart
            history={historyQuery.data?.items ?? []}
            loading={historyQuery.isLoading}
          />
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
