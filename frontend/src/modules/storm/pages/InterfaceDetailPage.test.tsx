import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { InterfaceDetailPage } from '@/modules/storm/pages/InterfaceDetailPage'
import type { StormIncident } from '@/types'

const useAuth = vi.fn()
const useStormIncidentsQuery = vi.fn()
const useInterfaceMutations = vi.fn()

vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => useAuth(),
}))

vi.mock('@/hooks/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/queries')>()
  return {
    ...actual,
    useStormIncidentsQuery: (...args: unknown[]) => useStormIncidentsQuery(...args),
    useInterfaceMutations: () => useInterfaceMutations(),
    useDeviceInterfacesQuery: () => ({
      data: {
        hostname: 'sw1',
        ipAddress: '10.0.0.1',
        data: [
          {
            name: 'Gi1/0/10',
            hostname: 'sw1',
            ipAddress: '10.0.0.1',
            adminStatus: 'up',
            operStatus: 'up',
          },
        ],
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    }),
    useDeviceInterfaceStatsQuery: () => ({
      data: [
        {
          interfaceName: 'Gi1/0/10',
          hostname: 'sw1',
          ipAddress: '10.0.0.1',
          utilization: 1,
        },
      ],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    }),
    useInterfaceHistoryQuery: () => ({
      data: {
        data: [
          {
            timestamp: '2026-09-01T10:00:00.000Z',
            utilization: 1,
            broadcastPackets: 0,
            multicastPackets: 0,
            inputErrors: 0,
            outputErrors: 0,
            discards: 0,
            rxBytes: 0,
            txBytes: 0,
          },
        ],
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    }),
    useInterfaceRiskQuery: () => ({
      data: { data: null },
      isLoading: false,
      error: null,
    }),
    useMitigationDetailQuery: () => ({
      data: null,
      isLoading: false,
      error: null,
    }),
    useRecoveryDetailQuery: () => ({
      data: null,
      isLoading: false,
      error: null,
    }),
  }
})

function incident(status: StormIncident['status']): StormIncident {
  return {
    incidentId: 'storm-2026-000100',
    deviceId: '507f1f77bcf86cd799439011',
    interface: 'Gi1/0/10',
    status,
    severity: 'HIGH',
    createdAt: '2026-09-01T10:00:00.000Z',
    updatedAt: '2026-09-01T10:00:00.000Z',
  }
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/interfaces/507f1f77bcf86cd799439011/Gi1%2F0%2F10']}>
        <Routes>
          <Route
            path="/interfaces/:deviceId/:interfaceName"
            element={<InterfaceDetailPage />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('InterfaceDetailPage manual recover visibility', () => {
  beforeEach(() => {
    useAuth.mockReturnValue({
      isAdmin: false,
      isUser: true,
    })
    useInterfaceMutations.mockReturnValue({
      collectDevice: { isPending: false, mutate: vi.fn() },
      manualShutdown: { isPending: false, mutate: vi.fn() },
      manualRecover: { isPending: false, mutate: vi.fn() },
      setMonitoring: { isPending: false, mutate: vi.fn() },
    })
    useStormIncidentsQuery.mockReturnValue({
      data: { data: [incident('MITIGATED')] },
      isLoading: false,
      error: null,
    })
  })

  it.each([
  ['MITIGATED'],
  ['OPEN'],
  ['MONITORING'],
  ['RECOVERED'],
  ['MITIGATION_FAILED'],
] as const)('shows Manual recover when latest incident status is %s', (status) => {
    useStormIncidentsQuery.mockReturnValue({
      data: { data: [incident(status)] },
      isLoading: false,
      error: null,
    })

    renderPage()

    expect(screen.getByRole('button', { name: 'Manual recover' })).toBeInTheDocument()
  })

  it('shows Manual recover when there is no incident', () => {
    useStormIncidentsQuery.mockReturnValue({
      data: { data: [] },
      isLoading: false,
      error: null,
    })

    renderPage()

    expect(screen.getByRole('button', { name: 'Manual recover' })).toBeInTheDocument()
    expect(
      screen.getByText(
        /Manual recover is always available\. Recovery is only permitted when the incident is in MITIGATED status\./,
      ),
    ).toBeInTheDocument()
  })

  it('hides Manual recover for unauthorized users', () => {
    useAuth.mockReturnValue({
      isAdmin: false,
      isUser: false,
    })

    renderPage()

    expect(screen.queryByRole('button', { name: 'Manual recover' })).not.toBeInTheDocument()
  })

  it('disables Manual recover and shows Recovering… while pending', () => {
    useInterfaceMutations.mockReturnValue({
      collectDevice: { isPending: false, mutate: vi.fn() },
      manualShutdown: { isPending: false, mutate: vi.fn() },
      manualRecover: { isPending: true, mutate: vi.fn() },
      setMonitoring: { isPending: false, mutate: vi.fn() },
    })

    renderPage()

    const button = screen.getByRole('button', { name: 'Recovering…' })
    expect(button).toBeDisabled()
  })
})
