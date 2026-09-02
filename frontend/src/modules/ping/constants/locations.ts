/** Initial supported site locations for device and ISP grouping. */
export const SITE_LOCATIONS = ['Mills', 'Karachi', 'Lahore'] as const

export type SiteLocation = (typeof SITE_LOCATIONS)[number]

export const DEFAULT_SITE_LOCATION: SiteLocation = 'Mills'

export const ISPS_PER_SITE = 3

/** Normalize site names, including legacy Mill -> Mills. */
export function canonicalSiteLocation(value: string | null | undefined): string | null {
  const cleaned = (value ?? '').trim()
  if (!cleaned) return null
  if (cleaned.toLowerCase() === 'mill') return 'Mills'
  return cleaned
}

/** Return the three ISP slot ids for a site (matches backend slot naming). */
export function ispSlotIdsForLocation(location: string): string[] {
  const key = (canonicalSiteLocation(location) || DEFAULT_SITE_LOCATION).trim().toLowerCase()
  if (key === 'mill' || key === 'mills') {
    return ['isp-1', 'isp-2', 'isp-3']
  }
  const slug = key.replace(/\s+/g, '-')
  return Array.from({ length: ISPS_PER_SITE }, (_, index) => `${slug}-isp-${index + 1}`)
}
