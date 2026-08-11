import { memo, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import {
  ReactFlow,
  Controls,
  Background,
  BaseEdge,
  EdgeLabelRenderer,
  MarkerType,
  Handle,
  Position,
  getSmoothStepPath,
  useNodesState,
  useEdgesState,
  Panel,
} from '@xyflow/react'
import type { Node, Edge, NodeProps, EdgeProps, ReactFlowInstance } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from 'dagre'
import { Network, Server, Share2 } from 'lucide-react'

import { PageHeader } from '@/shared/components/PageHeader'
import { LoadingState } from '@/shared/components/LoadingState'
import { ErrorState } from '@/shared/components/ErrorState'
import { useTopologySwitches, useLevel1Topology, useFullTopology } from '@/hooks/useTopologyData'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip'
import { cn } from '@/lib/utils'
import type { TopologyEdge as ApiTopologyEdge, TopologyNodeDetails } from '@/api/topologyService'

type NodeHandleSpec = {
  id: string
  type: 'source' | 'target'
  position: Position
  style?: CSSProperties
}

type TopologyNodeData = {
  hostname: string
  label: string
  ip: string
  mac?: string
  type: string
  status: string
  isKnownDevice: boolean
  isCentral?: boolean
  details: TopologyNodeDetails
  handles: NodeHandleSpec[]
}

type TopologyEdgeData = {
  sourcePort?: string
  targetPort?: string
  isTrunk?: boolean
  linkType?: string
  protocol?: string
  description?: string
  speed?: string
  centerLabel?: string
  status?: 'active' | 'stale'
}

const NODE_WIDTH = 200
const NODE_HEIGHT = 96
const EDGE_COLOR = '#3b82f6'
const TRUNK_EDGE_COLOR = '#f59e0b'
const STALE_EDGE_COLOR = '#94a3b8'
const POSITIONS_STORAGE_PREFIX = 'netpulse-topology-pos-'

function isSwitchType(type: string) {
  return type.toLowerCase().includes('switch')
}

function displayHostname(data: TopologyNodeData) {
  return data.hostname || data.label || 'Unknown'
}

function getPointOnPath(path: string, ratio: number) {
  if (typeof document === 'undefined') {
    return { x: 0, y: 0 }
  }
  const svgPath = document.createElementNS('http://www.w3.org/2000/svg', 'path')
  svgPath.setAttribute('d', path)
  const length = svgPath.getTotalLength()
  const point = svgPath.getPointAtLength(length * ratio)
  return { x: point.x, y: point.y }
}

function distributeOffset(count: number, index: number) {
  if (count <= 1) return 50
  return 12 + (76 / (count - 1)) * index
}

function computeHandleAssignments(apiEdges: ApiTopologyEdge[]) {
  const outgoing = new Map<string, ApiTopologyEdge[]>()
  const incoming = new Map<string, ApiTopologyEdge[]>()
  const edgeHandles = new Map<string, { sourceHandle: string; targetHandle: string }>()
  const nodeHandles = new Map<string, NodeHandleSpec[]>()

  for (const edge of apiEdges) {
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, [])
    outgoing.get(edge.source)!.push(edge)
    if (!incoming.has(edge.target)) incoming.set(edge.target, [])
    incoming.get(edge.target)!.push(edge)
  }

  for (const [nodeId, outs] of outgoing) {
    const specs = nodeHandles.get(nodeId) ?? []
    outs.forEach((edge, index) => {
      const handleId = `${nodeId}-out-${index}`
      const offset = distributeOffset(outs.length, index)
      const onBottom = index % 2 === 0
      specs.push({
        id: handleId,
        type: 'source',
        position: onBottom ? Position.Bottom : Position.Right,
        style: onBottom ? { left: `${offset}%` } : { top: `${offset}%` },
      })
      edgeHandles.set(edge.id, {
        ...(edgeHandles.get(edge.id) ?? { sourceHandle: '', targetHandle: '' }),
        sourceHandle: handleId,
      })
    })
    nodeHandles.set(nodeId, specs)
  }

  for (const [nodeId, ins] of incoming) {
    const specs = nodeHandles.get(nodeId) ?? []
    ins.forEach((edge, index) => {
      const handleId = `${nodeId}-in-${index}`
      const offset = distributeOffset(ins.length, index)
      const onTop = index % 2 === 0
      specs.push({
        id: handleId,
        type: 'target',
        position: onTop ? Position.Top : Position.Left,
        style: onTop ? { left: `${offset}%` } : { top: `${offset}%` },
      })
      edgeHandles.set(edge.id, {
        ...(edgeHandles.get(edge.id) ?? { sourceHandle: '', targetHandle: '' }),
        targetHandle: handleId,
      })
    })
    nodeHandles.set(nodeId, specs)
  }

  return { edgeHandles, nodeHandles }
}

