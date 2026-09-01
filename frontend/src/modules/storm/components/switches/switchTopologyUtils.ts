import dagre from 'dagre'
import { MarkerType, Position } from '@xyflow/react'
import type { Edge, Node } from '@xyflow/react'
import type { TopologyEdge, TopologyNode } from '@/api/topologyService'
import type { SwitchNodeData } from './SwitchNode'
import {
  layoutPositionsFromSaved,
  mergeLayoutPositions,
  hasSavedLayout,
} from '@/modules/storm/utils/topologyLayout'

export { layoutPositionsFromSaved, mergeLayoutPositions, hasSavedLayout }
export const SWITCH_NODE_WIDTH = 80
export const SWITCH_NODE_HEIGHT = 58
export const SWITCHES_LAYOUT_VIEW_KEY = 'switches'

const GENERIC_HOSTNAME_LABELS = new Set(['', 'unknown', 'unknown device'])

export type SpeedCategory =
  | '100_GBPS'
  | '20_GBPS'
  | '10_GBPS'
  | '1_GBPS'
  | '100_MBPS'
  | '10_MBPS'
  | 'UNKNOWN'
  | 'DOWN'

export type LinkStyle = {
  category: SpeedCategory
  color: string
  strokeDasharray?: string
  strokeWidth: number
  label: string
}

export type SwitchEdgeData = {
  speedMbps: number | null
  speedLabel: string
  linkStyle: LinkStyle
  sourcePort?: string
  targetPort?: string
  operStatus?: string
  highlighted?: boolean
  dimmed?: boolean
}

export const LINK_SPEED_COLORS: Record<Exclude<SpeedCategory, 'DOWN'>, string> = {
  '100_GBPS': '#2563eb',
  '20_GBPS': '#d946ef',
  '10_GBPS': '#ec4899',
  '1_GBPS': '#8b5cf6',
  '100_MBPS': '#06b6d4',
  '10_MBPS': '#3b82f6',
  UNKNOWN: '#94a3b8',
}

export const DOWN_LINK_COLOR = '#ef4444'
export const TOPOLOGY_STALE_EDGE_COLOR = '#94a3b8'
export const TOPOLOGY_TRUNK_EDGE_COLOR = '#f59e0b'

export function isTopologyOfflineStatus(status: string | undefined): boolean {
  const value = (status || '').toLowerCase()
  return (
    value.includes('offline') ||
    value.includes('not reachable') ||
    value.includes('critical')
  )
}

export type TopologyFlowEdgeData = SwitchEdgeData & {
  sourcePort?: string
  targetPort?: string
  isTrunk?: boolean
  linkType?: string
  protocol?: string
  description?: string
  speed?: string
  operStatus?: string
  vlanSummary?: string
  centerLabel?: string
  status?: 'active' | 'stale'
  highlighted?: boolean
  dimmed?: boolean
}

export type TopologyFlowEdgeProps = {
  type: 'topologyEdge'
  animated: boolean
  data: TopologyFlowEdgeData
  style: {
    stroke: string
    strokeWidth: number
    opacity: number
    strokeDasharray?: string
  }
  markerEnd: {
    type: typeof MarkerType.ArrowClosed
    width: number
    height: number
    color: string
  }
}

export function buildTopologyFlowEdge(
  edge: TopologyEdge,
  nodeStatusById: Map<string, string>,
): TopologyFlowEdgeProps {
  const isTrunk = Boolean(edge.isTrunk)
  const isStaleApi = edge.status === 'stale'
  const linkDown = isLinkDown(edge)
  const speedMbps = normalizeSpeedToMbps(edge.speed)
  const linkStyle = getLinkStyle(speedMbps, linkDown)
  const sourceStatus = nodeStatusById.get(edge.source)
  const targetStatus = nodeStatusById.get(edge.target)
  const isOffline = isTopologyOfflineStatus(sourceStatus) || isTopologyOfflineStatus(targetStatus)
  const isInactive = isOffline || isStaleApi
  const edgeStatus: 'active' | 'stale' = isInactive ? 'stale' : 'active'

  let stroke: string
  if (linkDown) {
    stroke = linkStyle.color
  } else if (isInactive) {
    stroke = TOPOLOGY_STALE_EDGE_COLOR
  } else if (isTrunk) {
    stroke = TOPOLOGY_TRUNK_EDGE_COLOR
  } else {
    stroke = linkStyle.color
  }

  const centerLabel = isTrunk
    ? 'Trunk'
    : (edge.linkType || edge.protocol || '').trim()

  return {
    type: 'topologyEdge',
    animated: !isTrunk && !isInactive && !linkDown,
    data: {
      sourcePort: edge.sourcePort,
      targetPort: edge.targetPort,
      isTrunk: edge.isTrunk,
      linkType: edge.linkType,
      protocol: edge.protocol,
      description: edge.description,
      speed: edge.speed,
      operStatus: edge.operStatus,
      vlanSummary: edge.vlanSummary,
      speedLabel: linkStyle.label,
      speedMbps,
      linkStyle,
      centerLabel,
      status: edgeStatus,
    },
    style: {
      stroke,
      strokeWidth: isTrunk ? 2.5 : linkStyle.strokeWidth,
      opacity: isInactive && !linkDown ? 0.45 : 1,
      strokeDasharray: linkDown
        ? linkStyle.strokeDasharray
        : isInactive
          ? '8,6'
          : isTrunk
            ? undefined
            : '6,4',
    },
    markerEnd: {
      type: MarkerType.ArrowClosed,
      width: 16,
      height: 16,
      color: stroke,
    },
  }
}

