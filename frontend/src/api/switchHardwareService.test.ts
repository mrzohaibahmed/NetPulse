import { describe, expect, it, vi } from 'vitest'
import {
  collectSwitchHardware,
  getSwitchHardware,
  getSwitchHardwareEvents,
  getSwitchHardwareHistory,
  getSwitchOutage,
  getSwitchOutages,
} from '@/api/switchHardwareService'
import { apiRequest } from '@/shared/api/client'

vi.mock('@/shared/api/client', () => ({
  apiRequest: vi.fn(),
}))

describe('switchHardwareService', () => {
  it('calls correct endpoints', async () => {
    const mock = vi.mocked(apiRequest)
    mock.mockResolvedValue({ success: true })

    await getSwitchHardware('abc')
    expect(mock).toHaveBeenCalledWith('/api/switches/abc/hardware')

    await getSwitchHardwareHistory('abc', { page: 1, limit: 50 })
    expect(mock).toHaveBeenCalledWith('/api/switches/abc/hardware/history?page=1&limit=50')

    await getSwitchHardwareEvents('abc', { page: 1, limit: 25 })
    expect(mock).toHaveBeenCalledWith('/api/switches/abc/hardware/events?page=1&limit=25')

    await getSwitchOutages('abc', { page: 1, limit: 10 })
    expect(mock).toHaveBeenCalledWith('/api/switches/abc/outages?page=1&limit=10')

    await getSwitchOutage('abc', 'inc1')
    expect(mock).toHaveBeenCalledWith('/api/switches/abc/outages/inc1')

    await collectSwitchHardware('abc')
    expect(mock).toHaveBeenCalledWith('/api/switches/abc/hardware/collect', {
      method: 'POST',
      body: {},
      timeoutMs: 60_000,
    })
  })
})