function loadSavedPositions(viewKey: string) {
  try {
    const raw = localStorage.getItem(`${POSITIONS_STORAGE_PREFIX}${viewKey}`)
    if (!raw) return {}
    return JSON.parse(raw) as Record<string, { x: number; y: number }>
  } catch {
    return {}
  }
}

function savePositions(viewKey: string, nodes: Node[]) {
  const payload = Object.fromEntries(nodes.map((node) => [node.id, node.position]))
  localStorage.setItem(`${POSITIONS_STORAGE_PREFIX}${viewKey}`, JSON.stringify(payload))
}

function getLayoutedElements(nodes: Node[], edges: Edge[], direction = 'TB') {
  if (nodes.length === 0) {
    return { nodes, edges, bounds: { width: 900, height: 560, minX: 0, minY: 0 } }
  }

  const graph = new dagre.graphlib.Graph()
  graph.setDefaultEdgeLabel(() => ({}))
  graph.setGraph({ rankdir: direction, nodesep: 100, ranksep: 140, marginx: 48, marginy: 48 })

  const isHorizontal = direction === 'LR'

  nodes.forEach((node) => {
    graph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
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
    const x = pos.x - NODE_WIDTH / 2
    const y = pos.y - NODE_HEIGHT / 2
    minX = Math.min(minX, x)
    minY = Math.min(minY, y)
    maxX = Math.max(maxX, x + NODE_WIDTH)
    maxY = Math.max(maxY, y + NODE_HEIGHT)

    return {
      ...node,
      position: { x, y },
      targetPosition: isHorizontal ? Position.Left : Position.Top,
      sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
    } as Node
  })

  const padding = 96
  return {
    nodes: layoutedNodes,
    edges,
    bounds: {
      width: Math.max(maxX - minX + padding * 2, 960),
      height: Math.max(maxY - minY + padding * 2, 560),
      minX: minX - padding,
      minY: minY - padding,
    },
  }
}

function EdgeLabel({
  x,
  y,
  text,
  variant = 'port',
}: {
  x: number
  y: number
  text: string
  variant?: 'port' | 'center' | 'trunk'
}) {
  if (!text) return null
  return (
    <div
      className="nodrag nopan pointer-events-none absolute"
      style={{ transform: `translate(-50%, -50%) translate(${x}px, ${y}px)` }}
    >
      <span
        className={cn(
          'rounded-md border px-1.5 py-0.5 text-[10px] font-medium shadow-sm',
          variant === 'trunk' &&
            'border-amber-500/50 bg-amber-500/15 font-semibold text-amber-700 dark:text-amber-300',
          variant === 'center' && 'border-border/80 bg-card/95 text-muted-foreground',
          variant === 'port' && 'border-border/80 bg-card/95 font-mono text-foreground/90',
        )}
      >
        {text}
      </span>
    </div>
  )
}