const SPEED_LABEL_RE =
  /^(\d+(?:\.\d+)?)\s*(g|gbps|gb|m|mbps|mb|k|kbps|bps|b)?$/i

export function normalizeSpeedToMbps(raw: string | number | null | undefined): number | null {
  if (raw === null || raw === undefined) return null

  if (typeof raw === 'number') {
    if (!Number.isFinite(raw) || raw <= 0) return null
    if (raw >= 1_000_000) return Math.round(raw / 1_000_000)
    if (raw >= 1000) return Math.round(raw / 1000)
    return Math.round(raw)
  }

  const text = String(raw).trim()
  if (!text) return null

  const lower = text.toLowerCase().replace(/\s+/g, '')
  if (['auto', 'a-auto', 'unknown', '-', 'n/a', 'na'].includes(lower)) return null

  if (/^\d+(\.\d+)?$/.test(lower)) {
    const numeric = Number(lower)
    if (!Number.isFinite(numeric) || numeric <= 0) return null
    if (numeric >= 1_000_000) return Math.round(numeric / 1_000_000)
    if (numeric >= 1000 && numeric % 1000 === 0 && numeric >= 10_000) {
      return Math.round(numeric / 1_000_000) >= 1
        ? Math.round(numeric / 1_000_000) * 1000
        : Math.round(numeric / 1000)
    }
    return Math.round(numeric)
  }

  const normalized = lower.replace(/^a-/, '')
  const match = normalized.match(SPEED_LABEL_RE)
  if (!match) return null

  const value = Number(match[1])
  if (!Number.isFinite(value) || value <= 0) return null

  const unit = (match[2] || '').toLowerCase()
  if (unit === 'bps' || unit === 'b') return Math.round(value / 1_000_000)
  if (['g', 'gbps', 'gb'].includes(unit)) return Math.round(value * 1000)
  if (['k', 'kbps'].includes(unit)) return Math.round(value / 1000)
  return Math.round(value)
}

export function formatSpeed(mbps: number | null): string {
  if (mbps === null || mbps <= 0) return 'Unknown'
  if (mbps >= 1000 && mbps % 1000 === 0) {
    return `${(mbps / 1000).toFixed(1)} Gbps`
  }
  if (mbps >= 1000) {
    const gbps = mbps / 1000
    return `${gbps % 1 === 0 ? gbps.toFixed(1) : gbps.toFixed(1)} Gbps`
  }
  return `${mbps.toFixed(1)} Mbps`
}

export function getSpeedCategory(mbps: number | null): Exclude<SpeedCategory, 'DOWN'> {
  if (mbps === null || mbps <= 0) return 'UNKNOWN'
  if (mbps >= 100_000) return '100_GBPS'
  if (mbps >= 20_000) return '20_GBPS'
  if (mbps >= 10_000) return '10_GBPS'
  if (mbps >= 1_000) return '1_GBPS'
  if (mbps >= 100) return '100_MBPS'
  if (mbps >= 10) return '10_MBPS'
  return 'UNKNOWN'
}

export function isLinkDown(edge: Pick<TopologyEdge, 'operStatus'>): boolean {
  return String(edge.operStatus || '').toLowerCase() === 'down'
}

