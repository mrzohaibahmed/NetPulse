import { motion } from 'framer-motion'
import { Globe, Server } from 'lucide-react'
import { Link } from 'react-router-dom'
import {
  IspStatusCard,
  normalizeIspSlotsForLocation,
} from '@/modules/ping/components/IspConnectivitySection'
import { formatMs, formatRelative } from '@/utils/format'
import type { SiteMonitoringSite, SiteMonitoringServer } from '@/types'
import { SectionHeading } from '@/shared/components/SectionHeading'
import { EmptyState } from '@/shared/components/EmptyState'
import { Card, CardContent } from '@/shared/ui/card'
import { cn } from '@/lib/utils'

interface SiteMonitoringSectionProps {
  sites: SiteMonitoringSite[] | undefined
  isLoading?: boolean
}

export function SiteMonitoringSection({ sites, isLoading }: SiteMonitoringSectionProps) {
  const siteList = sites ?? []

  return (
    <motion.section
      className="space-y-6"
      aria-label="Site monitoring"
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
    >
      <SectionHeading
        title="Site Monitoring"
        description="ISP connectivity and server reachability grouped by location."
      />

      {siteList.length === 0 && !isLoading ? (
        <EmptyState
          title="No sites configured"
          description="Add server devices with a location or configure ISP links in Settings."
          icon={Globe}
        />
      ) : (
        <div className="space-y-8">
          {siteList.map((site) => (
            <SitePanel key={site.name} site={site} isLoading={isLoading} />
          ))}
        </div>
      )}
    </motion.section>
  )
}

function SitePanel({ site, isLoading }: { site: SiteMonitoringSite; isLoading?: boolean }) {
  const isps = normalizeIspSlotsForLocation(site.isps, site.name)

  return (
    <section className="space-y-4" aria-label={`${site.name} monitoring`}>
      <div className="border-b border-border/70 pb-2">
        <h3 className="text-sm font-bold uppercase tracking-widest text-foreground">{site.name}</h3>
      </div>

      <div className="space-y-3">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          ISP Connectivity
        </p>
        <div className="grid gap-4 md:grid-cols-3">
          {isps.map((isp) => (
            <IspStatusCard key={isp.id} isp={isp} isLoading={isLoading} />
          ))}
        </div>
      </div>

      <div className="space-y-3">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          Server Monitoring
        </p>
        {site.servers.length === 0 ? (
          <Card className="glass border-dashed border-border/70">
            <CardContent className="p-4 text-sm text-muted-foreground">
              No server devices configured
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4 md:grid-cols-3">
            {site.servers.map((server) => (
              <ServerStatusCard key={server.id} server={server} isLoading={isLoading} />
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

function serverLatencyLabel(server: SiteMonitoringServer): string {
  if (server.status !== 'Online') return '—'
  return formatMs(server.responseTime)
}

function ServerStatusCard({
  server,
  isLoading,
}: {
  server: SiteMonitoringServer
  isLoading?: boolean
}) {
  const online = server.status === 'Online'
  const offline = server.status !== 'Online'

  return (
    <Link to={`/devices/${server.id}`} className="block">
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
              <Server className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <p className="truncate text-base font-bold tracking-tight text-foreground">
                {server.hostname}
              </p>
              <p className="truncate text-xs font-mono text-muted-foreground">{server.ipAddress}</p>
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
              {isLoading && !server.lastCheckedAt ? 'Checking…' : serverLatencyLabel(server)}
            </p>
            <p className="mt-0.5 text-[10px] text-muted-foreground">
              Last seen:{' '}
              <span className="font-medium text-foreground">
                {server.lastSeen ? formatRelative(server.lastSeen) : '—'}
              </span>
            </p>
          </div>
        </CardContent>
      </Card>
    </Link>
  )
}
