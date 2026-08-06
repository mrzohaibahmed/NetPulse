import { Link } from 'react-router-dom'
import { ChevronDown, ChevronUp, ExternalLink, Network } from 'lucide-react'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent } from '@/shared/ui/card'
import type {
  MitigationLog,
  RecoveryLog,
  RiskResult,
  SafetyResult,
  StormIncident,
} from '@/types'
import { StormConfirmationSection } from './StormConfirmationSection'
import { StormEligibilitySection } from './StormEligibilitySection'
import { StormIncidentSection } from './StormIncidentSection'
import { StormMitigationSection } from './StormMitigationSection'
import { StormRecoverySection } from './StormRecoverySection'
import { StormRiskSection } from './StormRiskSection'
import { StormSafetySection } from './StormSafetySection'
import { Kpi, summarizeSwitch, type SwitchStormSectionData } from './stormShared'

type StormSwitchCardProps = {
  device: SwitchStormSectionData
  collapsed: boolean
  isAdmin: boolean
  requiredConfirmations: number
  pipelineSectionId?: string
  incidentsSectionId?: string
  selectedRisk: RiskResult | null
  selectedSafety: SafetyResult | null
  selectedIncident: StormIncident | null
  selectedMitigation: MitigationLog | null
  selectedRecovery: RecoveryLog | null
  expandedSections: Record<string, boolean>
  trendData: Array<{ time: string; label: string; riskScore: number; severity: string }>
  trendLoading: boolean
  mitigationPending: boolean
  rollbackPending: boolean
  recoveryPending: boolean
  retryPending: boolean
  onToggle: () => void
  onSelectRisk: (row: RiskResult) => void
  onSelectSafety: (row: SafetyResult) => void
  onSelectIncident: (row: StormIncident) => void
  onViewIncident: (row: StormIncident) => void
  onSelectMitigation: (row: MitigationLog) => void
  onSelectRecovery: (row: RecoveryLog) => void
  onToggleJsonSection: (key: string) => void
  onExportIncident: (incident: StormIncident) => void
  onExecuteMitigation: (incidentId: string, strategy: string) => void
  onRollback: (incidentId: string) => void
  onRetryRecovery: (incidentId: string) => void
  onForceRecovery: (incidentId: string) => void
}

export function StormSwitchCard({
  device,
  collapsed,
  isAdmin,
  requiredConfirmations,
  pipelineSectionId,
  incidentsSectionId,
  selectedRisk,
  selectedSafety,
  selectedIncident,
  selectedMitigation,
  selectedRecovery,
  expandedSections,
  trendData,
  trendLoading,
  mitigationPending,
  rollbackPending,
  recoveryPending,
  retryPending,
  onToggle,
  onSelectRisk,
  onSelectSafety,
  onSelectIncident,
  onViewIncident,
  onSelectMitigation,
  onSelectRecovery,
  onToggleJsonSection,
  onExportIncident,
  onExecuteMitigation,
  onRollback,
  onRetryRecovery,
  onForceRecovery,
}: StormSwitchCardProps) {
  const summary = summarizeSwitch(device)

  return (
    <Card className="border-border/70 shadow-sm">
      <div className="border-b border-border/60 bg-card">
        <div className="space-y-3 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-md bg-primary/10 p-1 text-primary">
                  <Network className="h-4 w-4" />
                </span>
                <h3 className="truncate text-base font-semibold">{device.hostname}</h3>
                <Badge variant={device.monitor ? 'success' : 'muted'}>
                  {device.monitor ? 'Monitored' : 'Not monitored'}
                </Badge>
                <StatusBadge status={device.status} />
              </div>
              <p className="mono mt-1 text-xs text-muted-foreground">
                {device.ipAddress} · {device.vendor} · {device.risk.length} scored ports
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button type="button" size="sm" variant="secondary" asChild>
                <Link to={`/devices/${device.deviceId}`}>
                  <ExternalLink className="h-3.5 w-3.5" />
                  Device
                </Link>
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={onToggle}>
                {collapsed ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronUp className="h-4 w-4" />
                )}
                {collapsed ? 'Expand' : 'Collapse'}
              </Button>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">
            <Kpi label="Eligible" value={String(summary.eligible)} tone="success" />
            <Kpi
              label="Critical risk"
              value={String(summary.critical)}
              tone={summary.critical > 0 ? 'danger' : 'default'}
            />
            <Kpi
              label="Confirmed"
              value={String(summary.confirmed)}
              tone={summary.confirmed > 0 ? 'warning' : 'default'}
            />
            <Kpi label="Safe" value={String(summary.safe)} tone="success" />
            <Kpi
              label="Open incidents"
              value={String(summary.openIncidents)}
              tone={summary.openIncidents > 0 ? 'danger' : 'default'}
            />
          </div>
        </div>
      </div>

      {!collapsed ? (
        <CardContent className="space-y-0 p-0">
          <StormRiskSection
            rows={device.risk}
            selectedRisk={selectedRisk}
            onSelectRisk={onSelectRisk}
            trendData={trendData}
            trendLoading={trendLoading}
            sectionId={pipelineSectionId}
          />
          <StormEligibilitySection rows={device.eligibility} />
          <StormConfirmationSection
            rows={device.confirmation}
            requiredConfirmations={requiredConfirmations}
          />
          <StormSafetySection
            rows={device.safety}
            selectedSafety={selectedSafety}
            onSelectSafety={onSelectSafety}
          />
          <StormIncidentSection
            rows={device.incidents}
            selectedIncident={selectedIncident}
            expandedSections={expandedSections}
            isAdmin={isAdmin}
            mitigationPending={mitigationPending}
            rollbackPending={rollbackPending}
            recoveryPending={recoveryPending}
            retryPending={retryPending}
            sectionId={incidentsSectionId}
            onSelectIncident={onSelectIncident}
            onViewIncident={onViewIncident}
            onExportIncident={onExportIncident}
            onToggleJsonSection={onToggleJsonSection}
            onExecuteMitigation={onExecuteMitigation}
            onRollback={onRollback}
            onRetryRecovery={onRetryRecovery}
            onForceRecovery={onForceRecovery}
          />
          <StormMitigationSection
            rows={device.mitigation}
            selectedMitigation={selectedMitigation}
            onSelectMitigation={onSelectMitigation}
          />
          <StormRecoverySection
            rows={device.recovery}
            selectedRecovery={selectedRecovery}
            onSelectRecovery={onSelectRecovery}
          />
        </CardContent>
      ) : null}
    </Card>
  )
}
