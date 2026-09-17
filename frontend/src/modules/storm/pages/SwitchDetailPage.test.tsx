import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes, useSearchParams } from 'react-router-dom'
import { SwitchDetailPage } from '@/modules/storm/pages/SwitchDetailPage'
import type { SwitchHardwareCurrent } from '@/api/switchHardwareService'

const useAuth = vi.fn()
const useDeviceQuery = vi.fn()
const useSwitchHardwareQuery = vi.fn()
const useDeviceInterfacesQuery = vi.fn()
const useDeviceRiskQuery = vi.fn()
const collectMutate = vi.fn()

vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => useAuth(),
}))

vi.mock('@/hooks/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/queries')>()
  return {
    ...actual,
    useDeviceQuery: (...args: unknown[]) => useDeviceQuery(...args),
    useSwitchHardwareQuery: (...args: unknown[]) => useSwitchHardwareQuery(...args),
    useDeviceInterfacesQuery: (...args: unknown[]) => useDeviceInterfacesQuery(...args),
    useDeviceRiskQuery: (...args: unknown[]) => useDeviceRiskQuery(...args),
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
    fans: { count: 2, healthyCount: 2, failedCount: 0, items: [] },
    powerSupplies: { count: 1, healthyCount: 1, failedCount: 0, redundancy: null, items: [] },
    cpu: { utilizationPercent: 12, status: 'healthy' },
    memory: { utilizationPercent: 40, status: 'healthy' },
    hardwareAlarms: [],
    availability: { snmp: 'available', ssh: 'available' },
    lastSuccessfulCollectionAt: '2026-09-14T08:00:00.000Z',
    lastAttemptedCollectionAt: '2026-09-14T08:00:00.000Z',
    lastError: null,
  }
}

const DEVICE_ID = '507f1f77bcf86cd799439011'

// Stands in for the real Network Topology page so redirects away from the
// Topology tab can be asserted without pulling in TopologyPage's own data
// fetching. Renders the `switch` search param so tests can confirm both
// that the redirect happened and which device it's scoped to.
function TopologyPageStub() {
  const [params] = useSearchParams()
  return <div data-testid="topology-page">{params.get('switch')}</div>
}

