import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { SwitchHardwareDetailPage } from '@/modules/storm/pages/SwitchHardwareDetailPage'
import type { SwitchHardwareCurrent } from '@/api/switchHardwareService'

const useAuth = vi.fn()
const useSwitchHardwareQuery = vi.fn()
const useDeviceQuery = vi.fn()
const collectMutate = vi.fn()

vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => useAuth(),
}))

vi.mock('@/hooks/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/queries')>()
  return {
    ...actual,
    useAuth: undefined,
    useDeviceQuery: (...args: unknown[]) => useDeviceQuery(...args),
    useSwitchHardwareQuery: (...args: unknown[]) => useSwitchHardwareQuery(...args),
    useSwitchHardwareHistoryQuery: () => ({
      data: { items: [], pagination: { page: 1, limit: 100, total: 0, totalPages: 0 } },
      isLoading: false,
      refetch: vi.fn(),
    }),
    useSwitchHardwareEventsQuery: () => ({
      data: { items: [], pagination: { page: 1, limit: 25, total: 0, totalPages: 0 } },
      isLoading: false,
      refetch: vi.fn(),
    }),
    useSwitchHardwareOutagesQuery: () => ({
      data: { items: [], pagination: { page: 1, limit: 10, total: 0, totalPages: 0 } },
      isLoading: false,
      refetch: vi.fn(),
    }),
    useSwitchHardwareOutageQuery: () => ({
      data: null,
      isLoading: false,
    }),
    useSwitchHardwareCollectMutation: () => ({
      isPending: false,
      mutate: collectMutate,
    }),
    useSettingsQuery: () => ({
      data: { switchHardwareMonitoringEnabled: true },
      isLoading: false,
    }),
    useSettingsMutation: () => ({
      isPending: false,
      mutate: vi.fn(),
    }),
  }
})

function hardwareFixture(): SwitchHardwareCurrent {
  return {
    deviceId: '507f1f77bcf86cd799439011',
    vendor: 'Cisco',
    platform: 'IOS',
    overallHealth: 'warning',
    collectionStatus: 'success',
    inventory: {
      model: 'WS-C2960',
      serialNumber: 'FCW123',
      productId: 'WS-C2960',
      firmwareVersion: '15.2',
      iosVersion: '15.2',
      hostname: 'sw1',
      uptime: '1 day',
      bootReason: null,
      chassis: [],
      modules: [],
    },
    temperature: {
      status: 'warning',
      sensors: [
        {
          name: 'Inlet',
          value: 48,
          unit: 'C',
          status: 'warning',
          warningThreshold: 45,
          criticalThreshold: 55,
          source: 'snmp',
        },
      ],
    },
    fans: {
      count: 2,
      healthyCount: 2,
      failedCount: 0,
      items: [{ name: 'Fan 1', status: 'healthy', speedRpm: null, source: 'ssh' }],
    },
    powerSupplies: {
      count: 1,
      healthyCount: 1,
      failedCount: 0,
      redundancy: null,
      items: [{ name: 'PS1', status: 'healthy', source: 'ssh' }],
    },
    cpu: { utilizationPercent: 12, status: 'healthy' },
    memory: { utilizationPercent: 40, status: 'healthy' },
    hardwareAlarms: [],
    availability: { snmp: 'available', ssh: 'available' },
    lastSuccessfulCollectionAt: '2026-09-14T08:00:00.000Z',
    lastAttemptedCollectionAt: '2026-09-14T08:00:00.000Z',
    lastError: null,
  }
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/switches/507f1f77bcf86cd799439011/hardware']}>
        <Routes>
          <Route path="/switches/:deviceId/hardware" element={<SwitchHardwareDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('SwitchHardwareDetailPage', () => {
  beforeEach(() => {
    collectMutate.mockReset()
    useDeviceQuery.mockReturnValue({
      data: {
        _id: '507f1f77bcf86cd799439011',
        hostname: 'sw1',
        ipAddress: '10.0.0.1',
        status: 'Online',
      },
      isLoading: false,
      error: null,
    })
    useSwitchHardwareQuery.mockReturnValue({
      data: hardwareFixture(),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
  })

  it('renders temperature and inventory for an authenticated user', () => {
    useAuth.mockReturnValue({ isAdmin: false })
    renderPage()
    expect(screen.getByText('Temperature')).toBeInTheDocument()
    expect(screen.getByText('Hardware Inventory')).toBeInTheDocument()
    expect(screen.getByText('48 C')).toBeInTheDocument()
    expect(screen.queryByText('Collect Now')).not.toBeInTheDocument()
    expect(screen.queryByText(/password/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/community/i)).not.toBeInTheDocument()
  })

  it('shows Collect Now for admins', () => {
    useAuth.mockReturnValue({ isAdmin: true })
    renderPage()
    expect(screen.getByRole('button', { name: 'Collect Now' })).toBeInTheDocument()
  })

  it('shows unavailable state when hardware is null', () => {
    useAuth.mockReturnValue({ isAdmin: false })
    useSwitchHardwareQuery.mockReturnValue({
      data: null,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    renderPage()
    expect(screen.getByText('Hardware data unavailable')).toBeInTheDocument()
  })
})
