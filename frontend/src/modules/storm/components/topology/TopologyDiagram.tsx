import { useEffect, useCallback, useMemo } from 'react'
import {
  ReactFlow,
  useNodesState,
  useEdgesState,
  Controls,
  Background,
  Panel,
  MarkerType,
  BackgroundVariant
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { SwitchNode, EndpointNode } from './TopologyCustomNodes'
import { Loader2 } from 'lucide-react'
import { getFullTopology, getSwitchTopology } from '@/api'
import { useQuery } from '@tanstack/react-query'
import dagre from 'dagre'

const nodeTypes = {
  switchNode: SwitchNode,
  endpointNode: EndpointNode,
}

// Layout utility using dagre
const getLayoutedElements = (nodes: any[], edges: any[], direction = 'TB') => {
  const dagreGraph = new dagre.graphlib.Graph()
  dagreGraph.setDefaultEdgeLabel(() => ({}))
  
  const nodeWidth = 180
  const nodeHeight = 120

  dagreGraph.setGraph({ rankdir: direction, nodesep: 100, ranksep: 100 })

  nodes.forEach((node) => {
    dagreGraph.setNode(node.id, { width: nodeWidth, height: nodeHeight })
  })

  edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target)
  })

  dagre.layout(dagreGraph)

  const newNodes = nodes.map((node) => {
    const nodeWithPosition = dagreGraph.node(node.id)
    return {
      ...node,
      position: {
        x: nodeWithPosition.x - nodeWidth / 2,
        y: nodeWithPosition.y - nodeHeight / 2,
      },
    }
  })

  return { nodes: newNodes, edges }
}

interface TopologyDiagramProps {
  deviceId?: string // If not provided, load full topology
}

export function TopologyDiagram({ deviceId }: TopologyDiagramProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['topology', deviceId || 'full'],
    queryFn: () => deviceId ? getSwitchTopology(deviceId) : getFullTopology(),
    refetchInterval: 30000, // Real-time updates every 30s
  })

  useEffect(() => {
    if (data?.data) {
      const rawNodes = data.data.nodes || []
      
      const rawEdges = (data.data.edges || []).map((edge: any) => ({
        ...edge,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 20,
          height: 20,
          color: edge.style?.stroke || '#b1b1b7',
        },
      }))

      // Apply automatic layout
      const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
        rawNodes,
        rawEdges,
        'TB'
      )

      setNodes(layoutedNodes)
      setEdges(layoutedEdges)
    }
  }, [data, setNodes, setEdges])

  if (isLoading && nodes.length === 0) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center bg-card border border-border rounded-xl">
        <Loader2 className="w-8 h-8 text-primary animate-spin mb-4" />
        <p className="text-muted-foreground font-medium">Building topology map...</p>
      </div>
    )
  }

  if (isError) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center bg-card border border-border rounded-xl">
        <p className="text-destructive font-medium">Failed to load topology.</p>
        <button 
          onClick={() => refetch()}
          className="mt-4 px-4 py-2 bg-primary/10 hover:bg-primary/20 text-primary rounded-md transition-colors"
        >
          Retry
        </button>
      </div>
    )
  }

  return (
    <div className="w-full h-full bg-background border border-border rounded-xl overflow-hidden relative">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView
        minZoom={0.2}
        maxZoom={2}
        className="bg-dot-pattern"
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} color="var(--border)" />
        <Controls className="bg-card border-border fill-foreground" />
        
        <Panel position="top-right" className="bg-card/80 backdrop-blur border border-border rounded-lg p-2 shadow-sm text-xs font-medium">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-1.5">
              <div className="w-3 h-0.5 bg-purple-500"></div>
              <span>Trunk</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-3 h-0.5 bg-blue-500"></div>
              <span>Access</span>
            </div>
          </div>
        </Panel>
      </ReactFlow>
    </div>
  )
}
