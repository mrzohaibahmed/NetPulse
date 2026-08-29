import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import type { NodeProps } from '@xyflow/react'
import { Network } from 'lucide-react'
import { cn } from '@/lib/utils'
import { isOnlineStatus } from '@/lib/status'

export type SwitchNodeData = {
  hostname: string
  ip: string
  status: string
  highlighted?: boolean
  dimmed?: boolean
}

function displayHostname(data: SwitchNodeData) {
  return data.hostname || 'Unknown'
}

export const SwitchNode = memo(function SwitchNode({ data }: NodeProps) {
  const nodeData = data as SwitchNodeData
  const online = isOnlineStatus(nodeData.status)

  return (
    <div
      className={cn(
        'flex h-[72px] w-[180px] flex-col items-center justify-center rounded-xl border-2 px-3 py-2 shadow-md transition-opacity',
        online
          ? 'border-success bg-success/20 text-foreground'
          : 'border-danger bg-danger/20 text-foreground',
        nodeData.highlighted && 'ring-2 ring-primary ring-offset-2 ring-offset-background',
        nodeData.dimmed && 'opacity-35',
      )}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!h-2 !w-2 !border-2 !border-background !bg-primary"
      />
      <div className="flex w-full min-w-0 items-center justify-center gap-1.5">
        <Network
          className={cn('h-4 w-4 shrink-0', online ? 'text-success' : 'text-danger')}
          aria-hidden
        />
        <div
          className="min-w-0 truncate text-center text-sm font-bold leading-tight"
          title={displayHostname(nodeData)}
        >
          {displayHostname(nodeData)}
        </div>
      </div>
      <div
        className="mt-1 w-full truncate text-center font-mono text-xs text-muted-foreground"
        title={nodeData.ip}
      >
        {nodeData.ip || '—'}
      </div>
      <Handle
        type="source"
        position={Position.Bottom}
        className="!h-2 !w-2 !border-2 !border-background !bg-primary"
      />
    </div>
  )
})
