import { formatPercent } from '@/utils/format'
import type { PaginationParams, ReportType } from '@/types'

export function formatRatio(value: number | null | undefined): string {
  return formatPercent(value)
}

export function formatBucket(value: string | null | undefined): string {
  if (!value) return '—'
  if (/^\d{4}-\d{2}-\d{2} \d{2}:00$/.test(value)) {
    return value.slice(5, 13)
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return value.slice(5)
  }
  return value
}

export function reportQueryParams(input: {
  reportType: ReportType
  period: string
  startDate: string
  endDate: string
  deviceId: string
  deviceType: string
  status: string
  iface: string
  severity: string
  alertType: string
  alertStatus: string
  incidentStatus: string
  page: number
  limit: number
}): PaginationParams {
  const custom = input.period === 'custom'
  const params: PaginationParams = {
    period: input.period,
    startDate: custom && input.startDate ? input.startDate : undefined,
    endDate: custom && input.endDate ? input.endDate : undefined,
    deviceId: input.deviceId && input.deviceId !== 'all' ? input.deviceId : undefined,
    deviceType: input.deviceType,
    page: input.page,
    limit: input.limit,
  }

  if (input.reportType === 'executive' || input.reportType === 'availability' || input.reportType === 'performance') {
    params.status = input.status
  }
  if (input.reportType === 'performance') {
    params.interface = input.iface
  }
  if (input.reportType === 'alerts') {
    params.severity = input.severity
    params.alertType = input.alertType
    params.alertStatus = input.alertStatus
  }
  if (input.reportType === 'storm') {
    params.severity = input.severity
    params.incidentStatus = input.incidentStatus
  }
  return params
}
