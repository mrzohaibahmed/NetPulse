import { describe, expect, it } from 'vitest'
import type { TopologyEdge } from '@/api/topologyService'
import {
  buildSwitchSearchVisibility,
  dedupeSwitchEdges,
  formatSpeed,
  getLinkStyle,
  getSpeedCategory,
  isLinkDown,
  normalizeSpeedToMbps,
  resolveSwitchDisplay,
} from './switchTopologyUtils'

function edge(
  source: string,
  target: string,
  extra: Partial<TopologyEdge> = {},
): TopologyEdge {
  return {
    id: `edge_${source}_${target}`,
    source,
    target,
    label: '',
    sourcePort: extra.sourcePort || 'Gi1/0/1',
    targetPort: extra.targetPort || 'Gi1/0/2',
    isTrunk: false,
    linkType: 'unknown',
    protocol: 'CDP/LLDP',
    ...extra,
  }
}

describe('normalizeSpeedToMbps', () => {
  it('normalizes common string formats', () => {
    expect(normalizeSpeedToMbps('10 Gbps')).toBe(10000)
    expect(normalizeSpeedToMbps('20Gbps')).toBe(20000)
    expect(normalizeSpeedToMbps('1 Gbps')).toBe(1000)
    expect(normalizeSpeedToMbps('100 Mbps')).toBe(100)
    expect(normalizeSpeedToMbps('10 Mbps')).toBe(10)
    expect(normalizeSpeedToMbps('10G')).toBe(10000)
    expect(normalizeSpeedToMbps('1000')).toBe(1000)
  })

  it('normalizes raw bit-per-second values', () => {
    expect(normalizeSpeedToMbps(100_000_000_000)).toBe(100_000)
    expect(normalizeSpeedToMbps('10000000000')).toBe(10_000)
    expect(normalizeSpeedToMbps('1000000000')).toBe(1000)
  })

  it('returns null for unknown values', () => {
    expect(normalizeSpeedToMbps('auto')).toBeNull()
    expect(normalizeSpeedToMbps('')).toBeNull()
    expect(normalizeSpeedToMbps(undefined)).toBeNull()
  })
})

describe('formatSpeed', () => {
  it('formats speeds for display', () => {
    expect(formatSpeed(100_000)).toBe('100.0 Gbps')
    expect(formatSpeed(20_000)).toBe('20.0 Gbps')
    expect(formatSpeed(10_000)).toBe('10.0 Gbps')
    expect(formatSpeed(1000)).toBe('1.0 Gbps')
    expect(formatSpeed(100)).toBe('100.0 Mbps')
    expect(formatSpeed(10)).toBe('10.0 Mbps')
    expect(formatSpeed(null)).toBe('Unknown')
  })
})

describe('getSpeedCategory and getLinkStyle', () => {
  it('maps speed categories to colors', () => {
    expect(getSpeedCategory(100_000)).toBe('100_GBPS')
    expect(getSpeedCategory(20_000)).toBe('20_GBPS')
    expect(getSpeedCategory(10_000)).toBe('10_GBPS')
    expect(getSpeedCategory(1000)).toBe('1_GBPS')
    expect(getSpeedCategory(100)).toBe('100_MBPS')
    expect(getSpeedCategory(10)).toBe('10_MBPS')
    expect(getSpeedCategory(null)).toBe('UNKNOWN')
  })

  it('uses pink/magenta for 10 Gbps links', () => {
    expect(getLinkStyle(10_000, false).color).toBe('#ec4899')
    expect(getLinkStyle(10_000, false).label).toBe('10.0 Gbps')
  })

  it('uses purple for 1 Gbps links', () => {
    expect(getLinkStyle(1000, false).color).toBe('#8b5cf6')
    expect(getLinkStyle(1000, false).label).toBe('1.0 Gbps')
  })

  it('uses cyan for 100 Mbps links', () => {
    expect(getLinkStyle(100, false).color).toBe('#06b6d4')
    expect(getLinkStyle(100, false).label).toBe('100.0 Mbps')
  })

  it('uses blue for 10 Mbps links', () => {
    expect(getLinkStyle(10, false).color).toBe('#3b82f6')
    expect(getLinkStyle(10, false).label).toBe('10.0 Mbps')
  })

  it('uses blue for 100 Gbps links', () => {
    expect(getLinkStyle(100_000, false).color).toBe('#2563eb')
    expect(getLinkStyle(100_000, false).label).toBe('100.0 Gbps')
  })

  it('uses gray for unknown speed', () => {
    expect(getLinkStyle(null, false).category).toBe('UNKNOWN')
    expect(getLinkStyle(null, false).color).toBe('#94a3b8')
    expect(getLinkStyle(null, false).label).toBe('Unknown')
  })

  it('uses red dashed style for down links', () => {
    const style = getLinkStyle(10_000, true)
    expect(style.category).toBe('DOWN')
    expect(style.color).toBe('#ef4444')
    expect(style.strokeDasharray).toBe('8 6')
    expect(style.label).toBe('10.0 Gbps')
  })
})

