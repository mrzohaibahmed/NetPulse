import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import type { NodeProps } from '@xyflow/react'
import { Network } from 'lucide-react'
import { cn } from '@/lib/utils'
import { isOnlineStatus } from '@/lib/status'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip'

export type SwitchNodeData = {
  hostname: string
  ip: string
  status: string
  highlighted?: boolean
  dimmed?: boolean
}

export const SwitchNode = memo(function SwitchNode({ data }: NodeProps) {
  const nodeData = data as SwitchNodeData
  const online = isOnlineStatus(nodeData.status)
  const statusLabel = online ? 'Online' : nodeData.status || 'Offline'

  return (
    <Tooltip delayDuration={200}>
      <TooltipTrigger asChild>
        <div
          className={cn(
            'flex w-[80px] flex-col items-center transition-opacity',
            nodeData.dimmed && 'opacity-35',
          )}
        >
          <Handle
            type="target"
            position={Position.Top}
            className="!-top-px !left-1/2 !h-1 !w-1 !-translate-x-1/2 !border-0 !bg-transparent !opacity-0"
          />

          <div
            className={cn(
              'relative flex h-9 w-[52px] items-center justify-center rounded-md border shadow-sm',
              online
                ? 'border-emerald-500/80 bg-emerald-600/90'
                : 'border-red-500/80 bg-red-600/90',
              nodeData.highlighted && 'ring-2 ring-primary ring-offset-1 ring-offset-background',
            )}
          >
            <Network className="h-3.5 w-3.5 text-white" strokeWidth={2.25} aria-hidden />
            <span
              className={cn(
                'absolute -bottom-1 left-1/2 h-1.5 w-1.5 -translate-x-1/2 rounded-full border border-background',
                online ? 'bg-emerald-400' : 'bg-red-400',
              )}
              aria-hidden
            />
          </div>

          <div
            className="mt-1 max-w-[80px] truncate text-center text-[9px] font-semibold leading-tight text-foreground"
            title={nodeData.hostname}
          >
            {nodeData.hostname}
          </div>
          {nodeData.ip ? (
            <div
              className="max-w-[80px] truncate text-center font-mono text-[8px] leading-tight text-muted-foreground"
              title={nodeData.ip}
            >
              {nodeData.ip}
            </div>
          ) : null}

          <Handle
            type="source"
            position={Position.Bottom}
            className="!-bottom-px !left-1/2 !h-1 !w-1 !-translate-x-1/2 !border-0 !bg-transparent !opacity-0"
          />
        </div>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs space-y-0.5 p-2 text-xs">
        <div className="font-semibold text-foreground">{nodeData.hostname}</div>
        {nodeData.ip ? <div className="font-mono text-muted-foreground">{nodeData.ip}</div> : null}
        <div className="text-muted-foreground">{statusLabel}</div>
      </TooltipContent>
    </Tooltip>
  )
})