export function getLinkStyle(
  speedMbps: number | null,
  isDown: boolean,
): LinkStyle {
  if (isDown) {
    return {
      category: 'DOWN',
      color: DOWN_LINK_COLOR,
      strokeDasharray: '8 6',
      strokeWidth: 2,
      label: formatSpeed(speedMbps),
    }
  }

  const category = getSpeedCategory(speedMbps)
  return {
    category,
    color: LINK_SPEED_COLORS[category],
    strokeWidth: 2,
    label: formatSpeed(speedMbps),
  }
}

export function resolveEdgeSpeedMbps(edge: TopologyEdge): number | null {
  return normalizeSpeedToMbps(edge.speed)
}

export function resolveSwitchIp(sw: TopologyNode): string {
  return (sw.ip || sw.managementAddress || sw.details?.ip || '').trim()
}

export function resolveSwitchDisplay(sw: TopologyNode): { hostname: string; ip: string } {
  const ip = resolveSwitchIp(sw)
  const candidates = [sw.hostname, sw.details?.hostname, sw.label]
    .map((value) => (value || '').trim())
    .filter((value) => value && !GENERIC_HOSTNAME_LABELS.has(value.toLowerCase()))

  let hostname = candidates.find((value) => value !== ip) || candidates[0] || ''
  if (!hostname) {
    hostname = ip || 'Unknown'
  }

  return {
    hostname,
    ip: ip && ip !== hostname ? ip : '',
  }
}

export function resolveSwitchSearchText(sw: TopologyNode): { hostname: string; ip: string } {
  const display = resolveSwitchDisplay(sw)
  const ip = resolveSwitchIp(sw)
  return {
    hostname: display.hostname,
    ip: ip || display.ip,
  }
}

export function getPointOnPath(path: string, ratio: number) {
  if (typeof document === 'undefined') {
    return { x: 0, y: 0 }
  }
  const svgPath = document.createElementNS('http://www.w3.org/2000/svg', 'path')
  svgPath.setAttribute('d', path)
  const length = svgPath.getTotalLength()
  const point = svgPath.getPointAtLength(length * ratio)
  return { x: point.x, y: point.y }
}

function canonicalDevicePair(source: string, target: string): [string, string] {
  return source <= target ? [source, target] : [target, source]
}

function canonicalPortPair(sourcePort: string, targetPort: string): string {
  const ports = [sourcePort.trim(), targetPort.trim()].filter(Boolean).sort()
  return ports.length > 0 ? ports.join('|') : 'default'
}

function dedupeKey(edge: TopologyEdge): string {
  const [left, right] = canonicalDevicePair(edge.source, edge.target)
  const leftPort = edge.source === left ? edge.sourcePort : edge.targetPort
  const rightPort = edge.source === left ? edge.targetPort : edge.sourcePort
  return `${left}|${right}|${canonicalPortPair(leftPort || '', rightPort || '')}`
}

function preferEdge(current: TopologyEdge, candidate: TopologyEdge): TopologyEdge {
  const currentDown = isLinkDown(current)
  const candidateDown = isLinkDown(candidate)
  if (currentDown !== candidateDown) return candidateDown ? current : candidate

  const currentSpeed = resolveEdgeSpeedMbps(current)
  const candidateSpeed = resolveEdgeSpeedMbps(candidate)
  if (currentSpeed === null && candidateSpeed !== null) return candidate
  if (candidateSpeed === null && currentSpeed !== null) return current

  const currentOper = String(current.operStatus || '').toLowerCase()
  const candidateOper = String(candidate.operStatus || '').toLowerCase()
  if (currentOper !== 'up' && candidateOper === 'up') return candidate
  return current
}

export function dedupeSwitchEdges(edges: TopologyEdge[], switchIds: Set<string>): TopologyEdge[] {
  const merged = new Map<string, TopologyEdge>()

  for (const edge of edges) {
    if (!switchIds.has(edge.source) || !switchIds.has(edge.target)) continue
    if (edge.source === edge.target) continue

    const key = dedupeKey(edge)
    const existing = merged.get(key)
    merged.set(key, existing ? preferEdge(existing, edge) : edge)
  }

  return [...merged.values()]
}

export function computeBoundsFromNodes(nodes: Node[]) {
  if (nodes.length === 0) {
    return { width: 960, height: 420 }
  }

  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity

  for (const node of nodes) {
    minX = Math.min(minX, node.position.x)
    minY = Math.min(minY, node.position.y)
    maxX = Math.max(maxX, node.position.x + SWITCH_NODE_WIDTH)
    maxY = Math.max(maxY, node.position.y + SWITCH_NODE_HEIGHT)
  }

  const padding = 96
  return {
    width: Math.max(maxX - minX + padding * 2, 960),
    height: Math.max(maxY - minY + padding * 2, 420),
  }
}

