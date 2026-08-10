import { useEffect, useMemo, useState, memo } from 'react'
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
import type { Node, Edge, NodeProps, EdgeProps } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from 'dagre'
import { Network, Server, Share2 } from 'lucide-react'

import { PageHeader } from '@/shared/components/PageHeader'
import { LoadingState } from '@/shared/components/LoadingState'
import { ErrorState } from '@/shared/components/ErrorState'
import { useTopologySwitches, useLevel1Topology, useFullTopology } from '@/hooks/useTopologyData'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { cn } from '@/lib/utils'

type TopologyNodeData = {
  label: string
  ip: string
  type: string
  status: string
  isKnownDevice: boolean
  isCentral?: boolean
}

type TopologyEdgeData = {
  label?: string
  protocol?: string
}

const NODE_WIDTH = 190
const NODE_HEIGHT = 92
const EDGE_COLOR = '#3b82f6'

function isSwitchType(type: string) {
  return type.toLowerCase().includes('switch')
}

const TopologyDeviceNode = memo(function TopologyDeviceNode({ data }: NodeProps) {
  const nodeData = data as TopologyNodeData
  const switchLike = isSwitchType(nodeData.type || '')

  return (
    <div
      className={cn(
        'flex h-[92px] w-[190px] flex-col justify-center rounded-xl border px-3 py-2 shadow-md',
        'border-border/70 bg-card',
        nodeData.isCentral && 'border-primary ring-1 ring-primary/50 bg-primary/5',
        !nodeData.isKnownDevice && 'border-dashed border-border/60 bg-muted/30',
      )}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!h-2.5 !w-2.5 !border-2 !border-background !bg-primary"
      />
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
          <div className="truncate text-sm font-semibold leading-tight text-foreground" title={nodeData.label}>
            {nodeData.label || 'Unknown'}
          </div>
          <div className="truncate text-[11px] leading-tight text-muted-foreground" title={nodeData.type}>
            {nodeData.type || 'Unknown'}
          </div>
          {nodeData.ip ? (
            <div className="truncate font-mono text-[10px] leading-tight text-muted-foreground/90" title={nodeData.ip}>
              {nodeData.ip}
            </div>
          ) : null}
        </div>
      </div>
      <Handle
        type="source"
        position={Position.Bottom}
        className="!h-2.5 !w-2.5 !border-2 !border-background !bg-primary"
      />
    </div>
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
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 12,
  })

  const portLabel = (edgeData.label || '').trim()

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          stroke: EDGE_COLOR,
          strokeWidth: 2,
          ...style,
        }}
      />
      {portLabel ? (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan pointer-events-none absolute"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            }}
          >
            <span className="rounded-md border border-border/80 bg-card px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground shadow-sm">
              {portLabel}
            </span>
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  )
})

const nodeTypes = { topologyDevice: TopologyDeviceNode }
const edgeTypes = { topologyEdge: TopologyEdge }

function getLayoutedElements(nodes: Node[], edges: Edge[], direction = 'TB') {
  if (nodes.length === 0) return { nodes, edges }

  const graph = new dagre.graphlib.Graph()
  graph.setDefaultEdgeLabel(() => ({}))
  graph.setGraph({ rankdir: direction, nodesep: 90, ranksep: 130, marginx: 24, marginy: 24 })

  const isHorizontal = direction === 'LR'

  nodes.forEach((node) => {
    graph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  })

  edges.forEach((edge) => {
    graph.setEdge(edge.source, edge.target)
  })

  dagre.layout(graph)

  const layoutedNodes = nodes.map((node) => {
    const pos = graph.node(node.id)
    return {
      ...node,
      position: {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - NODE_HEIGHT / 2,
      },
      targetPosition: isHorizontal ? Position.Left : Position.Top,
      sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
    } as Node
  })

  return { nodes: layoutedNodes, edges }
}

export function TopologyPage() {
  const [selectedSwitchId, setSelectedSwitchId] = useState<string | null>(null)
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])

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
    if (!activeData?.nodes || !activeData?.edges) {
      setNodes([])
      setEdges([])
      return
    }

    const flowNodes: Node[] = activeData.nodes.map((n) => ({
      id: n.id,
      type: 'topologyDevice',
      position: { x: 0, y: 0 },
      data: {
        label: n.label,
        ip: n.ip,
        type: n.type,
        status: n.status,
        isKnownDevice: n.isKnownDevice,
        isCentral: selectedSwitchId != null && n.id === selectedSwitchId,
      } satisfies TopologyNodeData,
    }))

    const flowEdges: Edge[] = activeData.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: 'topologyEdge',
      animated: true,
      data: {
        label: e.label,
        protocol: e.protocol,
      } satisfies TopologyEdgeData,
      style: {
        stroke: EDGE_COLOR,
        strokeWidth: 2,
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 16,
        height: 16,
        color: EDGE_COLOR,
      },
    }))

    const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
      flowNodes,
      flowEdges,
      'TB',
    )
    setNodes(layoutedNodes)
    setEdges(layoutedEdges)
  }, [activeData, selectedSwitchId, setNodes, setEdges])

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
    <div className="np-page flex h-[calc(100vh-2rem)] flex-col">
      <PageHeader
        title="Network Topology"
        description="Interactive Level 1 and Level 2 graphs built from CDP/LLDP neighbors."
      />

      <div className="mt-4 flex min-h-0 flex-1 flex-col gap-4 overflow-hidden">
        {/* Top horizontal selector cards */}
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
                  <CardTitle className="flex items-center gap-2 truncate text-sm" title={sw.label}>
                    <Network className="h-4 w-4 shrink-0 text-primary" />
                    <span className="truncate">{sw.label}</span>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-0.5 p-4 pt-0 text-xs text-muted-foreground">
                  <div>IP: {sw.ip || 'Unknown'}</div>
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

        {/* Graph canvas below cards */}
        <div className="relative min-h-0 flex-1 overflow-hidden rounded-xl border border-border/70 bg-card/50">
          {isLoadingActive ? (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-card/50 backdrop-blur-sm">
              <LoadingState label="Drawing topology…" />
            </div>
          ) : null}

          {nodes.length === 0 && !isLoadingActive ? (
            <div className="absolute inset-0 z-10 flex items-center justify-center">
              <div className="rounded-xl border border-border/70 bg-card/80 px-6 py-4 text-sm text-muted-foreground backdrop-blur-md">
                No topology links found for this view.
              </div>
            </div>
          ) : null}

          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            fitViewOptions={{ padding: 0.25 }}
            attributionPosition="bottom-left"
            proOptions={{ hideAttribution: true }}
            minZoom={0.2}
            maxZoom={1.75}
            defaultEdgeOptions={{
              type: 'topologyEdge',
              animated: true,
            }}
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
      </div>
    </div>
  )
}