const TopologyDeviceNode = memo(function TopologyDeviceNode({ data }: NodeProps) {
  const nodeData = data as TopologyNodeData
  const switchLike = isSwitchType(nodeData.type || '')
  const details = nodeData.details

  const card = (
    <div
      className={cn(
        'flex h-[96px] w-[200px] flex-col justify-center rounded-xl border px-3 py-2 shadow-md backdrop-blur-sm',
        'border-border/70 bg-card/90',
        nodeData.isCentral && 'border-primary ring-1 ring-primary/50 bg-primary/5',
        !nodeData.isKnownDevice && 'border-dashed border-border/60 bg-muted/20',
      )}
    >
      {(nodeData.handles ?? []).map((handle) => (
        <Handle
          key={handle.id}
          id={handle.id}
          type={handle.type}
          position={handle.position}
          style={handle.style}
          className="!h-2.5 !w-2.5 !border-2 !border-background !bg-primary"
        />
      ))}
      <div className="flex items-start gap-2.5">
        <div
          className={cn(
            'mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border/60',
            switchLike ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground',
          )}
        >
          {switchLike ? <Network className="h-4 w-4" /> : <Server className="h-4 w-4" />}
        </div>
        <div className="min-w-0 flex-1 space-y-0.5">
          <div
            className="truncate text-sm font-bold leading-tight text-foreground"
            title={displayHostname(nodeData)}
          >
            {displayHostname(nodeData)}
          </div>
          <div className="truncate text-[11px] leading-tight text-muted-foreground" title={nodeData.type}>
            {nodeData.type || 'Unknown'}
          </div>
          <div className="flex flex-col gap-0.5 mt-0.5">
            {nodeData.ip && (
              <div className="truncate font-mono text-[10px] leading-tight text-muted-foreground">
                IP: {nodeData.ip}
              </div>
            )}
            {nodeData.mac && (
              <div className="truncate font-mono text-[10px] leading-tight text-muted-foreground">
                MAC: {nodeData.mac}
              </div>
            )}
            {!nodeData.ip && !nodeData.mac && (
              <div className="truncate font-mono text-[10px] leading-tight text-muted-foreground">
                —
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )

  return (
    <Tooltip delayDuration={200}>
      <TooltipTrigger asChild>{card}</TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs space-y-1 p-3 text-xs">
        <div className="font-semibold text-foreground">{displayHostname(nodeData)}</div>
        <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-muted-foreground">
          {nodeData.ip && (
            <>
              <span>IP</span>
              <span className="font-mono text-foreground">{nodeData.ip}</span>
            </>
          )}
          {nodeData.mac && (
            <>
              <span>MAC</span>
              <span className="font-mono text-foreground">{nodeData.mac}</span>
            </>
          )}
          <span>Type</span>
          <span className="text-foreground">{details?.type || nodeData.type || '—'}</span>
          <span>Status</span>
          <span className="text-foreground">{details?.status || nodeData.status || '—'}</span>
          {details?.vendor ? (
            <>
              <span>Vendor</span>
              <span className="text-foreground">{details.vendor}</span>
            </>
          ) : null}
          {details?.platform ? (
            <>
              <span>Platform</span>
              <span className="text-foreground">{details.platform}</span>
            </>
          ) : null}
          {details?.protocol ? (
            <>
              <span>Protocol</span>
              <span className="text-foreground">{details.protocol}</span>
            </>
          ) : null}
          {details?.managementAddress ? (
            <>
              <span>Mgmt IP</span>
              <span className="font-mono text-foreground">{details.managementAddress}</span>
            </>
          ) : null}
          {details?.operatingSystem ? (
            <>
              <span>OS</span>
              <span className="text-foreground">{details.operatingSystem}</span>
            </>
          ) : null}
          {details?.systemDescription ? (
            <>
              <span>Description</span>
              <span className="text-foreground">{details.systemDescription}</span>
            </>
          ) : null}
        </div>
      </TooltipContent>
    </Tooltip>
  )
})

const TopologyEdge = memo(function TopologyEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  markerEnd,
  data,
}: EdgeProps) {
  const edgeData = (data || {}) as TopologyEdgeData
  const isTrunk = Boolean(edgeData.isTrunk)
  const isStale = edgeData.status === 'stale'
  const stroke = isStale ? STALE_EDGE_COLOR : isTrunk ? TRUNK_EDGE_COLOR : EDGE_COLOR

  const [edgePath] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 14,
  })

  const sourceLabelPoint = getPointOnPath(edgePath, 0.12)
  const centerLabelPoint = getPointOnPath(edgePath, 0.5)
  const targetLabelPoint = getPointOnPath(edgePath, 0.88)

  const sourcePort = (edgeData.sourcePort || '').trim()
  const targetPort = (edgeData.targetPort || '').trim()
  const centerLabel = isTrunk
    ? 'Trunk'
    : (edgeData.centerLabel || edgeData.linkType || edgeData.protocol || '').trim()

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        interactionWidth={20}
        style={{
          stroke,
          strokeWidth: isTrunk ? 2.5 : 2,
          strokeDasharray: isStale ? '8,6' : isTrunk ? undefined : '6,4',
          opacity: isStale ? 0.45 : 1,
          ...style,
        }}
      />
      <EdgeLabelRenderer>
        <EdgeLabel x={sourceLabelPoint.x} y={sourceLabelPoint.y} text={sourcePort} variant="port" />
        <EdgeLabel
          x={centerLabelPoint.x}
          y={centerLabelPoint.y}
          text={centerLabel}
          variant={isTrunk ? 'trunk' : 'center'}
        />
        <EdgeLabel x={targetLabelPoint.x} y={targetLabelPoint.y} text={targetPort} variant="port" />
      </EdgeLabelRenderer>
    </>
  )
})

