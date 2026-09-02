/** Initial supported site locations for device and ISP grouping. */
export const SITE_LOCATIONS = ['Mill', 'Karachi', 'Lahore'] as const

export type SiteLocation = (typeof SITE_LOCATIONS)[number]

export const DEFAULT_SITE_LOCATION: SiteLocation = 'Mill'

export const ISPS_PER_SITE = 3

/** Return the three ISP slot ids for a site (matches backend slot naming). */
export function ispSlotIdsForLocation(location: string): string[] {
  const key = location.trim().toLowerCase()
  if (key === 'mill') {
    return ['isp-1', 'isp-2', 'isp-3']
  }
  const slug = key.replace(/\s+/g, '-')
  return Array.from({ length: ISPS_PER_SITE }, (_, index) => `${slug}-isp-${index + 1}`)
}
