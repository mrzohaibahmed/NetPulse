import { motion } from 'framer-motion'
import { Globe, Wifi, WifiOff } from 'lucide-react'
import { DEFAULT_SITE_LOCATION, canonicalSiteLocation, ispSlotIdsForLocation } from '@/modules/ping/constants/locations'
import { formatMs, formatRelative } from '@/utils/format'
import type { IspConnection } from '@/types'
import { SectionHeading } from '@/shared/components/SectionHeading'
import { Card, CardContent } from '@/shared/ui/card'
import { cn } from '@/lib/utils'

export const ISP_SLOT_IDS = ispSlotIdsForLocation(DEFAULT_SITE_LOCATION)

export function normalizeIspSlotsForLocation(
  isps: IspConnection[] | undefined,
  location: string = DEFAULT_SITE_LOCATION,
): IspConnection[] {
  const slotIds = ispSlotIdsForLocation(location)
  const locationKey = canonicalSiteLocation(location) || DEFAULT_SITE_LOCATION
  const byId = new Map((isps ?? []).map((isp) => [isp.id, isp]))

  return slotIds.map((id, index) => {
    const existing = byId.get(id)
    if (existing && (canonicalSiteLocation(existing.location) || DEFAULT_SITE_LOCATION) === locationKey) {
      return existing
    }
    return {
      id,
      name: `ISP ${index + 1}`,
      target: '',
      location: locationKey,
      monitor: false,
      status: 'Unknown',
      responseTime: null,
      lastSeen: null,
      createdAt: '',
      updatedAt: '',
    }
  })
}

/** Backward-compatible Mills-site normalization for settings and legacy tests. */
export function normalizeIspSlots(isps: IspConnection[] | undefined): IspConnection[] {
  return normalizeIspSlotsForLocation(isps, DEFAULT_SITE_LOCATION)
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

export function IspStatusCard({
  isp,
  isLoading,
  compact = false,
}: {
  isp: IspConnection
  isLoading?: boolean
  compact?: boolean
}) {
  const online = isp.status === 'Online'
  const offline = isp.status !== 'Online'
  const latency = isLoading && !isp.lastCheckedAt ? 'Checking…' : ispLatencyLabel(isp)
  const lastSeen = isp.lastSeen ? formatRelative(isp.lastSeen) : '—'

  if (compact) {
    return (
      <Card
        className={cn(
          'glass relative min-w-0 overflow-hidden border-border/70 transition-shadow hover:shadow-md',
          online && 'border-success/30 shadow-success/5',
          offline && 'border-danger/40 shadow-danger/10 ring-1 ring-danger/20',
        )}
      >
        <div
          className={cn(
            'pointer-events-none absolute inset-x-0 top-0 h-0.5',
            online && 'bg-success',
            offline && 'bg-danger',
            !online && !offline && 'bg-muted-foreground/30',
          )}
        />
        <CardContent className="space-y-1.5 px-2.5 py-2">
          <div className="flex min-w-0 items-center gap-1.5">
            <div
              className={cn(
                'flex h-5 w-5 shrink-0 items-center justify-center rounded-md',
                online && 'bg-success/15 text-success',
                offline && 'bg-danger/15 text-danger',
                !online && !offline && 'bg-secondary text-muted-foreground',
              )}
            >
              {online ? (
                <Wifi className="h-3 w-3" />
              ) : offline ? (
                <WifiOff className="h-3 w-3" />
              ) : (
                <Globe className="h-3 w-3" />
              )}
            </div>
            <p className="truncate text-xs font-bold leading-tight tracking-tight text-foreground">
              {isp.name}
            </p>
          </div>
          <p className="truncate font-mono text-[10px] leading-tight text-muted-foreground">
            {isp.target || 'No target'}
          </p>
          <div className="min-w-0">
            <p
              className={cn(
                'truncate text-sm font-bold leading-tight tracking-tight',
                online && 'text-foreground',
                offline && 'text-muted-foreground',
              )}
            >
              {latency}
            </p>
            <p className="truncate text-[9px] leading-tight text-muted-foreground">
              Seen{' '}
              <span className="font-medium text-foreground">{lastSeen}</span>
            </p>
          </div>
        </CardContent>
      </Card>
    )
  }

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
        <div className="flex min-w-0 items-center gap-3">
          <div
            className={cn(
              'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg',
              online && 'bg-success/15 text-success',
              offline && 'bg-danger/15 text-danger',
              !online && !offline && 'bg-secondary text-muted-foreground',
            )}
          >
            {online ? (
              <Wifi className="h-4 w-4" />
            ) : offline ? (
              <WifiOff className="h-4 w-4" />
            ) : (
              <Globe className="h-4 w-4" />
            )}
          </div>
          <div className="min-w-0">
            <p className="truncate text-base font-bold tracking-tight text-foreground">{isp.name}</p>
            {isp.target ? (
              <p className="truncate text-xs font-mono text-muted-foreground">{isp.target}</p>
            ) : (
              <p className="text-xs text-muted-foreground">No target configured</p>
            )}
          </div>
        </div>

        <div className="flex shrink-0 flex-col items-end justify-center text-right">
          <p
            className={cn(
              'text-lg font-bold leading-tight tracking-tight',
              online && 'text-foreground',
              offline && 'text-muted-foreground',
            )}
          >
            {latency}
          </p>
          <p className="mt-0.5 text-[10px] text-muted-foreground">
            Last seen:{' '}
            <span className="font-medium text-foreground">{lastSeen}</span>
          </p>
        </div>
      </CardContent>
    </Card>
  )
}
