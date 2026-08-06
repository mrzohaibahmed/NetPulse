import type { AlertItem, DeviceStatus } from '@/types'

export const STATUS_COLORS: Record<string, string> = {
  Online: '#22C55E',
  'Not Reachable': '#F59E0B',
  'Offline (Critical)': '#EF4444',
  Offline: '#EF4444',
  Unknown: '#94A3B8',
}

export const TYPE_COLORS = ['#3B82F6', '#22C55E', '#F59E0B', '#8B5CF6', '#06B6D4', '#F97316', '#EC4899', '#64748B']

export function statusTone(status: string | null | undefined): 'online' | 'offline' | 'warn' | 'unknown' {
  if (status === 'Online') return 'online'
  if (status === 'Not Reachable') return 'warn'
  if (status === 'Offline (Critical)' || status === 'Offline') return 'offline'
  return 'unknown'
}

export function isOnlineStatus(status: DeviceStatus | string): boolean {
  return status === 'Online'
}

/** Distinguishes storm-protection alerts from device (reachability) alerts. */
export function isStormAlert(alert: AlertItem): boolean {
  const category = (alert.category || alert.alertType || alert.scanType || '').toLowerCase()
  return category.includes('storm')
}
