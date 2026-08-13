import type { DashboardSummary } from '@/types'

export type HealthLabel = 'Excellent' | 'Good' | 'Warning' | 'Critical'

export interface NetworkHealth {
  score: number
  label: HealthLabel
}

function toFiniteNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

/**
 * Network Health Score = share of devices currently Online.
 * Uses device counts when present; falls back to onlinePercentage.
 * Does not double-penalize offline/unreachable devices (that previously
 * drove legitimate low-online fleets to a false 0%).
 */
export function computeNetworkHealth(summary: DashboardSummary | null | undefined): NetworkHealth {
  if (!summary) {
    return { score: 100, label: 'Excellent' }
  }

  const total = toFiniteNumber(summary.totalDevices)
  if (total === null || total === 0) {
    return { score: 100, label: 'Excellent' }
  }

  const online = toFiniteNumber(summary.onlineDevices)
  const onlinePercentage = toFiniteNumber(summary.onlinePercentage)

  let rawScore: number | null = null
  if (online !== null) {
    rawScore = (online / total) * 100
  } else if (onlinePercentage !== null) {
    rawScore = onlinePercentage
  }

  // Incomplete summary with devices present — do not invent a Critical 0%.
  if (rawScore === null) {
    return { score: 100, label: 'Excellent' }
  }

  const score = Math.max(0, Math.min(100, Math.round(rawScore)))

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
