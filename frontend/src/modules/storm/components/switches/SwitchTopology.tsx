import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  Controls,
  Background,
  useNodesState,
  useEdgesState,
} from '@xyflow/react'
import type { Edge, Node, ReactFlowInstance } from '@xyflow/react'
import { Loader2, Save } from 'lucide-react'
import { toast } from 'sonner'
import '@xyflow/react/dist/style.css'

import type { TopologyEdge, TopologyNode } from '@/api/topologyService'
import { useTopologyLayout, useSaveTopologyLayoutMutation } from '@/hooks/useTopologyData'
import { Button } from '@/shared/ui/button'
import { cn } from '@/lib/utils'
import { SwitchNode } from './SwitchNode'
import {
  buildSwitchGraph,
  layoutPositionsFromSaved,
  SWITCHES_LAYOUT_VIEW_KEY,
} from './switchTopologyUtils'

const nodeTypes = { switchNode: SwitchNode }

type LayoutSaveStatus = 'idle' | 'saved' | 'unsaved' | 'saving' | 'error'

interface SwitchTopologyProps {
  switches: TopologyNode[]
  edges: TopologyEdge[]
}

export function SwitchTopology({ switches, edges }: SwitchTopologyProps) {
  const layoutQuery = useTopologyLayout(SWITCHES_LAYOUT_VIEW_KEY)
  const saveLayoutMutation = useSaveTopologyLayoutMutation()

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [flowEdges, setFlowEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [canvasSize, setCanvasSize] = useState({ width: 960, height: 420 })
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null)
  const [saveStatus, setSaveStatus] = useState<LayoutSaveStatus>('idle')

  const shouldFitViewRef = useRef(true)
  const sessionPositionsRef = useRef<Record<string, { x: number; y: number }>>({})

  const savedPositions = useMemo(
    () => layoutPositionsFromSaved(layoutQuery.data),
    [layoutQuery.data],
  )

  useEffect(() => {
    if (layoutQuery.isLoading) return
    if (layoutQuery.data) {
      setSaveStatus((prev) => (prev === 'unsaved' || prev === 'saving' || prev === 'error' ? prev : 'saved'))
    } else {
      setSaveStatus((prev) => (prev === 'unsaved' || prev === 'saving' || prev === 'error' ? prev : 'idle'))
    }
  }, [layoutQuery.data, layoutQuery.isLoading])

  useEffect(() => {
    if (switches.length === 0) {
      setNodes([])
      setFlowEdges([])
      return
    }

    if (layoutQuery.isLoading) return

    const positionMap = {
      ...savedPositions,
      ...sessionPositionsRef.current,
    }

    const graph = buildSwitchGraph(switches, edges, positionMap)

    sessionPositionsRef.current = Object.fromEntries(
      graph.nodes.map((node) => [node.id, node.position]),
    )

    setNodes(graph.nodes)
    setFlowEdges(graph.edges)
    setCanvasSize(graph.bounds)
    shouldFitViewRef.current = true
  }, [switches, edges, savedPositions, layoutQuery.isLoading, setNodes, setFlowEdges])

  useEffect(() => {
    if (!rfInstance || nodes.length === 0 || !shouldFitViewRef.current) return
    const frame = window.requestAnimationFrame(() => {
      rfInstance.fitView({ padding: 0.2, maxZoom: 1.25, duration: 250 })
      shouldFitViewRef.current = false
    })
    return () => window.cancelAnimationFrame(frame)
  }, [rfInstance, nodes])

  const onNodeDragStop = useCallback(() => {
    const currentNodes = rfInstance?.getNodes() ?? nodes
    sessionPositionsRef.current = Object.fromEntries(
      currentNodes.map((node) => [node.id, node.position]),
    )
    setSaveStatus('unsaved')
  }, [nodes, rfInstance])

  const handleSaveTopology = useCallback(async () => {
    const currentNodes = rfInstance?.getNodes() ?? nodes
    const currentEdges = rfInstance?.getEdges() ?? flowEdges
    sessionPositionsRef.current = Object.fromEntries(
      currentNodes.map((node) => [node.id, node.position]),
    )
    setSaveStatus('saving')
    try {
      await saveLayoutMutation.mutateAsync({
        viewKey: SWITCHES_LAYOUT_VIEW_KEY,
        nodes: currentNodes.map((node) => ({
          id: node.id,
          position: { x: node.position.x, y: node.position.y },
        })),
        edges: currentEdges.map((edge) => ({
          id: edge.id,
          source: edge.source,
          target: edge.target,
        })),
      })
      setSaveStatus('saved')
      toast.success('Topology layout saved')
    } catch (err) {
      setSaveStatus('error')
      toast.error(err instanceof Error ? err.message : 'Failed to save topology layout')
    }
  }, [flowEdges, nodes, rfInstance, saveLayoutMutation])

  const saveStatusLabel =
    saveStatus === 'saving'
      ? 'Saving…'
      : saveStatus === 'unsaved'
        ? 'Unsaved changes'
        : saveStatus === 'error'
          ? 'Save failed'
          : saveStatus === 'saved'
            ? 'Saved'
            : 'Not saved yet'

  if (switches.length === 0) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-xl border border-dashed border-border/60 bg-muted/10 text-sm text-muted-foreground">
        No switches match your search.
      </div>
    )
  }

  return (
    <div className="relative min-h-[420px] max-h-[calc(100vh-15rem)] rounded-xl border border-border/60 bg-background/40">
      <div className="pointer-events-none absolute right-3 top-3 z-20">
        <div className="pointer-events-auto flex flex-wrap items-center gap-2 rounded-lg border border-border/70 bg-card/95 px-2.5 py-1.5 text-xs font-medium text-foreground shadow-sm backdrop-blur-md">
          <span
            className={cn(
              'rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide',
              saveStatus === 'unsaved' && 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
              saveStatus === 'saved' && 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
              saveStatus === 'saving' && 'bg-primary/10 text-primary',
              saveStatus === 'error' && 'bg-destructive/15 text-destructive',
              saveStatus === 'idle' && 'bg-muted text-muted-foreground',
            )}
          >
            {saveStatusLabel}
          </span>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="h-7 gap-1.5 px-2 text-xs"
            disabled={nodes.length === 0 || saveStatus === 'saving' || saveLayoutMutation.isPending}
            onClick={() => void handleSaveTopology()}
          >
            {saveStatus === 'saving' || saveLayoutMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Save className="h-3.5 w-3.5" />
            )}
            {saveStatus === 'saving' || saveLayoutMutation.isPending ? 'Saving…' : 'Save Topology'}
          </Button>
        </div>
      </div>

      {layoutQuery.isLoading ? (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/60 backdrop-blur-[1px]">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : null}

      <div className="max-h-[calc(100vh-15rem)] min-h-[420px] overflow-auto">
        <div
          style={{
            width: Math.max(canvasSize.width, 960),
            height: Math.max(canvasSize.height, 420),
            minWidth: '100%',
          }}
        >
          <ReactFlow
            nodes={nodes}
            edges={flowEdges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeDragStop={onNodeDragStop}
            nodeTypes={nodeTypes}
            onInit={setRfInstance}
            minZoom={0.25}
            maxZoom={2}
            nodesDraggable
            nodesConnectable={false}
            elementsSelectable={false}
            panOnDrag
            zoomOnScroll
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={20} size={1} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      </div>
    </div>
  )
}
