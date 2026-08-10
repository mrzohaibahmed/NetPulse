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
  const offline = isp.status === 'Offline'

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
      <CardContent className="space-y-4 p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold tracking-tight">{isp.name}</p>
            {isp.target ? (
              <p className="truncate text-xs text-muted-foreground">{isp.target}</p>
            ) : (
              <p className="text-xs text-muted-foreground">No target configured</p>
            )}
          </div>
          <div
            className={cn(
              'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg',
              online && 'bg-success/15 text-success',
              offline && 'bg-danger/15 text-danger',
              !online && !offline && 'bg-secondary text-muted-foreground',
            )}
          >
            {online ? <Wifi className="h-4 w-4" /> : offline ? <WifiOff className="h-4 w-4" /> : <Globe className="h-4 w-4" />}
          </div>
        </div>

        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                'h-2.5 w-2.5 rounded-full',
                online && 'bg-success shadow-[0_0_8px_rgba(34,197,94,0.65)]',
                offline && 'bg-danger shadow-[0_0_8px_rgba(239,68,68,0.55)]',
                !online && !offline && 'bg-muted-foreground/50',
              )}
              aria-hidden="true"
            />
            <span
              className={cn(
                'text-xs font-bold uppercase tracking-wider',
                online && 'text-success',
                offline && 'text-danger',
                !online && !offline && 'text-muted-foreground',
              )}
            >
              {isLoading && !isp.lastCheckedAt ? 'Checking…' : isp.status}
            </span>
          </div>
          <p
            className={cn(
              'text-2xl font-bold tracking-tight',
              online && 'text-foreground',
              offline && 'text-muted-foreground',
            )}
          >
            {ispLatencyLabel(isp)}
          </p>
        </div>

        <p className="text-xs text-muted-foreground">
          Last seen:{' '}
          <span className="font-medium text-foreground">
            {isp.lastSeen ? formatRelative(isp.lastSeen) : '—'}
          </span>
        </p>
      </CardContent>
    </Card>
  )
}
