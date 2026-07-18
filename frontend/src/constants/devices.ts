/** Canonical device types used in forms, filters, and reports. */
export const DEVICE_TYPES = [
  'Router',
  'Switch',
  'Firewall',
  'Server',
  'Access Point',
  'Workstation',
  'Printer',
  'Other',
] as const

export type DeviceTypeOption = (typeof DEVICE_TYPES)[number]

export const DEFAULT_DEVICE_TYPE: DeviceTypeOption = 'Server'
