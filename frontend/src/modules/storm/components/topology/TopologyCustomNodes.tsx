import { Handle, Position } from '@xyflow/react'

export function SwitchNode({ data }: { data: { label: string; ip: string; status: string } }) {
  const isUp = data.status.toLowerCase() === 'up'
  return (
    <div className="flex flex-col items-center justify-center p-4 min-w-[150px] bg-card border border-border rounded-xl shadow-lg shadow-purple-500/10 backdrop-blur-md transition-all hover:border-purple-500/50 hover:shadow-purple-500/20">
      <Handle type="target" position={Position.Top} className="w-3 h-3 bg-purple-500" />
      <div className="w-12 h-12 rounded-full bg-purple-500/10 flex items-center justify-center mb-3">
        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-purple-500"><rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect><rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect><line x1="6" y1="6" x2="6.01" y2="6"></line><line x1="6" y1="18" x2="6.01" y2="18"></line></svg>
      </div>
      <div className="text-sm font-semibold text-foreground text-center">{data.label}</div>
      <div className="text-xs text-muted-foreground text-center mt-1">{data.ip}</div>
      <div className="mt-2 px-2 py-0.5 rounded-full bg-background border border-border text-[10px] font-medium uppercase tracking-wider flex items-center gap-1.5">
        <div className={`w-1.5 h-1.5 rounded-full ${isUp ? 'bg-emerald-500' : 'bg-rose-500'}`}></div>
        {data.status}
      </div>
      <Handle type="source" position={Position.Bottom} className="w-3 h-3 bg-purple-500" />
    </div>
  )
}

export function EndpointNode({ data }: { data: { label: string; ip: string; platform: string } }) {
  return (
    <div className="flex flex-col items-center justify-center p-3 min-w-[120px] bg-card/80 border border-border rounded-xl shadow-md backdrop-blur-sm transition-all hover:border-blue-500/50 hover:shadow-blue-500/20">
      <Handle type="target" position={Position.Top} className="w-3 h-3 bg-blue-500" />
      <div className="w-10 h-10 rounded-full bg-blue-500/10 flex items-center justify-center mb-2">
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-blue-500"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect><line x1="8" y1="21" x2="16" y2="21"></line><line x1="12" y1="17" x2="12" y2="21"></line></svg>
      </div>
      <div className="text-sm font-medium text-foreground text-center truncate max-w-[150px]">{data.label}</div>
      {data.ip && <div className="text-xs text-muted-foreground text-center mt-0.5">{data.ip}</div>}
      {data.platform && <div className="text-[10px] text-muted-foreground/70 text-center mt-1 truncate max-w-[150px]">{data.platform}</div>}
      <Handle type="source" position={Position.Bottom} className="w-3 h-3 bg-blue-500" />
    </div>
  )
}
