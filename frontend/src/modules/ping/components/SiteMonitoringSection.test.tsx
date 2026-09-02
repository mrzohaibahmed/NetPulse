import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SiteMonitoringSection } from '@/modules/ping/components/SiteMonitoringSection'
import type { SiteMonitoringSite } from '@/types'

const sampleSites: SiteMonitoringSite[] = [
  {
    name: 'Mill',
    isps: [
      {
        id: 'isp-1',
        name: 'Multinet',
        target: '8.8.8.8',
        location: 'Mill',
        monitor: true,
        status: 'Online',
        responseTime: 52.4,
        lastSeen: '2026-08-10T08:00:00Z',
        createdAt: '2026-08-10T08:00:00Z',
        updatedAt: '2026-08-10T08:00:00Z',
      },
      {
        id: 'isp-2',
        name: 'CyberNet',
        target: '1.1.1.1',
        location: 'Mill',
        monitor: true,
        status: 'Online',
        responseTime: 71.9,
        lastSeen: '2026-08-10T08:00:00Z',
        createdAt: '2026-08-10T08:00:00Z',
        updatedAt: '2026-08-10T08:00:00Z',
      },
      {
        id: 'isp-3',
        name: 'Wateen',
        target: '9.9.9.9',
        location: 'Mill',
        monitor: true,
        status: 'Online',
        responseTime: 42.3,
        lastSeen: '2026-08-10T08:00:00Z',
        createdAt: '2026-08-10T08:00:00Z',
        updatedAt: '2026-08-10T08:00:00Z',
      },
    ],
    servers: [
      {
        id: 'server-1',
        hostname: 'Mill Firewall',
        ipAddress: '192.168.1.10',
        deviceType: 'Server',
        status: 'Online',
        responseTime: 5.0,
        lastSeen: '2026-08-10T08:00:00Z',
        location: 'Mill',
        monitor: true,
        critical: false,
      },
    ],
  },
  {
    name: 'Karachi',
    isps: [],
    servers: [],
  },
  {
    name: 'Lahore',
    isps: [],
    servers: [],
  },
]

describe('SiteMonitoringSection', () => {
  it('renders all site panels', () => {
    render(
      <MemoryRouter>
        <SiteMonitoringSection sites={sampleSites} />
      </MemoryRouter>,
    )

    expect(screen.getByText('Mill')).toBeInTheDocument()
    expect(screen.getByText('Karachi')).toBeInTheDocument()
    expect(screen.getByText('Lahore')).toBeInTheDocument()
  })

  it('renders ISP cards and server cards independently', () => {
    render(
      <MemoryRouter>
        <SiteMonitoringSection sites={sampleSites} />
      </MemoryRouter>,
    )

    expect(screen.getByText('Multinet')).toBeInTheDocument()
    expect(screen.getByText('CyberNet')).toBeInTheDocument()
    expect(screen.getByText('Wateen')).toBeInTheDocument()
    expect(screen.getByText('Mill Firewall')).toBeInTheDocument()
    expect(screen.getByText('192.168.1.10')).toBeInTheDocument()
    expect(screen.getAllByText('ISP Connectivity')).toHaveLength(3)
    expect(screen.getAllByText('Server Monitoring')).toHaveLength(3)
  })

  it('shows empty server state when no servers configured for a site', () => {
    render(
      <MemoryRouter>
        <SiteMonitoringSection sites={sampleSites} />
      </MemoryRouter>,
    )

    expect(screen.getAllByText('No server devices configured').length).toBeGreaterThan(0)
  })
})
