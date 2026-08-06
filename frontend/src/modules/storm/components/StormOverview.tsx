import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Input } from '@/shared/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select'
import { Badge } from '@/shared/ui/badge'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { cn } from '@/lib/utils'

export type StormSwitchChip = {
  deviceId: string
  hostname: string
  ipAddress: string
  critical: number
  confirmed: number
  openIncidents: number
}

export type StormFleetKpis = {
  switches: number
  eligible: number
  critical: number
  confirmed: number
  safe: number
  incidents: number
}

type StormOverviewProps = {
  fleetKpis: StormFleetKpis
  isAdmin: boolean
  settings: {
    mitigationMode?: string
    autoRecovery?: boolean
  }
  settingsPending: boolean
  onSetMitigationMode: (mode: 'automatic' | 'manual') => void
  onSetAutoRecovery: (enabled: boolean) => void
  query: string
  onQueryChange: (value: string) => void
  severityFilter: string
  onSeverityFilterChange: (value: string) => void
  eligibleFilter: string
  onEligibleFilterChange: (value: string) => void
  confirmStateFilter: string
  onConfirmStateFilterChange: (value: string) => void
  safetyStatusFilter: string
  onSafetyStatusFilterChange: (value: string) => void
  incidentStatusFilter: string
  onIncidentStatusFilterChange: (value: string) => void
  switchFilter: string
  onSwitchFilterChange: (value: string | ((current: string) => string)) => void
  switchChips: StormSwitchChip[]
  showSwitchControls: boolean
  switchPagination?: {
    page: number
    totalPages: number
    total: number
    limit: number
    setPage: (page: number) => void
    setLimit: (limit: number) => void
  }
}

