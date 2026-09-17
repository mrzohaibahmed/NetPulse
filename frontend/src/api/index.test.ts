import { describe, expect, it, vi } from 'vitest'
import { getDeviceRisk, getRiskResults } from '@/api'
import { apiRequest } from '@/shared/api/client'

vi.mock('@/shared/api/client', () => ({
  apiRequest: vi.fn(),
}))

describe('risk API scoping', () => {
  it('getDeviceRisk requests the device-scoped path, not a deviceId query param', async () => {
    const mock = vi.mocked(apiRequest)
    mock.mockResolvedValue({ success: true })

    await getDeviceRisk('switch-a', { limit: 500 })
    expect(mock).toHaveBeenCalledWith('/api/storm/risk/switch-a?limit=500')
  })

  it('getRiskResults (fleet-wide) never scopes by device path', async () => {
    const mock = vi.mocked(apiRequest)
    mock.mockResolvedValue({ success: true })

    await getRiskResults({ limit: 500 })
    expect(mock).toHaveBeenCalledWith('/api/storm/risk?limit=500')
  })
})
