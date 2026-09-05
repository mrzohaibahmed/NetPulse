import type { ReportType } from '@/types'

export const REPORT_OPTIONS: Array<{
  value: ReportType
  label: string
  description: string
}> = [
  {
    value: 'executive',
    label: 'Executive Network Health',
    description: 'Fleet snapshot, period probe results, and data-quality notes.',
  },
  {
    value: 'availability',
    label: 'Device Availability & Outage',
    description: 'Current status, probe success ratio, and confirmed outage events.',
  },
  {
    value: 'performance',
    label: 'Network Performance',
    description: 'Successful ICMP scan RTT and valid interface utilization.',
  },
  {
    value: 'alerts',
    label: 'Alerts & Incidents',
    description: 'Device alerts and storm incidents as separate event families.',
  },
  {
    value: 'storm',
    label: 'Storm / Risk',
    description: 'Storm incidents, risk scores, mitigation, and recovery.',
  },
]

export const PERIOD_OPTIONS = [
  { value: '24h', label: 'Last 24 hours' },
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
  { value: 'custom', label: 'Custom range' },
] as const

export const DEVICE_STATUSES = ['all', 'Online', 'Not Reachable', 'Offline (Critical)', 'Unknown']

export const ALERT_TYPES = ['all', 'Device Offline', 'Device Recovered', 'Storm Protection', 'Collector Health']

export const ALERT_STATUSES = ['all', 'open', 'resolved', 'acknowledged']

export const INCIDENT_STATUSES = [
  'all',
  'OPEN',
  'MITIGATING',
  'MITIGATED',
  'RECOVERING',
  'RECOVERED',
  'RESOLVED',
  'CANCELLED',
]

export const SEVERITIES = ['all', 'CRITICAL', 'HIGH', 'WARNING', 'MEDIUM', 'LOW', 'INFO']

export const DEFAULT_LIMIT = 25
