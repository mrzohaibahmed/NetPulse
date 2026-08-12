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
      <CardContent className="flex flex-col gap-2 p-2.5">
        <div className="flex items-start justify-between gap-1.5">
          <div className="min-w-0">
            <p className="truncate text-xs font-semibold tracking-tight leading-none mb-0.5">{isp.name}</p>
            {isp.target ? (
              <p className="truncate text-[10px] text-muted-foreground leading-none">{isp.target}</p>
            ) : (
              <p className="text-[10px] text-muted-foreground leading-none">No target configured</p>
            )}
          </div>
          <div
            className={cn(
              'flex h-6 w-6 shrink-0 items-center justify-center rounded-md',
              online && 'bg-success/15 text-success',
              offline && 'bg-danger/15 text-danger',
              !online && !offline && 'bg-secondary text-muted-foreground',
            )}
          >
            {online ? <Wifi className="h-3 w-3" /> : offline ? <WifiOff className="h-3 w-3" /> : <Globe className="h-3 w-3" />}
          </div>
        </div>

        <div>
          <div className="flex items-center gap-1.5 mb-1">
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                online && 'bg-success shadow-[0_0_6px_rgba(34,197,94,0.65)]',
                offline && 'bg-danger shadow-[0_0_6px_rgba(239,68,68,0.55)]',
                !online && !offline && 'bg-muted-foreground/50',
              )}
              aria-hidden="true"
            />
            <span
              className={cn(
                'text-[9px] font-bold uppercase tracking-wider leading-none',
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
              'text-base font-bold tracking-tight leading-none',
              online && 'text-foreground',
              offline && 'text-muted-foreground',
            )}
          >
            {ispLatencyLabel(isp)}
          </p>
        </div>

        <p className="text-[9px] text-muted-foreground mt-0.5 leading-none">
          Last seen:{' '}
          <span className="font-medium text-foreground">
            {isp.lastSeen ? formatRelative(isp.lastSeen) : '—'}
          </span>
        </p>
      </CardContent>
    </Card>
  )
}
