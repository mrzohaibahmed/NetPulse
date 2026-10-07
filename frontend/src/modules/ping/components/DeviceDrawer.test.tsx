import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { DeviceDrawer } from '@/modules/ping/components/DeviceDrawer'
import type { ServerHardwareHealthData } from '@/types'

vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ isUser: true, isAdmin: true }),
}))

const mockUseDeviceHistoryQuery = vi.fn()
const mockUseDeviceMutations = vi.fn()
const mockUseNmapScanMutation = vi.fn()
const mockUseServerHardwareHealthQuery = vi.fn()

vi.mock('@/hooks/queries', () => ({
  useDeviceHistoryQuery: (...args: unknown[]) => mockUseDeviceHistoryQuery(...args),
  useDeviceMutations: () => mockUseDeviceMutations(),
  useNmapScanMutation: () => mockUseNmapScanMutation(),
  useServerHardwareHealthQuery: (...args: unknown[]) => mockUseServerHardwareHealthQuery(...args),
}))

describe('DeviceDrawer - HPE iLO Hardware & Freshness UI', () => {
  const baseDevice = {
    _id: 'dev-1',
    hostname: 'server-01',
    ipAddress: '10.0.0.10',
    iloAddress: 'ilo-01.local',
    deviceType: 'Server',
    critical: false,
    monitor: true,
    showOnDashboard: false,
    status: 'Online' as const,
    lastSeen: '2026-10-07T12:00:00Z',
    responseTime: 2.5,
    createdAt: '2026-10-01T00:00:00Z',
    updatedAt: '2026-10-07T12:00:00Z',
  }

  const baseHistoryResponse = {
    device: baseDevice,
    uptime: { uptimePercentage: 99.9, downtimePercentage: 0.1, totalChecks: 1000 },
    history: [],
    responseTimeTrend: [],
  }

  beforeEach(() => {
    vi.resetAllMocks()
    mockUseDeviceHistoryQuery.mockReturnValue({
      data: baseHistoryResponse,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    mockUseDeviceMutations.mockReturnValue({
      scan: { isPending: false, mutate: vi.fn() },
    })
    mockUseNmapScanMutation.mockReturnValue({
      isPending: false,
      mutate: vi.fn(),
    })
  })

  it('1. Fresh state: renders Fresh badge and collection details', () => {
    const hwData: ServerHardwareHealthData = {
      deviceId: 'dev-1',
      observedAt: '2026-10-07T12:00:00Z',
      freshness: {
        status: 'FRESH',
        dataAgeSeconds: 120,
        staleThresholdSeconds: 1800,
        isStale: false,
        observedAt: '2026-10-07T12:00:00Z',
        lastPollStatus: 'SUCCESS',
      },
      collection: {
        lastAttemptAt: '2026-10-07T12:00:00Z',
        lastSuccessAt: '2026-10-07T12:00:00Z',
        lastFailureAt: null,
        lastPollStatus: 'SUCCESS',
        consecutiveFailures: 0,
        lastError: null,
        updatedAt: '2026-10-07T12:00:00Z',
      },
      health: {
        overallHealth: 'OK',
        powerState: 'On',
        summaryReasons: [],
        subsystems: {
          processors: {
            name: 'Processors',
            status: 'OK',
            totalComponents: 2,
            healthyComponents: 2,
            warningComponents: 0,
            criticalComponents: 0,
            reasons: [],
          },
        },
      },
    }

    mockUseServerHardwareHealthQuery.mockReturnValue({
      data: hwData,
      isLoading: false,
      isError: false,
    })

    render(
      <MemoryRouter>
        <DeviceDrawer deviceId="dev-1" open={true} onOpenChange={vi.fn()} />
      </MemoryRouter>,
    )

    const hwTab = screen.getByRole('button', { name: /Hardware Health/i })
    fireEvent.click(hwTab)

    expect(screen.getAllByText('Fresh').length).toBeGreaterThan(0)
    expect(screen.getByText('iLO Telemetry & Collection Status')).toBeInTheDocument()
    expect(screen.getByText('Overall Hardware Status')).toBeInTheDocument()
    expect(screen.getByText('Processors')).toBeInTheDocument()
  })

  it('2. Stale state: renders Stale badge and stale warning banner', () => {
    const hwData: ServerHardwareHealthData = {
      deviceId: 'dev-1',
      observedAt: '2026-10-07T10:00:00Z',
      freshness: {
        status: 'STALE',
        dataAgeSeconds: 7200,
        staleThresholdSeconds: 1800,
        isStale: true,
        observedAt: '2026-10-07T10:00:00Z',
        lastPollStatus: 'SUCCESS',
      },
      collection: {
        lastAttemptAt: '2026-10-07T12:00:00Z',
        lastSuccessAt: '2026-10-07T10:00:00Z',
        lastFailureAt: '2026-10-07T12:00:00Z',
        lastPollStatus: 'FAILED',
        consecutiveFailures: 1,
        lastError: 'Connection timeout',
        updatedAt: '2026-10-07T12:00:00Z',
      },
      health: {
        overallHealth: 'OK',
        powerState: 'On',
        summaryReasons: [],
        subsystems: {},
      },
    }

    mockUseServerHardwareHealthQuery.mockReturnValue({
      data: hwData,
      isLoading: false,
      isError: false,
    })

    render(
      <MemoryRouter>
        <DeviceDrawer deviceId="dev-1" open={true} onOpenChange={vi.fn()} />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /Hardware Health/i }))

    expect(screen.getAllByText('Stale').length).toBeGreaterThan(0)
    expect(screen.getByText('Telemetry is stale')).toBeInTheDocument()
    expect(
      screen.getByText(/The hardware information shown below may not reflect the server's current state/i),
    ).toBeInTheDocument()
  })

  it('3. Failing state: renders Collection Failing badge, error alert banner and sanitized error', () => {
    const hwData: ServerHardwareHealthData = {
      deviceId: 'dev-1',
      observedAt: '2026-10-07T11:00:00Z',
      freshness: {
        status: 'FAILING',
        dataAgeSeconds: 3600,
        staleThresholdSeconds: 1800,
        isStale: true,
        observedAt: '2026-10-07T11:00:00Z',
        lastPollStatus: 'FAILED',
      },
      collection: {
        lastAttemptAt: '2026-10-07T12:00:00Z',
        lastSuccessAt: '2026-10-07T11:00:00Z',
        lastFailureAt: '2026-10-07T12:00:00Z',
        lastPollStatus: 'FAILED',
        consecutiveFailures: 3,
        lastError: 'iLO REST API HTTP 401 Unauthorized',
        updatedAt: '2026-10-07T12:00:00Z',
      },
      health: {
        overallHealth: 'OK',
        powerState: 'On',
        summaryReasons: [],
        subsystems: {},
      },
    }

    mockUseServerHardwareHealthQuery.mockReturnValue({
      data: hwData,
      isLoading: false,
      isError: false,
    })

    render(
      <MemoryRouter>
        <DeviceDrawer deviceId="dev-1" open={true} onOpenChange={vi.fn()} />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /Hardware Health/i }))

    expect(screen.getAllByText('Collection Failing').length).toBeGreaterThan(0)
    expect(screen.getByText('Hardware collection is failing')).toBeInTheDocument()
    expect(screen.getByText(/iLO REST API HTTP 401 Unauthorized/i)).toBeInTheDocument()
  })

  it('4. Never Polled state without errors: renders clean empty state', () => {
    const hwData: ServerHardwareHealthData = {
      deviceId: 'dev-1',
      observedAt: null,
      freshness: {
        status: 'NEVER_POLLED',
        dataAgeSeconds: null,
        staleThresholdSeconds: 1800,
        isStale: false,
        observedAt: null,
        lastPollStatus: null,
      },
      collection: {
        lastAttemptAt: null,
        lastSuccessAt: null,
        lastFailureAt: null,
        lastPollStatus: null,
        consecutiveFailures: 0,
        lastError: null,
        updatedAt: null,
      },
      health: null,
    }

    mockUseServerHardwareHealthQuery.mockReturnValue({
      data: hwData,
      isLoading: false,
      isError: false,
    })

    render(
      <MemoryRouter>
        <DeviceDrawer deviceId="dev-1" open={true} onOpenChange={vi.fn()} />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /Hardware Health/i }))

    expect(screen.getAllByText('Never Polled').length).toBeGreaterThan(0)
    expect(screen.getByText('Hardware telemetry has not been collected yet')).toBeInTheDocument()
    expect(screen.queryByText('Processors')).not.toBeInTheDocument()
  })

  it('5. First Poll Failure state: renders failure card, error message and no fake hardware cards', () => {
    const hwData: ServerHardwareHealthData = {
      deviceId: 'dev-1',
      observedAt: null,
      freshness: {
        status: 'NEVER_POLLED',
        dataAgeSeconds: null,
        staleThresholdSeconds: 1800,
        isStale: false,
        observedAt: null,
        lastPollStatus: 'FAILED',
      },
      collection: {
        lastAttemptAt: '2026-10-07T12:00:00Z',
        lastSuccessAt: null,
        lastFailureAt: '2026-10-07T12:00:00Z',
        lastPollStatus: 'FAILED',
        consecutiveFailures: 1,
        lastError: 'iLO connection refused (port 443 closed)',
        updatedAt: '2026-10-07T12:00:00Z',
      },
      health: null,
    }

    mockUseServerHardwareHealthQuery.mockReturnValue({
      data: hwData,
      isLoading: false,
      isError: false,
    })

    render(
      <MemoryRouter>
        <DeviceDrawer deviceId="dev-1" open={true} onOpenChange={vi.fn()} />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /Hardware Health/i }))

    expect(screen.getByText('Hardware polling is failing')).toBeInTheDocument()
    expect(screen.getByText(/iLO connection refused \(port 443 closed\)/i)).toBeInTheDocument()
    expect(screen.queryByText('Overall Hardware Status')).not.toBeInTheDocument()
  })

  it('6. Healthy + Stale: preserves OK hardware health while showing Stale freshness', () => {
    const hwData: ServerHardwareHealthData = {
      deviceId: 'dev-1',
      observedAt: '2026-10-07T08:00:00Z',
      freshness: {
        status: 'STALE',
        dataAgeSeconds: 14400,
        staleThresholdSeconds: 1800,
        isStale: true,
        observedAt: '2026-10-07T08:00:00Z',
        lastPollStatus: 'SUCCESS',
      },
      collection: {
        lastAttemptAt: '2026-10-07T08:00:00Z',
        lastSuccessAt: '2026-10-07T08:00:00Z',
        lastFailureAt: null,
        lastPollStatus: 'SUCCESS',
        consecutiveFailures: 0,
        lastError: null,
        updatedAt: '2026-10-07T08:00:00Z',
      },
      health: {
        overallHealth: 'OK',
        powerState: 'On',
        summaryReasons: [],
        subsystems: {},
      },
    }

    mockUseServerHardwareHealthQuery.mockReturnValue({
      data: hwData,
      isLoading: false,
      isError: false,
    })

    render(
      <MemoryRouter>
        <DeviceDrawer deviceId="dev-1" open={true} onOpenChange={vi.fn()} />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /Hardware Health/i }))

    expect(screen.getAllByText('OK').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Stale').length).toBeGreaterThan(0)
    expect(screen.getByText('Telemetry is stale')).toBeInTheDocument()
  })

  it('7. Critical + Fresh: keeps CRITICAL hardware health distinct from FRESH freshness', () => {
    const hwData: ServerHardwareHealthData = {
      deviceId: 'dev-1',
      observedAt: '2026-10-07T12:00:00Z',
      freshness: {
        status: 'FRESH',
        dataAgeSeconds: 60,
        staleThresholdSeconds: 1800,
        isStale: false,
        observedAt: '2026-10-07T12:00:00Z',
        lastPollStatus: 'SUCCESS',
      },
      collection: {
        lastAttemptAt: '2026-10-07T12:00:00Z',
        lastSuccessAt: '2026-10-07T12:00:00Z',
        lastFailureAt: null,
        lastPollStatus: 'SUCCESS',
        consecutiveFailures: 0,
        lastError: null,
        updatedAt: '2026-10-07T12:00:00Z',
      },
      health: {
        overallHealth: 'CRITICAL',
        powerState: 'On',
        summaryReasons: ['Fan 3 failure detected'],
        subsystems: {},
      },
    }

    mockUseServerHardwareHealthQuery.mockReturnValue({
      data: hwData,
      isLoading: false,
      isError: false,
    })

    render(
      <MemoryRouter>
        <DeviceDrawer deviceId="dev-1" open={true} onOpenChange={vi.fn()} />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /Hardware Health/i }))

    expect(screen.getAllByText('CRITICAL').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Fresh').length).toBeGreaterThan(0)
    expect(screen.getByText('Fan 3 failure detected')).toBeInTheDocument()
    expect(screen.queryByText('Telemetry is stale')).not.toBeInTheDocument()
  })

  it('8. Failure + Previous Health: collection failure does not erase last known health', () => {
    const hwData: ServerHardwareHealthData = {
      deviceId: 'dev-1',
      observedAt: '2026-10-07T11:00:00Z',
      freshness: {
        status: 'FAILING',
        dataAgeSeconds: 3600,
        staleThresholdSeconds: 1800,
        isStale: true,
        observedAt: '2026-10-07T11:00:00Z',
        lastPollStatus: 'FAILED',
      },
      collection: {
        lastAttemptAt: '2026-10-07T12:00:00Z',
        lastSuccessAt: '2026-10-07T11:00:00Z',
        lastFailureAt: '2026-10-07T12:00:00Z',
        lastPollStatus: 'FAILED',
        consecutiveFailures: 2,
        lastError: 'iLO timeout',
        updatedAt: '2026-10-07T12:00:00Z',
      },
      health: {
        overallHealth: 'OK',
        powerState: 'On',
        summaryReasons: [],
        subsystems: {},
      },
    }

    mockUseServerHardwareHealthQuery.mockReturnValue({
      data: hwData,
      isLoading: false,
      isError: false,
    })

    render(
      <MemoryRouter>
        <DeviceDrawer deviceId="dev-1" open={true} onOpenChange={vi.fn()} />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /Hardware Health/i }))

    expect(screen.getAllByText('OK').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Collection Failing').length).toBeGreaterThan(0)
    expect(screen.getByText('Hardware collection is failing')).toBeInTheDocument()
  })

  it('9. Loading state: renders loading indicator', () => {
    mockUseServerHardwareHealthQuery.mockReturnValue({
      data: null,
      isLoading: true,
      isError: false,
    })

    render(
      <MemoryRouter>
        <DeviceDrawer deviceId="dev-1" open={true} onOpenChange={vi.fn()} />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /Hardware Health/i }))

    expect(screen.getByText(/Loading hardware health telemetry…/i)).toBeInTheDocument()
  })

  it('10. API Error state: renders error state without mislabeling as iLO collection failure', () => {
    mockUseServerHardwareHealthQuery.mockReturnValue({
      data: null,
      isLoading: false,
      isError: true,
      error: new Error('Network Error 500'),
      refetch: vi.fn(),
    })

    render(
      <MemoryRouter>
        <DeviceDrawer deviceId="dev-1" open={true} onOpenChange={vi.fn()} />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /Hardware Health/i }))

    expect(screen.getByText('Network Error 500')).toBeInTheDocument()
    expect(screen.queryByText('Hardware collection is failing')).not.toBeInTheDocument()
  })

  it('11. Non-server device: Hardware Health tab is hidden', () => {
    mockUseDeviceHistoryQuery.mockReturnValue({
      data: {
        ...baseHistoryResponse,
        device: { ...baseDevice, deviceType: 'Switch' },
      },
      isLoading: false,
      error: null,
    })

    render(
      <MemoryRouter>
        <DeviceDrawer deviceId="dev-1" open={true} onOpenChange={vi.fn()} />
      </MemoryRouter>,
    )

    expect(screen.queryByRole('button', { name: /Hardware Health/i })).not.toBeInTheDocument()
  })
})