export function buildSwitchSearchVisibility(
  switches: TopologyNode[],
  edges: TopologyEdge[],
  searchQuery: string,
): {
  matchIds: Set<string>
  connectedIds: Set<string>
  dimmedIds: Set<string>
} {
  const switchIds = new Set(switches.map((sw) => sw.id))
  const trimmed = searchQuery.trim().toLowerCase()

  if (!trimmed) {
    return {
      matchIds: new Set(),
      connectedIds: new Set(switchIds),
      dimmedIds: new Set(),
    }
  }

  const matchIds = new Set<string>()
  for (const sw of switches) {
    const { hostname, ip } = resolveSwitchSearchText(sw)
    if (hostname.toLowerCase().includes(trimmed) || ip.toLowerCase().includes(trimmed)) {
      matchIds.add(sw.id)
    }
  }

  if (matchIds.size === 0) {
    return {
      matchIds,
      connectedIds: new Set(switchIds),
      dimmedIds: new Set(),
    }
  }

  const connectedIds = new Set<string>(matchIds)
  const switchEdges = dedupeSwitchEdges(edges, switchIds)
  for (const edge of switchEdges) {
    if (matchIds.has(edge.source)) connectedIds.add(edge.target)
    if (matchIds.has(edge.target)) connectedIds.add(edge.source)
  }

  const dimmedIds = new Set<string>()
  for (const id of switchIds) {
    if (!connectedIds.has(id)) dimmedIds.add(id)
  }

  return { matchIds, connectedIds, dimmedIds }
}

function collectNodeSearchTexts(node: TopologyNode): string[] {
  return [
    node.hostname,
    node.label,
    node.ip,
    node.mac,
    node.type,
    node.managementAddress,
    node.details?.hostname,
    node.details?.ip,
    node.details?.managementAddress,
    node.details?.vendor,
    node.details?.platform,
    node.details?.systemDescription,
  ]
    .map((value) => (value || '').trim().toLowerCase())
    .filter(Boolean)
}

function nodeMatchesSearch(node: TopologyNode, query: string): boolean {
  return collectNodeSearchTexts(node).some((text) => text.includes(query))
}

function edgeMatchesSearch(
  edge: TopologyEdge,
  query: string,
  nodeById: Map<string, TopologyNode>,
): boolean {
  const edgeTexts = [
    edge.sourcePort,
    edge.targetPort,
    edge.description,
    edge.vlanSummary,
    edge.linkType,
    edge.protocol,
    edge.speed,
    edge.label,
  ]
    .map((value) => (value || '').trim().toLowerCase())
    .filter(Boolean)

  if (edgeTexts.some((text) => text.includes(query))) {
    return true
  }

  const source = nodeById.get(edge.source)
  const target = nodeById.get(edge.target)
  return (
    (source != null && nodeMatchesSearch(source, query)) ||
    (target != null && nodeMatchesSearch(target, query))
  )
}

export function buildTopologySearchVisibility(
  nodes: TopologyNode[],
  edges: TopologyEdge[],
  searchQuery: string,
): {
  matchNodeIds: Set<string>
  matchEdgeIds: Set<string>
  highlightedNodeIds: Set<string>
  highlightedEdgeIds: Set<string>
  dimmedNodeIds: Set<string>
  dimmedEdgeIds: Set<string>
} {
  const nodeIds = new Set(nodes.map((node) => node.id))
  const edgeIds = new Set(edges.map((edge) => edge.id))
  const trimmed = searchQuery.trim().toLowerCase()

  if (!trimmed) {
    return {
      matchNodeIds: new Set(),
      matchEdgeIds: new Set(),
      highlightedNodeIds: new Set(nodeIds),
      highlightedEdgeIds: new Set(edgeIds),
      dimmedNodeIds: new Set(),
      dimmedEdgeIds: new Set(),
    }
  }

  const nodeById = new Map(nodes.map((node) => [node.id, node]))
  const matchNodeIds = new Set<string>()
  for (const node of nodes) {
    if (nodeMatchesSearch(node, trimmed)) {
      matchNodeIds.add(node.id)
    }
  }

  const matchEdgeIds = new Set<string>()
  for (const edge of edges) {
    if (edgeMatchesSearch(edge, trimmed, nodeById)) {
      matchEdgeIds.add(edge.id)
    }
  }

  if (matchNodeIds.size === 0 && matchEdgeIds.size === 0) {
    return {
      matchNodeIds,
      matchEdgeIds,
      highlightedNodeIds: new Set(nodeIds),
      highlightedEdgeIds: new Set(edgeIds),
      dimmedNodeIds: new Set(),
      dimmedEdgeIds: new Set(),
    }
  }

  const highlightedNodeIds = new Set<string>(matchNodeIds)
  const highlightedEdgeIds = new Set<string>(matchEdgeIds)

  for (const edge of edges) {
    if (matchNodeIds.has(edge.source) || matchNodeIds.has(edge.target)) {
      highlightedEdgeIds.add(edge.id)
      highlightedNodeIds.add(edge.source)
      highlightedNodeIds.add(edge.target)
    }
    if (matchEdgeIds.has(edge.id)) {
      highlightedNodeIds.add(edge.source)
      highlightedNodeIds.add(edge.target)
    }
  }

  const dimmedNodeIds = new Set<string>()
  for (const id of nodeIds) {
    if (!highlightedNodeIds.has(id)) dimmedNodeIds.add(id)
  }

  const dimmedEdgeIds = new Set<string>()
  for (const id of edgeIds) {
    if (!highlightedEdgeIds.has(id)) dimmedEdgeIds.add(id)
  }

  return {
    matchNodeIds,
    matchEdgeIds,
    highlightedNodeIds,
    highlightedEdgeIds,
    dimmedNodeIds,
    dimmedEdgeIds,
  }
}

