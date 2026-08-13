import { describe, expect, it } from 'vitest'
import { computeNetworkHealth } from '@/lib/health'
import type { DashboardSummary } from '@/types'

function summary(partial: Partial<DashboardSummary>): DashboardSummary {
  return {
    totalDevices: 0,
    onlineDevices: 0,
    notReachableDevices: 0,
    criticalOfflineDevices: 0,
    unknownDevices: 0,
    criticalDevices: 0,
    monitoredDevices: 0,
    onlinePercentage: 0,
    notReachablePercentage: 0,
    criticalOfflinePercentage: 0,
    ...partial,
  }
}

describe('computeNetworkHealth', () => {
  it('returns 90% for 180 Online / 20 Not Reachable of 200', () => {
    const result = computeNetworkHealth(
      summary({
        totalDevices: 200,
        onlineDevices: 180,
        notReachableDevices: 20,
        monitoredDevices: 200,
        onlinePercentage: 90,
        notReachablePercentage: 10,
        criticalOfflinePercentage: 0,
      }),
    )
    expect(result.score).toBe(90)
    expect(result.label).toBe('Excellent')
  })

  it('returns 100% when all devices are Online', () => {
    const result = computeNetworkHealth(
      summary({
        totalDevices: 50,
        onlineDevices: 50,
        monitoredDevices: 50,
        onlinePercentage: 100,
      }),
    )
    expect(result.score).toBe(100)
    expect(result.label).toBe('Excellent')
  })

  it('returns 0% when all devices are unhealthy', () => {
    const result = computeNetworkHealth(
      summary({
        totalDevices: 40,
        onlineDevices: 0,
        notReachableDevices: 25,
        criticalOfflineDevices: 10,
        unknownDevices: 5,
        onlinePercentage: 0,
        notReachablePercentage: 62.5,
        criticalOfflinePercentage: 25,
      }),
    )
    expect(result.score).toBe(0)
    expect(result.label).toBe('Critical')
  })

  it('uses empty-state Excellent/100 when there are no monitored devices', () => {
    expect(computeNetworkHealth(null)).toEqual({ score: 100, label: 'Excellent' })
    expect(computeNetworkHealth(undefined)).toEqual({ score: 100, label: 'Excellent' })
    expect(
      computeNetworkHealth(
        summary({
          totalDevices: 0,
          onlineDevices: 0,
          monitoredDevices: 0,
        }),
      ),
    ).toEqual({ score: 100, label: 'Excellent' })
  })

  it('handles mixed NetPulse status vocabulary without double-penalizing', () => {
    // 7 Online, 54 Not Reachable — must reflect ~11%, not collapse to 0 via penalties
    const result = computeNetworkHealth(
      summary({
        totalDevices: 61,
        onlineDevices: 7,
        notReachableDevices: 54,
        criticalOfflineDevices: 0,
        unknownDevices: 0,
        monitoredDevices: 61,
        onlinePercentage: 11.48,
        notReachablePercentage: 88.52,
        criticalOfflinePercentage: 0,
      }),
    )
    expect(result.score).toBe(11)
    expect(result.label).toBe('Critical')
  })

  it('prefers device counts over percentage fields (API contract)', () => {
    const result = computeNetworkHealth(
      summary({
        totalDevices: 200,
        onlineDevices: 180,
        // Intentionally wrong/stale percentage — counts win
        onlinePercentage: 0,
        notReachablePercentage: 100,
      }),
    )
    expect(result.score).toBe(90)
  })

  it('keeps numeric score when percentage arrives as a string', () => {
    const result = computeNetworkHealth(
      summary({
        totalDevices: 10 as unknown as number,
        onlineDevices: undefined as unknown as number,
        onlinePercentage: '80' as unknown as number,
      }),
    )
    expect(result.score).toBe(80)
    expect(typeof result.score).toBe('number')
  })

  it('does not silently treat missing online metrics as 0% Critical', () => {
    const result = computeNetworkHealth(
      summary({
        totalDevices: 12,
        onlineDevices: undefined as unknown as number,
        onlinePercentage: undefined as unknown as number,
      }),
    )
    expect(result).toEqual({ score: 100, label: 'Excellent' })
  })

  it('falls back to onlinePercentage when counts are incomplete', () => {
    const result = computeNetworkHealth(
      summary({
        totalDevices: 100,
        onlineDevices: undefined as unknown as number,
        onlinePercentage: 73.2,
      }),
    )
    expect(result.score).toBe(73)
    expect(result.label).toBe('Good')
  })
})
