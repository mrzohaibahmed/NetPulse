import { memo } from 'react'
import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath } from '@xyflow/react'
import type { EdgeProps } from '@xyflow/react'
import { cn } from '@/lib/utils'
import { getPointOnPath, type SwitchEdgeData } from './switchTopologyUtils'

function SpeedLabel({ x, y, text, isDown }: { x: number; y: number; text: string; isDown: boolean }) {
  return (
    <div
      className="nodrag nopan pointer-events-none absolute"
      style={{ transform: `translate(-50%, -50%) translate(${x}px, ${y}px)` }}
    >
      <span
        className={cn(
          'rounded border px-1 py-px text-[9px] font-semibold leading-none shadow-sm',
          isDown
            ? 'border-red-500/60 bg-red-500/10 text-red-700 dark:text-red-300'
            : 'border-border/80 bg-card/95 text-foreground',
        )}
      >
        {text}
      </span>
    </div>
  )
}

export const SwitchEdge = memo(function SwitchEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  data,
}: EdgeProps) {
  const edgeData = (data || {}) as SwitchEdgeData
  const linkStyle = edgeData.linkStyle
  const isDown = linkStyle?.category === 'DOWN'
  const isHighlighted = Boolean(edgeData.highlighted)
  const isDimmed = Boolean(edgeData.dimmed)

  const [edgePath] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 14,
  })

  const labelPoint = getPointOnPath(edgePath, 0.5)
  const stroke = isHighlighted ? '#3b82f6' : linkStyle?.color || '#94a3b8'
  const strokeDasharray = linkStyle?.strokeDasharray
  const label = edgeData.speedLabel || linkStyle?.label || 'Unknown'
  const strokeWidth = (style?.strokeWidth as number | undefined) ?? linkStyle?.strokeWidth ?? 2

  return (
    <>
      {isHighlighted ? (
        <BaseEdge
          id={`${id}-glow`}
          path={edgePath}
          interactionWidth={0}
          style={{
            stroke: '#3b82f6',
            strokeWidth: strokeWidth + 8,
            opacity: 0.25,
          }}
        />
      ) : null}
      <BaseEdge
        id={id}
        path={edgePath}
        interactionWidth={20}
        style={{
          stroke,
          strokeWidth,
          strokeDasharray,
          ...style,
        }}
      />
      <EdgeLabelRenderer>
        <div className={cn(isDimmed && 'opacity-30')}>
          <SpeedLabel x={labelPoint.x} y={labelPoint.y} text={label} isDown={isDown} />
        </div>
      </EdgeLabelRenderer>
    </>
  )
})

export type { SwitchEdgeData }
