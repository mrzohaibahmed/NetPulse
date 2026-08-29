import { useEffect, useMemo, useState } from 'react'
import {
  ReactFlow,
  Controls,
  Background,
  useNodesState,
  useEdgesState,
} from '@xyflow/react'
import type { ReactFlowInstance } from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import type { TopologyEdge, TopologyNode } from '@/api/topologyService'
import { SwitchNode } from './SwitchNode'
import { buildSwitchGraph } from './switchTopologyUtils'

const nodeTypes = { switchNode: SwitchNode }

interface SwitchTopologyProps {
  switches: TopologyNode[]
  edges: TopologyEdge[]
  onReady?: (instance: ReactFlowInstance) => void
}

export function SwitchTopology({ switches, edges, onReady }: SwitchTopologyProps) {
  const graph = useMemo(() => buildSwitchGraph(switches, edges), [switches, edges])
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null)

  const [nodes, setNodes, onNodesChange] = useNodesState(graph.nodes)
  const [flowEdges, setFlowEdges, onEdgesChange] = useEdgesState(graph.edges)

  useEffect(() => {
    setNodes(graph.nodes)
    setFlowEdges(graph.edges)
  }, [graph, setNodes, setFlowEdges])

  useEffect(() => {
    if (!rfInstance || graph.nodes.length === 0) return
    const frame = window.requestAnimationFrame(() => {
      rfInstance.fitView({ padding: 0.2, maxZoom: 1.25 })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [rfInstance, graph])

  if (switches.length === 0) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-xl border border-dashed border-border/60 bg-muted/10 text-sm text-muted-foreground">
        No switches match your search.
      </div>
    )
  }

  return (
    <div className="relative max-h-[calc(100vh-15rem)] min-h-[420px] overflow-auto rounded-xl border border-border/60 bg-background/40">
      <div
        style={{
          width: Math.max(graph.bounds.width, 960),
          height: Math.max(graph.bounds.height, 420),
          minWidth: '100%',
        }}
      >
        <ReactFlow
          nodes={nodes}
          edges={flowEdges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={nodeTypes}
          onInit={(instance) => {
            setRfInstance(instance)
            onReady?.(instance)
          }}
          minZoom={0.25}
          maxZoom={2}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          panOnDrag
          zoomOnScroll
          proOptions={{ hideAttribution: true }}
        >
          <Controls showInteractive={false} />
          <Background gap={20} size={1} />
        </ReactFlow>
      </div>
    </div>
  )
}