function renderPage(initialPath = `/switches/${DEVICE_ID}`) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/switches/:deviceId" element={<SwitchDetailPage />} />
          <Route path="/topology" element={<TopologyPageStub />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('SwitchDetailPage', () => {
  beforeEach(() => {
    collectMutate.mockReset()
    useAuth.mockReturnValue({ isAdmin: false })
    useDeviceQuery.mockReturnValue({
      data: {
        _id: DEVICE_ID,
        hostname: 'sw1',
        ipAddress: '10.0.0.1',
        deviceType: 'Managed Switch',
        status: 'Online',
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    useSwitchHardwareQuery.mockReturnValue({
      data: hardwareFixture(),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    useDeviceInterfacesQuery.mockReturnValue({
      data: {
        data: [
          {
            _id: 'if1',
            deviceId: DEVICE_ID,
            name: 'Gi1/0/1',
            description: '',
            adminStatus: 'up',
            operStatus: 'up',
            mode: 'access',
            portMode: 'access',
            isAccess: true,
            isTrunk: false,
            isUplink: false,
            isInfrastructure: false,
            isManagement: false,
            isProtected: true,
            monitoringEnabled: true,
            accessVlan: 10,
            voiceVlan: null,
            nativeVlan: null,
            allowedVlans: [],
            vlan: 10,
            speed: '1000',
            speedMbps: 1000,
            duplex: 'full',
            macAddress: '',
            vendor: 'cisco',
            collectionMethod: 'ssh',
            lastUpdated: null,
          },
        ],
        total: 1,
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    useDeviceRiskQuery.mockReturnValue({ data: { data: [] }, isLoading: false })
  })

  it('defaults to the Overview tab and shows KPIs + device info', () => {
    renderPage()
    expect(screen.getByRole('heading', { name: 'sw1' })).toBeInTheDocument()
    expect(screen.getByText('Overall Health')).toBeInTheDocument()
    expect(screen.getByText('Active Outage')).toBeInTheDocument()
    expect(screen.getByText('Device')).toBeInTheDocument()
    // Hardware-tab-only content must not be mounted yet (lazy tab loading).
    expect(screen.queryByText('Hardware Inventory')).not.toBeInTheDocument()
  })

  it('deep-links directly into the Hardware tab via ?tab=hardware', () => {
    renderPage(`/switches/${DEVICE_ID}?tab=hardware`)
    expect(screen.getByText('Hardware Inventory')).toBeInTheDocument()
    expect(screen.getByText('48 C')).toBeInTheDocument()
  })

  it('falls back to Overview for an invalid ?tab value', () => {
    renderPage(`/switches/${DEVICE_ID}?tab=not-a-real-tab`)
    expect(screen.getByText('Overall Health')).toBeInTheDocument()
    expect(screen.queryByText('Hardware Inventory')).not.toBeInTheDocument()
  })

  it('clicking the Interfaces tab shows the device-scoped interface table', () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Interfaces' }))
    expect(screen.getByText('Gi1/0/1')).toBeInTheDocument()
    expect(useDeviceInterfacesQuery).toHaveBeenCalledWith(DEVICE_ID, { limit: 500 })
  })

  it('shows Collect Now only for admins', () => {
    useAuth.mockReturnValue({ isAdmin: true })
    renderPage()
    expect(screen.getByRole('button', { name: 'Collect Now' })).toBeInTheDocument()
  })

  it('hides Collect Now for non-admins', () => {
    useAuth.mockReturnValue({ isAdmin: false })
    renderPage()
    expect(screen.queryByRole('button', { name: 'Collect Now' })).not.toBeInTheDocument()
  })

  // The hub no longer renders its own separate topology view — clicking
  // Topology (or landing on ?tab=topology) must redirect to the real
  // Network Topology page's Level 1 neighborhood view for this switch,
  // instead of a second disconnected rendering of just this switch.
  it('clicking the Topology tab redirects to the Network Topology page for this switch', () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Topology' }))
    expect(screen.getByTestId('topology-page')).toHaveTextContent(DEVICE_ID)
  })

  it('deep-linking to ?tab=topology also redirects to the Network Topology page', () => {
    renderPage(`/switches/${DEVICE_ID}?tab=topology`)
    expect(screen.getByTestId('topology-page')).toHaveTextContent(DEVICE_ID)
  })

  // Regression: must match InterfacesEnterprisePage.tsx's normalizeRisk() —
  // missing/empty/unrecognized severity normalizes to UNKNOWN, never a
  // fabricated known level, so the same risk record never shows a different
  // verdict on this page than on the fleet Interfaces page.
  describe('interface risk severity normalization matches the fleet page', () => {
    it.each([
      ['CRITICAL', 'CRITICAL'],
      ['HIGH', 'HIGH'],
      ['MEDIUM', 'MEDIUM'],
      ['LOW', 'LOW'],
      ['UNKNOWN', 'UNKNOWN'],
      [undefined, 'UNKNOWN'],
      [null, 'UNKNOWN'],
      ['', 'UNKNOWN'],
      ['bogus', 'UNKNOWN'],
    ])('severity %p normalizes to %p', (input, expected) => {
      useDeviceRiskQuery.mockReturnValue({
        data: {
          data: [{ deviceId: DEVICE_ID, interface: 'Gi1/0/1', severity: input }],
        },
        isLoading: false,
      })
      renderPage(`/switches/${DEVICE_ID}?tab=interfaces`)
      expect(screen.getByText(expected)).toBeInTheDocument()
    })
  })

  // Regression for the confirmed BLOCKER: SwitchDetailPage must use the
  // device-scoped risk query (GET /api/storm/risk/:deviceId), never the
  // generic fleet-wide risk query (GET /api/storm/risk?deviceId=...), so a
  // risk record belonging to a different switch can never appear here.
  it('requests risk data via the device-scoped hook, not the generic fleet-wide one', () => {
    renderPage()
    expect(useDeviceRiskQuery).toHaveBeenCalledWith(DEVICE_ID, { limit: 500 })
  })

  it('never displays another switch’s risk severity for a same-named interface', () => {
    // Simulates the device-scoped endpoint for Switch A: only Switch A's own
    // Gi1/0/1 record (HIGH) is returned. Switch B's Gi1/0/1 (CRITICAL) would
    // have leaked in under the old fleet-wide ?deviceId= query.
    useDeviceRiskQuery.mockReturnValue({
      data: {
        data: [{ deviceId: DEVICE_ID, interface: 'Gi1/0/1', severity: 'HIGH' }],
      },
      isLoading: false,
    })
    renderPage(`/switches/${DEVICE_ID}?tab=interfaces`)
    expect(screen.getByText('HIGH')).toBeInTheDocument()
    expect(screen.queryByText('CRITICAL')).not.toBeInTheDocument()
  })

  it('Overview "Critical Storm Risk" KPI is derived from the device-scoped risk data', () => {
    useDeviceRiskQuery.mockReturnValue({
      data: {
        data: [{ deviceId: DEVICE_ID, interface: 'Gi1/0/1', severity: 'CRITICAL' }],
      },
      isLoading: false,
    })
    renderPage()
    const kpi = screen.getByText('Critical Storm Risk').closest('div')
    expect(kpi).not.toBeNull()
    expect(kpi?.textContent).toContain('1')
  })
})
