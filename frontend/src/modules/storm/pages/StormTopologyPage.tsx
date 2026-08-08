import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getDevices } from '@/api'
import { Network, Router, ArrowLeft, Activity } from 'lucide-react'
import { TopologyDiagram } from '../components/topology/TopologyDiagram'
import { Button } from '@/shared/ui/button'

export function StormTopologyPage() {
  const [activeView, setActiveView] = useState<{ type: 'grid' | 'full' | 'switch', deviceId?: string }>({ type: 'grid' })

  // Fetch only switches/devices
  const { data: devicesData, isLoading } = useQuery({
    queryKey: ['devices'],
    queryFn: () => getDevices({ limit: 100 }), 
  })

  // Filter to only show actual network switches/routers (avoiding random endpoints)
  const devices = (devicesData?.data || []).filter((d: any) => {
    const type = (d.deviceType || '').toLowerCase()
    return type.includes('switch') || type.includes('router') || type.includes('firewall') || type.includes('core')
  })

  if (activeView.type !== 'grid') {
    const isFull = activeView.type === 'full'
    const device = isFull ? null : devices.find(d => d._id === activeView.deviceId)
    
    return (
      <div className="flex flex-col h-[calc(100vh-6rem)] gap-4">
        <div className="flex items-center gap-4">
          <Button 
            variant="ghost" 
            size="sm" 
            onClick={() => setActiveView({ type: 'grid' })}
            className="flex items-center gap-2"
          >
            <ArrowLeft className="w-4 h-4" /> Back to Devices
          </Button>
          <div className="flex-1">
            <h1 className="text-xl font-semibold flex items-center gap-2">
              {isFull ? (
                <><Network className="w-5 h-5 text-purple-500" /> Full Network Topology</>
              ) : (
                <><Router className="w-5 h-5 text-blue-500" /> Switch Topology: {device?.hostname || 'Unknown'}</>
              )}
            </h1>
            <p className="text-sm text-muted-foreground">
              {isFull ? 'Level 2 physical connections across the entire network.' : 'Level 1 direct physical connections for this switch.'}
            </p>
          </div>
        </div>
        
        <div className="flex-1 bg-card rounded-xl border border-border shadow-sm overflow-hidden">
           <TopologyDiagram deviceId={activeView.deviceId} />
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Network Topology</h1>
        <p className="text-muted-foreground mt-1">Visualize physical connections between your devices.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {/* Full Topology Card */}
        <div 
          onClick={() => setActiveView({ type: 'full' })}
          className="group relative flex flex-col p-5 bg-gradient-to-br from-purple-500/10 to-blue-500/10 border border-purple-500/20 rounded-xl cursor-pointer hover:border-purple-500/50 hover:shadow-lg hover:shadow-purple-500/10 transition-all overflow-hidden"
        >
          <div className="absolute -right-4 -top-4 w-24 h-24 bg-purple-500/20 rounded-full blur-2xl group-hover:bg-purple-500/30 transition-all"></div>
          
          <div className="w-12 h-12 rounded-lg bg-purple-500/20 flex items-center justify-center mb-4 text-purple-500 group-hover:scale-110 transition-transform">
            <Network className="w-6 h-6" />
          </div>
          
          <h3 className="text-lg font-semibold text-foreground">Full Topology</h3>
          <p className="text-sm text-muted-foreground mt-1">View the complete Level 2 network diagram showing all switches and endpoints.</p>
        </div>

        {/* Individual Switch Cards */}
        {isLoading ? (
          Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="flex flex-col p-5 bg-card border border-border rounded-xl animate-pulse">
              <div className="w-12 h-12 rounded-lg bg-muted mb-4"></div>
              <div className="h-5 w-24 bg-muted rounded mb-2"></div>
              <div className="h-4 w-full bg-muted rounded"></div>
            </div>
          ))
        ) : (
          devices.map((device) => (
            <div 
              key={device._id}
              onClick={() => setActiveView({ type: 'switch', deviceId: device._id })}
              className="group flex flex-col p-5 bg-card border border-border rounded-xl cursor-pointer hover:border-blue-500/50 hover:shadow-lg hover:shadow-blue-500/5 transition-all"
            >
              <div className="flex justify-between items-start mb-4">
                <div className="w-12 h-12 rounded-lg bg-blue-500/10 flex items-center justify-center text-blue-500 group-hover:scale-110 transition-transform">
                  <Router className="w-6 h-6" />
                </div>
                <div className="px-2 py-1 rounded-full bg-background border border-border text-[10px] font-medium uppercase flex items-center gap-1.5">
                   <div className={`w-1.5 h-1.5 rounded-full ${device.status?.toLowerCase() === 'up' ? 'bg-emerald-500' : 'bg-rose-500'}`}></div>
                   {device.status || 'Unknown'}
                </div>
              </div>
              
              <h3 className="text-lg font-semibold text-foreground">{device.hostname}</h3>
              <div className="flex items-center gap-2 mt-1 text-sm text-muted-foreground">
                <Activity className="w-3.5 h-3.5" />
                {device.ipAddress}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
