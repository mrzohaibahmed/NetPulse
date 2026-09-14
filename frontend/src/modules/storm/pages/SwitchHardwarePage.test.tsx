import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { SwitchHardwarePage } from '@/modules/storm/pages/SwitchHardwarePage'

vi.mock('@/utils/fetchAllPages', () => ({
  fetchAllListPages: vi.fn(async () => ({
    data: [
      {
        _id: '507f1f77bcf86cd799439011',
        hostname: 'sw1',
        ipAddress: '10.0.0.1',
        deviceType: 'Managed Switch',
        vendor: 'Cisco',
        status: 'Online',
        credentials: { sshVendor: 'cisco_ios' },
      },
    ],
    total: 1,
  })),
}))

vi.mock('@/hooks/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/queries')>()
  return {
    ...actual,
    useBatchedSwitchHardware: () => ({
      data: new Map([
        [
          '507f1f77bcf86cd799439011',
          {
            deviceId: '507f1f77bcf86cd799439011',
            overallHealth: 'healthy',
            platform: 'IOS',
            inventory: { model: 'WS-C2960' },
            temperature: { status: 'healthy', sensors: [] },
            fans: { count: 2, failedCount: 0 },
            powerSupplies: { count: 1, failedCount: 0 },
            cpu: { utilizationPercent: 10 },
            memory: { utilizationPercent: 30 },
            hardwareAlarms: [],
            lastSuccessfulCollectionAt: '2026-09-14T08:00:00.000Z',
          },
        ],
      ]),
      isLoading: false,
      refetch: vi.fn(),
    }),
  }
})

describe('SwitchHardwarePage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders fleet KPIs and switch row', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <SwitchHardwarePage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(await screen.findByText('Hardware Health')).toBeInTheDocument()
    expect(await screen.findByText('sw1')).toBeInTheDocument()
    expect(screen.getByText('10.0.0.1')).toBeInTheDocument()
    expect(screen.getByText('Total Switches')).toBeInTheDocument()
  })
})
