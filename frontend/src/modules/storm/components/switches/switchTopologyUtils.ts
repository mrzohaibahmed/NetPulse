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
export const SWITCH_NODE_WIDTH = 180
export const SWITCH_NODE_HEIGHT = 72
export const SWITCHES_LAYOUT_VIEW_KEY = 'switches'

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

export function dedupeSwitchEdges(edges: TopologyEdge[], switchIds: Set<string>): TopologyEdge[] {
  const seen = new Set<string>()
  const result: TopologyEdge[] = []

  for (const edge of edges) {
    if (!switchIds.has(edge.source) || !switchIds.has(edge.target)) continue
    if (edge.source === edge.target) continue

    const pair = [edge.source, edge.target].sort().join('|')
    if (seen.has(pair)) continue
    seen.add(pair)
    result.push(edge)
  }

  return result
}

export function buildSwitchGraph(
  switches: TopologyNode[],
  edges: TopologyEdge[],
  positionOverrides?: Record<string, { x: number; y: number }>,
): { nodes: Node[]; edges: Edge[]; bounds: { width: number; height: number } } {
  if (switches.length === 0) {
    return {
      nodes: [],
      edges: [],
      bounds: { width: 960, height: 560 },
    }
  }

  const switchIds = new Set(switches.map((sw) => sw.id))
  const switchEdges = dedupeSwitchEdges(edges, switchIds)

  const nodes: Node[] = switches.map((sw) => ({
    id: sw.id,
    type: 'switchNode',
    position: positionOverrides?.[sw.id] ?? { x: 0, y: 0 },
    draggable: true,
    data: {
      hostname: sw.hostname || sw.label || 'Unknown',
      ip: sw.ip || sw.managementAddress || '',
      status: sw.status || sw.details?.status || 'Unknown',
    } satisfies SwitchNodeData,
  }))

  const flowEdges: Edge[] = switchEdges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: 'smoothstep',
    animated: edge.animated ?? false,
    style: {
      stroke: edge.status === 'stale' ? '#94a3b8' : '#3b82f6',
      strokeWidth: 2,
    },
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: edge.status === 'stale' ? '#94a3b8' : '#3b82f6',
    },
  }))

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

function layoutSwitchGrid(nodes: Node[]) {
  const cols = Math.max(1, Math.ceil(Math.sqrt(nodes.length)))
  const gapX = 220
  const gapY = 120
  const padding = 96

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

function layoutSwitchGraph(nodes: Node[], edges: Edge[]) {
  if (edges.length === 0) {
    const grid = layoutSwitchGrid(nodes)
    return { nodes: grid.nodes, edges, bounds: grid.bounds }
  }

  const graph = new dagre.graphlib.Graph()
  graph.setDefaultEdgeLabel(() => ({}))
  graph.setGraph({ rankdir: 'TB', nodesep: 80, ranksep: 100, marginx: 48, marginy: 48 })

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