export function applyTopologyEdgeSearchStyle(
  built: TopologyFlowEdgeProps,
  highlighted: boolean,
  dimmed: boolean,
): TopologyFlowEdgeProps {
  if (!highlighted && !dimmed) {
    return {
      ...built,
      data: { ...built.data, highlighted: false, dimmed: false },
    }
  }

  const baseStrokeWidth = built.style.strokeWidth
  const baseOpacity = built.style.opacity

  return {
    ...built,
    data: { ...built.data, highlighted, dimmed },
    style: {
      ...built.style,
      strokeWidth: highlighted ? baseStrokeWidth + 2 : baseStrokeWidth,
      opacity: dimmed ? 0.12 : highlighted ? 1 : baseOpacity,
    },
    markerEnd: {
      ...built.markerEnd,
      color: highlighted ? '#3b82f6' : built.markerEnd.color,
    },
    animated: highlighted ? true : built.animated,
  }
}

function toFlowEdge(edge: TopologyEdge): Edge<SwitchEdgeData> {
  const speedMbps = resolveEdgeSpeedMbps(edge)
  const down = isLinkDown(edge)
  const linkStyle = getLinkStyle(speedMbps, down)

  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: 'switchEdge',
    animated: false,
    selectable: false,
    data: {
      speedMbps,
      speedLabel: linkStyle.label,
      linkStyle,
      sourcePort: edge.sourcePort || '',
      targetPort: edge.targetPort || '',
      operStatus: edge.operStatus || '',
    },
    style: {
      stroke: linkStyle.color,
      strokeWidth: linkStyle.strokeWidth,
      strokeDasharray: linkStyle.strokeDasharray,
    },
  }
}