export function StormOverview({
  fleetKpis,
  isAdmin,
  settings,
  settingsPending,
  onSetMitigationMode,
  onSetAutoRecovery,
  query,
  onQueryChange,
  severityFilter,
  onSeverityFilterChange,
  eligibleFilter,
  onEligibleFilterChange,
  confirmStateFilter,
  onConfirmStateFilterChange,
  safetyStatusFilter,
  onSafetyStatusFilterChange,
  incidentStatusFilter,
  onIncidentStatusFilterChange,
  switchFilter,
  onSwitchFilterChange,
  switchChips,
  showSwitchControls,
  switchPagination,
}: StormOverviewProps) {
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Switches</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{fleetKpis.switches}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Eligible</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-success">{fleetKpis.eligible}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Critical risk</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-danger">{fleetKpis.critical}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Confirmed</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-success">{fleetKpis.confirmed}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Safe</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-success">{fleetKpis.safe}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Incidents</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{fleetKpis.incidents}</p>
          </CardContent>
        </Card>
      </div>

      <Card className="border-primary/20 bg-secondary/10">
        <CardContent className="flex flex-wrap items-center justify-between gap-4 p-4">
          <div className="space-y-1">
            <p className="text-sm font-semibold">Automation</p>
            <p className="text-xs text-muted-foreground">
              Mitigation:{' '}
              <span className="font-semibold uppercase text-primary">
                {settings.mitigationMode || 'manual'}
              </span>
              {' · '}
              Auto recovery:{' '}
              <span className="font-semibold uppercase text-primary">
                {settings.autoRecovery ? 'enabled' : 'disabled'}
              </span>
            </p>
          </div>
          {isAdmin ? (
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant={settings.mitigationMode === 'automatic' ? 'default' : 'outline'}
                disabled={settingsPending}
                onClick={() => onSetMitigationMode('automatic')}
              >
                Automatic mitigation
              </Button>
              <Button
                type="button"
                size="sm"
                variant={settings.mitigationMode === 'manual' ? 'default' : 'outline'}
                disabled={settingsPending}
                onClick={() => onSetMitigationMode('manual')}
              >
                Manual mitigation
              </Button>
              <Button
                type="button"
                size="sm"
                variant={settings.autoRecovery ? 'default' : 'outline'}
                disabled={settingsPending}
                onClick={() => onSetAutoRecovery(true)}
              >
                Enable recovery
              </Button>
              <Button
                type="button"
                size="sm"
                variant={!settings.autoRecovery ? 'default' : 'outline'}
                disabled={settingsPending}
                onClick={() => onSetAutoRecovery(false)}
              >
                Disable recovery
              </Button>
            </div>
          ) : (
            <Badge variant="outline" className="uppercase">
              Admin configurable only
            </Badge>
          )}
        </CardContent>
      </Card>

      <div className="flex flex-wrap gap-3">
        <Input
          type="search"
          className="max-w-sm"
          placeholder="Search interface, host, incident…"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
        />
        <Select value={severityFilter} onValueChange={onSeverityFilterChange}>
          <SelectTrigger className="w-[160px]">
            <SelectValue placeholder="Severity" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All severities</SelectItem>
            <SelectItem value="LOW">Low</SelectItem>
            <SelectItem value="MEDIUM">Medium</SelectItem>
            <SelectItem value="HIGH">High</SelectItem>
            <SelectItem value="CRITICAL">Critical</SelectItem>
          </SelectContent>
        </Select>
        <Select value={eligibleFilter} onValueChange={onEligibleFilterChange}>
          <SelectTrigger className="w-[160px]">
            <SelectValue placeholder="Eligibility" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All eligibility</SelectItem>
            <SelectItem value="eligible">Eligible</SelectItem>
            <SelectItem value="ineligible">Not eligible</SelectItem>
          </SelectContent>
        </Select>
        <Select value={confirmStateFilter} onValueChange={onConfirmStateFilterChange}>
          <SelectTrigger className="w-[180px]">
            <SelectValue placeholder="Confirmation" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All confirmation</SelectItem>
            <SelectItem value="NOT_CONFIRMED">Not confirmed</SelectItem>
            <SelectItem value="PENDING">Pending</SelectItem>
            <SelectItem value="CONFIRMED">Confirmed</SelectItem>
          </SelectContent>
        </Select>
        <Select value={safetyStatusFilter} onValueChange={onSafetyStatusFilterChange}>
          <SelectTrigger className="w-[150px]">
            <SelectValue placeholder="Safety" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All safety</SelectItem>
            <SelectItem value="SAFE">Safe</SelectItem>
            <SelectItem value="WAITING">Waiting</SelectItem>
            <SelectItem value="UNSAFE">Unsafe</SelectItem>
          </SelectContent>
        </Select>
        <Select value={incidentStatusFilter} onValueChange={onIncidentStatusFilterChange}>
          <SelectTrigger className="w-[200px]">
            <SelectValue placeholder="Incidents" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All incidents</SelectItem>
            <SelectItem value="OPEN">Open</SelectItem>
            <SelectItem value="READY_FOR_MITIGATION">Ready for mitigation</SelectItem>
            <SelectItem value="PREPARED">Prepared</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {showSwitchControls ? (
        <>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant={switchFilter === 'all' ? 'default' : 'secondary'}
              onClick={() => onSwitchFilterChange('all')}
            >
              All switches ({switchChips.length})
            </Button>
            {switchChips.map((device) => {
              const selected = switchFilter === device.deviceId
              return (
                <button
                  key={device.deviceId}
                  type="button"
                  className={cn(
                    'min-w-[180px] rounded-lg border px-3 py-2 text-left transition-colors',
                    selected
                      ? 'border-primary bg-primary/10'
                      : 'border-border/70 bg-card hover:bg-muted/40',
                  )}
                  onClick={() =>
                    onSwitchFilterChange((current) =>
                      current === device.deviceId ? 'all' : device.deviceId,
                    )
                  }
                >
                  <p className="truncate text-sm font-semibold">{device.hostname}</p>
                  <p className="mono truncate text-xs text-muted-foreground">
                    {device.ipAddress}
                  </p>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    Crit {device.critical} · Conf {device.confirmed} · Inc{' '}
                    {device.openIncidents}
                  </p>
                </button>
              )
            })}
          </div>

          {switchPagination &&
          (switchPagination.totalPages > 1 ||
            switchPagination.total > switchPagination.limit) ? (
            <PaginationControls
              page={switchPagination.page}
              totalPages={Math.max(switchPagination.totalPages, 1)}
              total={switchPagination.total}
              limit={switchPagination.limit}
              onPageChange={switchPagination.setPage}
              onLimitChange={switchPagination.setLimit}
              limitOptions={[5, 10, 25, 50]}
              unitLabel="Switches"
            />
          ) : null}
        </>
      ) : null}
    </>
  )
}
