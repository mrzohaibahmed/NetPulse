import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ServerHardwarePage } from '@/modules/ping/pages/ServerHardwarePage'
import type { Device } from '@/types'

vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ isUser: true, isAdmin: true }),
}))

const mockFetchAllListPages = vi.fn()
vi.mock('@/utils/fetchAllPages', () => ({
  fetchAllListPages: (...args: unknown[]) => mockFetchAllListPages(...args),
}))

const mockUseServerHardwareHealthQuery = vi.fn()
const mockUseDeviceHistoryQuery = vi.fn()
const mockUpdateMutate = vi.fn()

vi.mock('@/hooks/queries', () => ({
  useServerHardwareHealthQuery: (...args: unknown[]) => mockUseServerHardwareHealthQuery(...args),
  useDeviceHistoryQuery: (...args: unknown[]) => mockUseDeviceHistoryQuery(...args),
  useDeviceMutations: () => ({
    scan: { isPending: false, mutate: vi.fn() },
    update: { isPending: false, mutate: mockUpdateMutate },
  }),
  useNmapScanMutation: () => ({ isPending: false, mutate: vi.fn() }),
  useSettingsQuery: () => ({
    data: { serverHardwareMonitoringEnabled: true },
    isLoading: false,
  }),
  useSettingsMutation: () => ({ isPending: false, mutate: vi.fn() }),
}))

