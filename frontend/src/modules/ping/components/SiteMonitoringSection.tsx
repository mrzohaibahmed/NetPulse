import { motion } from 'framer-motion'
import { Globe } from 'lucide-react'
import { EmptyState } from '@/shared/components/EmptyState'
import { LocationCard } from '@/modules/ping/components/LocationCard'
import type { SiteMonitoringSite } from '@/types'

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
      {siteList.length === 0 && !isLoading ? (
        <EmptyState
          title="No sites configured"
          description="Add server devices with a location or configure ISP links in Settings."
          icon={Globe}
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-3">
          {siteList.map((site) => (
            <LocationCard key={site.name} site={site} isLoading={isLoading} />
          ))}
        </div>
      )}
    </motion.section>
  )
}