const nodeTypes = { topologyDevice: TopologyDeviceNode }
const edgeTypes = { topologyEdge: TopologyEdge }

export function TopologyPage() {
  const [selectedSwitchId, setSelectedSwitchId] = useState<string | null>(null)
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [canvasSize, setCanvasSize] = useState({ width: 960, height: 560 })
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null)
  
  const [hoveredEdge, setHoveredEdge] = useState<{
    x: number
    y: number
    data: TopologyEdgeData
  } | null>(null)

  const onEdgeMouseEnter = useCallback((event: React.MouseEvent, edge: Edge) => {
    setHoveredEdge({
      x: event.clientX,
      y: event.clientY,
      data: (edge.data || {}) as TopologyEdgeData,
    })
  }, [])

  const onEdgeMouseMove = useCallback((event: React.MouseEvent) => {
    setHoveredEdge((prev) => {
      if (!prev) return prev
      return { ...prev, x: event.clientX, y: event.clientY }
    })
  }, [])

  const onEdgeMouseLeave = useCallback(() => {
    setHoveredEdge(null)
  }, [])

  const viewKey = selectedSwitchId ?? 'full'
  const shouldFitViewRef = useRef(true)
  const prevViewKeyRef = useRef(viewKey)

  const {
    data: switchesData,
    isLoading: isLoadingSwitches,
    isError: isErrorSwitches,
    refetch: refetchSwitches,
  } = useTopologySwitches()
  const { data: level1Data, isLoading: isLoadingLevel1 } = useLevel1Topology(selectedSwitchId)
  const { data: fullData, isLoading: isLoadingFull } = useFullTopology(selectedSwitchId === null)

  const activeData = selectedSwitchId ? level1Data : fullData
  const isLoadingActive = selectedSwitchId ? isLoadingLevel1 : isLoadingFull
  const switches = useMemo(() => switchesData || [], [switchesData])

  useEffect(() => {
    if (prevViewKeyRef.current !== viewKey) {
      prevViewKeyRef.current = viewKey
      shouldFitViewRef.current = true
    }
  }, [viewKey])

  useEffect(() => {
    if (!activeData?.nodes || !activeData?.edges) {
      setNodes([])
      setEdges([])
      return
    }

    // Level 2 is live-only: backend filters stale edges; keep a local guard.
    const visibleEdges =
      selectedSwitchId != null
        ? activeData.edges
        : activeData.edges.filter((e) => e.status !== 'stale')

    const { edgeHandles, nodeHandles } = computeHandleAssignments(visibleEdges)
    const savedPositions = loadSavedPositions(viewKey)

    const flowNodes: Node[] = activeData.nodes.map((n) => ({
      id: n.id,
      type: 'topologyDevice',
      position: savedPositions[n.id] ?? { x: 0, y: 0 },
      draggable: true,
      data: {
        hostname: n.hostname,
        label: n.label,
        ip: n.ip,
        mac: n.mac,
        type: n.type,
        status: n.status,
        isKnownDevice: n.isKnownDevice,
        isCentral: selectedSwitchId != null && n.id === selectedSwitchId,
        details: n.details,
        handles: nodeHandles.get(n.id) ?? [],
      } satisfies TopologyNodeData,
    }))

    const flowEdges: Edge[] = visibleEdges.map((e) => {
      const handles = edgeHandles.get(e.id)
      const isTrunk = Boolean(e.isTrunk)
      const isStale = e.status === 'stale'
      const stroke = isStale ? STALE_EDGE_COLOR : isTrunk ? TRUNK_EDGE_COLOR : EDGE_COLOR
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: handles?.sourceHandle,
        targetHandle: handles?.targetHandle,
        type: 'topologyEdge',
        animated: !isTrunk && !isStale,
        data: {
          sourcePort: e.sourcePort,
          targetPort: e.targetPort,
          isTrunk: e.isTrunk,
          linkType: e.linkType,
          protocol: e.protocol,
          description: e.description,
          speed: e.speed,
          centerLabel: e.isTrunk ? 'Trunk' : e.linkType || e.protocol,
          status: e.status ?? 'active',
        } satisfies TopologyEdgeData,
        style: {
          stroke,
          strokeWidth: isTrunk ? 2.5 : 2,
          opacity: isStale ? 0.45 : 1,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 16,
          height: 16,
          color: stroke,
        },
      }
    })

    const needsLayout = flowNodes.some((node) => !savedPositions[node.id])
    let nextNodes = flowNodes

    if (needsLayout) {
      const { nodes: layoutedNodes, bounds } = getLayoutedElements(flowNodes, flowEdges, 'TB')
      nextNodes = layoutedNodes.map((node) => ({
        ...node,
        position: savedPositions[node.id] ?? node.position,
      }))
      setCanvasSize({ width: bounds.width, height: bounds.height })
    } else {
      let minX = Infinity
      let minY = Infinity
      let maxX = -Infinity
      let maxY = -Infinity
      for (const node of nextNodes) {
        minX = Math.min(minX, node.position.x)
        minY = Math.min(minY, node.position.y)
        maxX = Math.max(maxX, node.position.x + NODE_WIDTH)
        maxY = Math.max(maxY, node.position.y + NODE_HEIGHT)
      }
      setCanvasSize({
        width: Math.max(maxX - minX + 192, 960),
        height: Math.max(maxY - minY + 192, 560),
      })
    }

    setNodes(nextNodes)
    setEdges(flowEdges)
  }, [activeData, selectedSwitchId, viewKey, setNodes, setEdges])

  useEffect(() => {
    if (!rfInstance || nodes.length === 0 || !shouldFitViewRef.current) return
    requestAnimationFrame(() => {
      rfInstance.fitView({ padding: 0.18, duration: 250 })
      shouldFitViewRef.current = false
    })
  }, [rfInstance, nodes, viewKey])

  const onNodeDragStop = useCallback(() => {
    const currentNodes = rfInstance?.getNodes() ?? nodes
    savePositions(viewKey, currentNodes)
  }, [nodes, rfInstance, viewKey])

  if (isLoadingSwitches) {
    return (
      <div className="np-page">
        <PageHeader title="Network Topology" description="Loading switches..." />
        <LoadingState />
      </div>
    )
  }

  if (isErrorSwitches) {
    return (
      <div className="np-page">
        <PageHeader title="Network Topology" description="Failed to load." />
        <ErrorState
          title="Error"
          message="Could not fetch topology data."
          onRetry={() => refetchSwitches()}
        />
      </div>
    )
  }

  return (
    <div className="np-page flex min-h-0 flex-col">
      <PageHeader
        title="Network Topology"
        description="Interactive Level 1 and Level 2 graphs built from CDP/LLDP neighbors."
      />

      <div className="mt-4 flex flex-col gap-4">
        <div className="shrink-0 overflow-x-auto">
          <div className="flex min-w-max gap-3 pb-1">
            <Card
              className={cn(
                'w-56 shrink-0 cursor-pointer border-border/70 bg-card/70 shadow-sm transition-all hover:border-primary',
                selectedSwitchId === null && 'border-primary bg-primary/5 ring-1 ring-primary',
              )}
              onClick={() => setSelectedSwitchId(null)}
            >
              <CardHeader className="p-4 pb-2">
                <CardTitle className="flex items-center gap-2 text-sm">
                  <Share2 className="h-4 w-4 text-primary" />
                  Full Topology
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 pt-0 text-xs text-muted-foreground">
                Level 2 — complete network graph
              </CardContent>
            </Card>

            {switches.map((sw) => (
              <Card
                key={sw.id}
                className={cn(
                  'w-56 shrink-0 cursor-pointer border-border/70 bg-card/70 shadow-sm transition-all hover:border-primary',
                  selectedSwitchId === sw.id && 'border-primary bg-primary/5 ring-1 ring-primary',
                )}
                onClick={() => setSelectedSwitchId(sw.id)}
              >
                <CardHeader className="p-4 pb-1">
                  <CardTitle className="flex items-center gap-2 truncate text-sm" title={sw.hostname || sw.label}>
                    <Network className="h-4 w-4 shrink-0 text-primary" />
                    <span className="truncate">{sw.hostname || sw.label}</span>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-0.5 p-4 pt-0 text-xs text-muted-foreground">
                  <div>IP: {sw.ip || '—'}</div>
                  <div className="truncate">{sw.type || 'Switch'}</div>
                </CardContent>
              </Card>
            ))}

            {switches.length === 0 && (
              <div className="flex h-[88px] w-56 items-center justify-center rounded-xl border border-dashed border-border/70 text-xs text-muted-foreground">
                No switches discovered yet.
              </div>
            )}
          </div>
        </div>

        <div className="relative max-h-[calc(100vh-15rem)] min-h-[420px] overflow-auto rounded-xl border border-border/70 bg-card/40">
          {isLoadingActive ? (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-card/50 backdrop-blur-sm">
              <LoadingState label="Drawing topology…" />
            </div>
          ) : null}

          {nodes.length === 0 && !isLoadingActive ? (
            <div className="flex min-h-[420px] items-center justify-center">
              <div className="rounded-xl border border-border/70 bg-card/80 px-6 py-4 text-sm text-muted-foreground backdrop-blur-md">
                No topology links found for this view.
              </div>
            </div>
          ) : (
            <div
              style={{
                width: Math.max(canvasSize.width, 960),
                height: Math.max(canvasSize.height, 420),
                minWidth: '100%',
              }}
            >
              <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onNodeDragStop={onNodeDragStop}
                onEdgeMouseEnter={onEdgeMouseEnter}
                onEdgeMouseMove={onEdgeMouseMove}
                onEdgeMouseLeave={onEdgeMouseLeave}
                onInit={setRfInstance}
                nodeTypes={nodeTypes}
                edgeTypes={edgeTypes}
                nodesDraggable
                panOnDrag
                zoomOnScroll
                minZoom={0.25}
                maxZoom={2}
                proOptions={{ hideAttribution: true }}
                defaultEdgeOptions={{ type: 'topologyEdge' }}
              >
                <Background color="#94a3b8" gap={18} size={1} />
                <Controls className="border-border bg-card fill-foreground" />
                <Panel
                  position="top-right"
                  className="rounded-lg border border-border/70 bg-card/90 px-2.5 py-1.5 text-xs font-medium text-foreground shadow-sm backdrop-blur-md"
                >
                  {selectedSwitchId === null ? 'Level 2 · Full Topology' : 'Level 1 · Switch Neighbors'}
                </Panel>
              </ReactFlow>
            </div>
          )}
        </div>
      </div>
      {hoveredEdge && (
        <div
          className="pointer-events-none fixed z-50 rounded-lg border border-border/70 bg-card/95 p-3 text-sm text-foreground shadow-xl backdrop-blur-md transition-opacity"
          style={{
            left: hoveredEdge.x + 15,
            top: hoveredEdge.y + 15,
          }}
        >
          <div className="mb-2 font-semibold flex items-center gap-2">
            <span className="text-muted-foreground">{hoveredEdge.data.sourcePort || 'Unknown'}</span>
            <span className="text-muted-foreground">⟷</span>
            <span>{hoveredEdge.data.targetPort || 'Unknown'}</span>
          </div>
          <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <div className="text-muted-foreground">Name:</div>
            <div className="text-foreground">{hoveredEdge.data.description || 'Unknown'}</div>
            <div className="text-muted-foreground">Type:</div>
            <div className="text-foreground capitalize">{hoveredEdge.data.linkType || hoveredEdge.data.protocol || 'Unknown'}</div>
            <div className="text-muted-foreground">Speed:</div>
            <div className="text-foreground">
              {hoveredEdge.data.speed 
                ? (hoveredEdge.data.speed.match(/^\d+$/) || hoveredEdge.data.speed.match(/^a-\d+$/) 
                    ? `${hoveredEdge.data.speed} Mbps` 
                    : hoveredEdge.data.speed)
                : 'Unknown'}
            </div>
            <div className="text-muted-foreground">Link:</div>
            <div className="text-foreground capitalize">
              {hoveredEdge.data.status === 'stale' ? 'Stale (endpoint offline)' : 'Active'}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
