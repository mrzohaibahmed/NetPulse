import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import type { NodeProps } from '@xyflow/react'
import { cn } from '@/lib/utils'
import { isOnlineStatus } from '@/lib/status'

export type SwitchNodeData = {
  hostname: string
  ip: string
  status: string
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
        'flex h-[72px] w-[180px] flex-col items-center justify-center rounded-xl border-2 px-3 py-2 shadow-md',
        online
          ? 'border-success bg-success/20 text-foreground'
          : 'border-danger bg-danger/20 text-foreground',
      )}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!h-2 !w-2 !border-2 !border-background !bg-primary"
      />
      <div
        className="w-full truncate text-center text-sm font-bold leading-tight"
        title={displayHostname(nodeData)}
      >
        {displayHostname(nodeData)}
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