export function buildSwitchGraph(
  switches: TopologyNode[],
  edges: TopologyEdge[],
  positionOverrides?: Record<string, { x: number; y: number }>,
  searchQuery = '',
): { nodes: Node<SwitchNodeData>[]; edges: Edge<SwitchEdgeData>[]; bounds: { width: number; height: number } } {
  if (switches.length === 0) {
    return {
      nodes: [],
      edges: [],
      bounds: { width: 960, height: 560 },
    }
  }

  const switchIds = new Set(switches.map((sw) => sw.id))
  const switchEdges = dedupeSwitchEdges(edges, switchIds)
  const searchVisibility = buildTopologySearchVisibility(switches, switchEdges, searchQuery)

  const nodes: Node<SwitchNodeData>[] = switches.map((sw) => {
    const isMatch = searchVisibility.matchNodeIds.has(sw.id)
    const isDimmed = searchVisibility.dimmedNodeIds.has(sw.id)
    const display = resolveSwitchDisplay(sw)

    return {
      id: sw.id,
      type: 'switchNode',
      position: positionOverrides?.[sw.id] ?? { x: 0, y: 0 },
      draggable: true,
      data: {
        hostname: display.hostname,
        ip: display.ip,
        status: sw.status || sw.details?.status || 'Unknown',
        highlighted: isMatch,
        dimmed: isDimmed,
      },
    }
  })

  const flowEdges: Edge<SwitchEdgeData>[] = switchEdges.map((edge) => {
    const base = toFlowEdge(edge)
    const baseData = base.data!
    const highlighted = searchVisibility.highlightedEdgeIds.has(edge.id)
    const dimmed = searchVisibility.dimmedEdgeIds.has(edge.id)
    const baseStrokeWidth =
      typeof base.style?.strokeWidth === 'number' ? base.style.strokeWidth : 2

    const edgeData: SwitchEdgeData = {
      speedMbps: baseData.speedMbps,
      speedLabel: baseData.speedLabel,
      linkStyle: baseData.linkStyle,
      sourcePort: baseData.sourcePort,
      targetPort: baseData.targetPort,
      operStatus: baseData.operStatus,
      highlighted,
      dimmed,
    }

    return {
      ...base,
      data: edgeData,
      style: {
        ...base.style,
        strokeWidth: highlighted ? baseStrokeWidth + 2 : baseStrokeWidth,
        opacity: dimmed ? 0.12 : 1,
      },
      zIndex: highlighted ? 10 : dimmed ? 0 : 1,
      animated: highlighted,
    }
  })

  const needsLayout = nodes.some((node) => !positionOverrides?.[node.id])

  if (!needsLayout) {
    const positionedNodes = nodes.map((node) => ({
      ...node,
      targetPosition: Position.Top,
      sourcePosition: Position.Bottom,
    }))
    return {
      nodes: positionedNodes,
      edges: flowEdges,
      bounds: computeBoundsFromNodes(positionedNodes),
    }
  }

  const laidOut = layoutSwitchGraph(nodes, flowEdges)
  const finalNodes = laidOut.nodes.map((node) => ({
    ...node,
    position: positionOverrides?.[node.id] ?? node.position,
    draggable: true,
  }))

  return {
    nodes: finalNodes,
    edges: laidOut.edges,
    bounds: laidOut.bounds,
  }
}

function layoutSwitchGrid(nodes: Node<SwitchNodeData>[]) {
  const cols = Math.max(1, Math.ceil(Math.sqrt(nodes.length)))
  const gapX = 110
  const gapY = 72
  const padding = 48

  const layoutedNodes = nodes.map((node, index) => ({
    ...node,
    position: {
      x: padding + (index % cols) * gapX,
      y: padding + Math.floor(index / cols) * gapY,
    },
    targetPosition: Position.Top,
    sourcePosition: Position.Bottom,
  }))

  const rows = Math.ceil(nodes.length / cols)
  return {
    nodes: layoutedNodes,
    bounds: {
      width: Math.max(cols * gapX + padding * 2, 960),
      height: Math.max(rows * gapY + padding * 2, 420),
    },
  }
}

function layoutSwitchGraph(nodes: Node<SwitchNodeData>[], edges: Edge<SwitchEdgeData>[]) {
  if (edges.length === 0) {
    const grid = layoutSwitchGrid(nodes)
    return { nodes: grid.nodes, edges, bounds: grid.bounds }
  }

  const graph = new dagre.graphlib.Graph()
  graph.setDefaultEdgeLabel(() => ({}))
  graph.setGraph({ rankdir: 'TB', nodesep: 36, ranksep: 56, marginx: 32, marginy: 32 })

  nodes.forEach((node) => {
    graph.setNode(node.id, { width: SWITCH_NODE_WIDTH, height: SWITCH_NODE_HEIGHT })
  })

  edges.forEach((edge) => {
    graph.setEdge(edge.source, edge.target)
  })

  dagre.layout(graph)

  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity

  const layoutedNodes = nodes.map((node) => {
    const pos = graph.node(node.id)
    const x = (pos?.x ?? 0) - SWITCH_NODE_WIDTH / 2
    const y = (pos?.y ?? 0) - SWITCH_NODE_HEIGHT / 2
    minX = Math.min(minX, x)
    minY = Math.min(minY, y)
    maxX = Math.max(maxX, x + SWITCH_NODE_WIDTH)
    maxY = Math.max(maxY, y + SWITCH_NODE_HEIGHT)

    return {
      ...node,
      position: { x, y },
      targetPosition: Position.Top,
      sourcePosition: Position.Bottom,
    }
  })

  const padding = 96
  return {
    nodes: layoutedNodes,
    edges,
    bounds: {
      width: Math.max(maxX - minX + padding * 2, 960),
      height: Math.max(maxY - minY + padding * 2, 560),
    },
  }
}
