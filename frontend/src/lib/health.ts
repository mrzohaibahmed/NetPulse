import type { DashboardSummary } from '@/types'

export type HealthLabel = 'Excellent' | 'Good' | 'Warning' | 'Critical'

export interface NetworkHealth {
  score: number
  label: HealthLabel
}

export function computeNetworkHealth(summary: DashboardSummary | null | undefined): NetworkHealth {
  if (!summary || summary.totalDevices === 0) {
    return { score: 100, label: 'Excellent' }
  }

  const onlineWeight = summary.onlinePercentage ?? 0
  const criticalPenalty = (summary.criticalOfflinePercentage ?? 0) * 1.5
  const unreachablePenalty = (summary.notReachablePercentage ?? 0) * 0.75
  const score = Math.max(0, Math.min(100, Math.round(onlineWeight - criticalPenalty - unreachablePenalty)))

  let label: HealthLabel = 'Excellent'
  if (score < 50) label = 'Critical'
  else if (score < 70) label = 'Warning'
  else if (score < 90) label = 'Good'

  return { score, label }
}

export function healthColor(label: HealthLabel): string {
  switch (label) {
    case 'Excellent':
      return '#22C55E'
    case 'Good':
      return '#3B82F6'
    case 'Warning':
      return '#F59E0B'
    case 'Critical':
      return '#EF4444'
  }
}
