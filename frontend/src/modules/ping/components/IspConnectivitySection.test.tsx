import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  IspConnectivitySection,
  ispLatencyLabel,
  normalizeIspSlots,
} from '@/modules/ping/components/IspConnectivitySection'
import type { IspConnection } from '@/types'

const sampleIsps: IspConnection[] = [
  {
    id: 'isp-1',
    name: 'Primary Link',
    target: '8.8.8.8',
    monitor: true,
    status: 'Online',
    responseTime: 18,
    lastSeen: '2026-08-10T08:00:00Z',
    createdAt: '2026-08-10T08:00:00Z',
    updatedAt: '2026-08-10T08:00:00Z',
  },
  {
    id: 'isp-2',
    name: 'Backup Link',
    target: '1.1.1.1',
    monitor: true,
    status: 'Offline',
    responseTime: null,
    lastSeen: '2026-08-10T07:50:00Z',
    createdAt: '2026-08-10T08:00:00Z',
    updatedAt: '2026-08-10T08:00:00Z',
  },
  {
    id: 'isp-3',
    name: 'Tertiary Link',
    target: '9.9.9.9',
    monitor: false,
    status: 'Unknown',
    responseTime: null,
    lastSeen: null,
    createdAt: '2026-08-10T08:00:00Z',
    updatedAt: '2026-08-10T08:00:00Z',
  },
]

describe('normalizeIspSlots', () => {
  it('always returns exactly 3 ISP cards worth of data', () => {
    expect(normalizeIspSlots(sampleIsps)).toHaveLength(3)
    expect(normalizeIspSlots([])).toHaveLength(3)
  })
})

describe('ispLatencyLabel', () => {
  it('shows latency for online ISP', () => {
    expect(ispLatencyLabel(sampleIsps[0])).toBe('18.0 ms')
  })

  it('shows dash for offline ISP', () => {
    expect(ispLatencyLabel(sampleIsps[1])).toBe('—')
  })
})

describe('IspConnectivitySection', () => {
  it('renders exactly 3 ISP cards', () => {
    render(<IspConnectivitySection isps={sampleIsps} />)
    expect(screen.getByText('ISP Connectivity')).toBeInTheDocument()
    expect(screen.getByText('Primary Link')).toBeInTheDocument()
    expect(screen.getByText('Backup Link')).toBeInTheDocument()
    expect(screen.getByText('Tertiary Link')).toBeInTheDocument()
    expect(screen.getAllByText(/Last seen:/)).toHaveLength(3)
  })

  it('shows online latency for reachable ISP', () => {
    render(<IspConnectivitySection isps={sampleIsps} />)
    expect(screen.getByText('18.0 ms')).toBeInTheDocument()
    expect(screen.getByText('8.8.8.8')).toBeInTheDocument()
  })

  it('shows unavailable latency for offline ISP', () => {
    render(<IspConnectivitySection isps={sampleIsps} />)
    expect(screen.getByText('Backup Link')).toBeInTheDocument()
    expect(screen.getByText('1.1.1.1')).toBeInTheDocument()
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThan(0)
  })
})
