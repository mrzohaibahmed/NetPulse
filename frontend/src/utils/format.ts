function parseTimestamp(value: string): Date {
  // API timestamps without a timezone are UTC; treat them as such for JS parsing.
  if (/[zZ]$|[+-]\d{2}:\d{2}$/.test(value)) {
    return new Date(value)
  }
  return new Date(`${value}Z`)
}

export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${value.toFixed(1)} ms`
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = parseTimestamp(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return formatUtilization(value)
}

export function formatRelative(value: string | null | undefined): string {
  if (!value) return 'Never'
  const date = parseTimestamp(value)
  if (Number.isNaN(date.getTime())) return '—'
  const diff = Date.now() - date.getTime()
  if (diff < 0) return 'just now'
  const seconds = Math.floor(diff / 1000)
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  const n = Number(value)
  if (n < 1000) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB', 'PB']
  let size = n
  let unit = -1
  do {
    size /= 1000
    unit += 1
  } while (size >= 1000 && unit < units.length - 1)
  return `${size.toFixed(size >= 100 ? 0 : size >= 10 ? 1 : 2)} ${units[unit]}`
}

export function formatPackets(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  const n = Number(value)
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}K`
  if (n < 1_000_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  return `${(n / 1_000_000_000).toFixed(2)}B`
}

export function formatUtilization(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  const n = Number(value)
  if (n === 0) return '0%'
  // Sub-0.1% traffic on 1G/10G links is real — do not round to "0.0%"
  if (n > 0 && n < 0.1) return `${n.toFixed(3)}%`
  if (n < 10) return `${n.toFixed(2)}%`
  return `${n.toFixed(1)}%`
}

export function formatSpeedBps(value: number | null | undefined): string {
  if (value === null || value === undefined || value <= 0) return '—'
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(value % 1_000_000_000 === 0 ? 0 : 1)} Gbps`
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value % 1_000_000 === 0 ? 0 : 1)} Mbps`
  if (value >= 1000) return `${(value / 1000).toFixed(0)} Kbps`
  return `${value} bps`
}
