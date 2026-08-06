import {
  Activity,
  CheckCircle2,
  FileJson,
  RefreshCw,
  Shield,
  ShieldCheck,
} from 'lucide-react'
import { Button } from '@/shared/ui/button'

type StormQuickActionsProps = {
  isAdmin: boolean
  isBusy: boolean
  enableEligibility?: boolean
  enableRisk?: boolean
  confirmationEnabled?: boolean
  safetyEnabled?: boolean
  eligibilityPending: boolean
  riskPending: boolean
  confirmationPending: boolean
  safetyPending: boolean
  preparePending: boolean
  onEvaluateEligibility: () => void
  onCalculateRisk: () => void
  onEvaluateConfirmation: () => void
  onEvaluateSafety: () => void
  onPrepareIncidents: () => void
  onRefresh: () => void
}

export function StormQuickActions({
  isAdmin,
  isBusy,
  enableEligibility,
  enableRisk,
  confirmationEnabled,
  safetyEnabled,
  eligibilityPending,
  riskPending,
  confirmationPending,
  safetyPending,
  preparePending,
  onEvaluateEligibility,
  onCalculateRisk,
  onEvaluateConfirmation,
  onEvaluateSafety,
  onPrepareIncidents,
  onRefresh,
}: StormQuickActionsProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {isAdmin ? (
        <>
          <Button
            type="button"
            variant="secondary"
            disabled={isBusy || enableEligibility === false}
            onClick={onEvaluateEligibility}
          >
            <Shield className="mr-2 h-4 w-4" />
            {eligibilityPending ? 'Evaluating…' : 'Evaluate eligibility'}
          </Button>
          <Button
            type="button"
            disabled={isBusy || enableRisk === false}
            onClick={onCalculateRisk}
          >
            <Activity className="mr-2 h-4 w-4" />
            {riskPending ? 'Scoring…' : 'Calculate risk'}
          </Button>
          <Button
            type="button"
            variant="secondary"
            disabled={isBusy || confirmationEnabled === false}
            onClick={onEvaluateConfirmation}
          >
            <CheckCircle2 className="mr-2 h-4 w-4" />
            {confirmationPending ? 'Confirming…' : 'Evaluate confirmation'}
          </Button>
          <Button
            type="button"
            variant="secondary"
            disabled={isBusy || safetyEnabled === false}
            onClick={onEvaluateSafety}
          >
            <ShieldCheck className="mr-2 h-4 w-4" />
            {safetyPending ? 'Checking…' : 'Evaluate safety'}
          </Button>
          <Button type="button" disabled={isBusy} onClick={onPrepareIncidents}>
            <FileJson className="mr-2 h-4 w-4" />
            {preparePending ? 'Preparing…' : 'Prepare incidents'}
          </Button>
        </>
      ) : null}
      <Button type="button" variant="secondary" onClick={onRefresh}>
        <RefreshCw className="mr-2 h-4 w-4" />
        Refresh
      </Button>
    </div>
  )
}