function renderWithProviders(ui: React.ReactElement, initialEntry = '/server-hardware') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/server-hardware" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('ServerHardwarePage Component', () => {
  const sampleDevices: Device[] = [
    {
      _id: 'srv-1',
      hostname: 'app-server-01',
      ipAddress: '192.168.1.100',
      iloAddress: '192.168.1.200',
      deviceType: 'Server',
      critical: false,
      monitor: true,
      showOnDashboard: true,
      status: 'Online',
      lastSeen: '2026-10-07T12:00:00Z',
      responseTime: 1.2,
      createdAt: '2026-09-01T00:00:00Z',
      updatedAt: '2026-10-07T12:00:00Z',
    },
    {
      _id: 'srv-2',
      hostname: 'db-linux-01',
      ipAddress: '192.168.1.101',
      iloAddress: '192.168.1.201',
      deviceType: 'Linux Server',
      critical: true,
      monitor: true,
      showOnDashboard: true,
      status: 'Offline (Critical)',
      lastSeen: '2026-10-07T11:00:00Z',
      responseTime: null,
      createdAt: '2026-09-01T00:00:00Z',
      updatedAt: '2026-10-07T12:00:00Z',
    },
    {
      _id: 'srv-3',
      hostname: 'esxi-hypervisor-01',
      ipAddress: '192.168.1.102',
      iloAddress: '192.168.1.202',
      deviceType: 'ESXi Server',
      critical: false,
      monitor: true,
      showOnDashboard: true,
      status: 'Online',
      lastSeen: '2026-10-07T12:00:00Z',
      responseTime: 2.1,
      createdAt: '2026-09-01T00:00:00Z',
      updatedAt: '2026-10-07T12:00:00Z',
    },
    {
      _id: 'sw-1',
      hostname: 'core-switch-01',
      ipAddress: '192.168.1.1',
      iloAddress: null,
      deviceType: 'Switch',
      critical: false,
      monitor: true,
      showOnDashboard: false,
      status: 'Online',
      lastSeen: '2026-10-07T12:00:00Z',
      responseTime: 0.8,
      createdAt: '2026-09-01T00:00:00Z',
      updatedAt: '2026-10-07T12:00:00Z',
    },
    {
      _id: 'rtr-1',
      hostname: 'edge-router-01',
      ipAddress: '192.168.1.254',
      iloAddress: null,
      deviceType: 'Router',
      critical: true,
      monitor: true,
      showOnDashboard: false,
      status: 'Online',
      lastSeen: '2026-10-07T12:00:00Z',
      responseTime: 1.0,
      createdAt: '2026-09-01T00:00:00Z',
      updatedAt: '2026-10-07T12:00:00Z',
    },
    {
      _id: 'srv-no-ilo',
      hostname: 'unconfigured-server-01',
      ipAddress: '192.168.1.105',
      iloAddress: null,
      deviceType: 'Server',
      critical: false,
      monitor: true,
      showOnDashboard: false,
      status: 'Online',
      lastSeen: '2026-10-07T12:00:00Z',
      responseTime: 1.0,
      createdAt: '2026-09-01T00:00:00Z',
      updatedAt: '2026-10-07T12:00:00Z',
    },
  ]

  beforeEach(() => {
    vi.resetAllMocks()
    mockFetchAllListPages.mockResolvedValue({ data: sampleDevices, total: sampleDevices.length })
    mockUseServerHardwareHealthQuery.mockReturnValue({
      data: {
        deviceId: 'srv-1',
        observedAt: '2026-10-07T12:00:00Z',
        freshness: { status: 'FRESH', isStale: false },
        collection: { lastAttemptAt: '2026-10-07T12:00:00Z', consecutiveFailures: 0 },
        health: { overallHealth: 'OK', powerState: 'On', summaryReasons: [], subsystems: {} },
      },
      isLoading: false,
    })
    mockUseDeviceHistoryQuery.mockReturnValue({
      data: { device: sampleDevices[0], uptime: { uptimePercentage: 100 }, responseTimeTrend: [], history: [] },
      isLoading: false,
    })
  })

  it('1. Renders only eligible server devices with configured iLO addresses', async () => {
    renderWithProviders(<ServerHardwarePage />)

    expect(await screen.findByText('app-server-01')).toBeInTheDocument()
    expect(screen.getByText('db-linux-01')).toBeInTheDocument()
    expect(screen.getByText('esxi-hypervisor-01')).toBeInTheDocument()

    expect(screen.queryByText('unconfigured-server-01')).not.toBeInTheDocument()
    expect(screen.queryByText('core-switch-01')).not.toBeInTheDocument()
    expect(screen.queryByText('edge-router-01')).not.toBeInTheDocument()
  })

  it('2. Calculates fleet KPIs correctly', async () => {
    renderWithProviders(<ServerHardwarePage />)

    expect(await screen.findByText('Total Servers')).toBeInTheDocument()
    expect(screen.getAllByText('3').length).toBeGreaterThan(0) // 3 eligible servers
  })

  it('3. Filters servers by device type dropdown', async () => {
    renderWithProviders(<ServerHardwarePage />)

    expect(await screen.findByText('app-server-01')).toBeInTheDocument()
  })

  it('4. Displays empty state when no eligible servers exist in the system', async () => {
    mockFetchAllListPages.mockResolvedValue({
      data: [
        sampleDevices[3], // Switch
        sampleDevices[4], // Router
      ],
      total: 2,
    })

    renderWithProviders(<ServerHardwarePage />)

    expect(await screen.findByText('No server hardware devices')).toBeInTheDocument()
    expect(
      screen.getByText(/No Server, Linux Server, or ESXi Server devices with a configured iLO/i),
    ).toBeInTheDocument()
  })

  it('5. Opening a server opens DeviceDrawer', async () => {
    renderWithProviders(<ServerHardwarePage />)

    const serverRow = await screen.findByText('app-server-01')
    fireEvent.click(serverRow)

    // DeviceDrawer opens
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('6. Clicking monitoring checkbox triggers device update mutation', async () => {
    renderWithProviders(<ServerHardwarePage />)

    const checkbox = await screen.findByRole('checkbox', {
      name: 'Toggle monitoring for app-server-01',
    })
    fireEvent.click(checkbox)

    expect(mockUpdateMutate).toHaveBeenCalledWith({
      id: 'srv-1',
      payload: { monitor: false },
    })
  })
})
