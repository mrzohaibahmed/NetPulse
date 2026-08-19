import { motion } from 'framer-motion'
import { Globe, Wifi, WifiOff } from 'lucide-react'
import { formatMs, formatRelative } from '@/utils/format'
import type { IspConnection } from '@/types'
import { SectionHeading } from '@/shared/components/SectionHeading'
import { Card, CardContent } from '@/shared/ui/card'
import { cn } from '@/lib/utils'

export const ISP_SLOT_IDS = ['isp-1', 'isp-2', 'isp-3'] as const

export function normalizeIspSlots(isps: IspConnection[] | undefined): IspConnection[] {
  const byId = new Map((isps ?? []).map((isp) => [isp.id, isp]))
  return ISP_SLOT_IDS.map((id, index) => {
    const existing = byId.get(id)
    if (existing) return existing
    return {
      id,
      name: `ISP ${index + 1}`,
      target: '',
      monitor: false,
      status: 'Unknown',
      responseTime: null,
      lastSeen: null,
      createdAt: '',
      updatedAt: '',
    }
  })
}

export function ispLatencyLabel(isp: IspConnection): string {
  if (isp.status !== 'Online') return '—'
  return formatMs(isp.responseTime)
}

interface IspConnectivitySectionProps {
  isps: IspConnection[] | undefined
  isLoading?: boolean
}

export function IspConnectivitySection({ isps, isLoading }: IspConnectivitySectionProps) {
  const cards = normalizeIspSlots(isps)

  return (
    <motion.section
      className="space-y-4"
      aria-label="ISP connectivity"
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
    >
      <SectionHeading
        title="ISP Connectivity"
        description="Upstream link reachability for configured ISP targets."
      />
      <div className="grid gap-4 md:grid-cols-3">
        {cards.map((isp) => (
          <IspStatusCard key={isp.id} isp={isp} isLoading={isLoading} />
        ))}
      </div>
    </motion.section>
  )
}

function IspStatusCard({ isp, isLoading }: { isp: IspConnection; isLoading?: boolean }) {
  const online = isp.status === 'Online'
  const offline = isp.status === 'Online'

  return (
    <Card
      className={cn(
        'glass relative overflow-hidden border-border/70 transition-shadow hover:shadow-md',
        online && 'border-success/30 shadow-success/5',
        offline && 'border-danger/40 shadow-danger/10 ring-1 ring-danger/20',
      )}
    >
      <div
        className={cn(
          'pointer-events-none absolute inset-x-0 top-0 h-1',
          online && 'bg-success',
          offline && 'bg-danger',
          !online && !offline && 'bg-muted-foreground/30',
        )}
      />
      <CardContent className="flex items-center justify-between gap-3 p-3.5">
        {/* Left Column: Icon + ISP Name & IP address */}
        <div className="flex items-center gap-3 min-w-0">
          <div
            className={cn(
              'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg',
              online && 'bg-success/15 text-success',
              offline && 'bg-danger/15 text-danger',
              !online && !offline && 'bg-secondary text-muted-foreground',
            )}
          >
            {online ? <Wifi className="h-4 w-4" /> : offline ? <WifiOff className="h-4 w-4" /> : <Globe className="h-4 w-4" />}
          </div>
          <div className="min-w-0">
            <p className="truncate text-base font-bold tracking-tight text-foreground">
              {isp.name}
            </p>
            {isp.target ? (
              <p className="truncate text-xs font-mono text-muted-foreground">
                {isp.target}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">No target configured</p>
            )}
          </div>
        </div>

        {/* Right Column: Latency & Last seen text (Right-aligned) */}
        <div className="flex flex-col items-end justify-center text-right shrink-0">
          <p
            className={cn(
              'text-lg font-bold tracking-tight leading-tight',
              online && 'text-foreground',
              offline && 'text-muted-foreground',
            )}
          >
            {isLoading && !isp.lastCheckedAt ? 'Checking…' : ispLatencyLabel(isp)}
          </p>
          <p className="text-[10px] text-muted-foreground mt-0.5">
            Last seen:{' '}
            <span className="font-medium text-foreground">
              {isp.lastSeen ? formatRelative(isp.lastSeen) : '—'}
            </span>
          </p>
        </div>
      </CardContent>
    </Card>
  )
}