describe('isLinkDown', () => {
  it('detects interface down status only', () => {
    expect(isLinkDown({ operStatus: 'down' })).toBe(true)
    expect(isLinkDown({ operStatus: 'up' })).toBe(false)
    expect(isLinkDown({ operStatus: undefined })).toBe(false)
  })
})

describe('dedupeSwitchEdges', () => {
  const switchIds = new Set(['a', 'b', 'c'])

  it('deduplicates bidirectional CDP/LLDP links', () => {
    const edges = [
      edge('a', 'b', { id: '1', sourcePort: 'Gi1/0/1', targetPort: 'Gi1/0/24' }),
      edge('b', 'a', { id: '2', sourcePort: 'Gi1/0/24', targetPort: 'Gi1/0/1' }),
    ]
    expect(dedupeSwitchEdges(edges, switchIds)).toHaveLength(1)
  })

  it('preserves separate physical links between the same switches', () => {
    const edges = [
      edge('a', 'b', { id: '1', sourcePort: 'Gi1/0/1', targetPort: 'Gi1/0/1' }),
      edge('a', 'b', { id: '2', sourcePort: 'Gi1/0/2', targetPort: 'Gi1/0/2' }),
    ]
    expect(dedupeSwitchEdges(edges, switchIds)).toHaveLength(2)
  })

  it('ignores edges that do not connect known switches', () => {
    const edges = [edge('a', 'x'), edge('x', 'b')]
    expect(dedupeSwitchEdges(edges, switchIds)).toHaveLength(0)
  })
})

describe('resolveSwitchDisplay', () => {
  it('uses hostname when available', () => {
    const sw = {
      hostname: 'Core-SW-01',
      label: 'Core-SW-01',
      ip: '192.168.1.1',
      managementAddress: '192.168.1.1',
      details: { hostname: 'Core-SW-01', ip: '192.168.1.1' },
    } as import('@/api/topologyService').TopologyNode

    expect(resolveSwitchDisplay(sw)).toEqual({
      hostname: 'Core-SW-01',
      ip: '192.168.1.1',
    })
  })

  it('falls back to IP instead of Unknown when hostname is missing', () => {
    const sw = {
      hostname: '',
      label: 'Unknown Device',
      ip: '192.168.18.1',
      managementAddress: '192.168.18.1',
      details: { hostname: '', ip: '192.168.18.1' },
    } as import('@/api/topologyService').TopologyNode

    expect(resolveSwitchDisplay(sw)).toEqual({
      hostname: '192.168.18.1',
      ip: '',
    })
  })

  it('uses details hostname when top-level hostname is empty', () => {
    const sw = {
      hostname: '',
      label: 'Unknown Device',
      ip: '192.168.18.6',
      managementAddress: '192.168.18.6',
      details: { hostname: 'Access-SW-06', ip: '192.168.18.6' },
    } as import('@/api/topologyService').TopologyNode

    expect(resolveSwitchDisplay(sw)).toEqual({
      hostname: 'Access-SW-06',
      ip: '192.168.18.6',
    })
  })
})

describe('buildSwitchSearchVisibility', () => {
  const switches = [
    { id: 'a', hostname: 'Core-SW-01', label: 'Core-SW-01', ip: '192.168.1.1' },
    { id: 'b', hostname: 'Access-SW-01', label: 'Access-SW-01', ip: '192.168.1.10' },
    { id: 'c', hostname: 'Access-SW-02', label: 'Access-SW-02', ip: '192.168.1.11' },
  ] as unknown as import('@/api/topologyService').TopologyNode[]

  const edges = [
    edge('a', 'b'),
    edge('b', 'c'),
  ]

  it('highlights matches and keeps direct neighbors visible', () => {
    const result = buildSwitchSearchVisibility(switches, edges, 'core')
    expect(result.matchIds.has('a')).toBe(true)
    expect(result.connectedIds.has('b')).toBe(true)
    expect(result.dimmedIds.has('c')).toBe(true)
  })
})
