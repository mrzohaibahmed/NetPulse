/** Canonical device types used in forms, filters, and reports. */
export const DEVICE_TYPES = [
  'Router',
  'WiFi Router',
  'Switch',
  'Managed Switch',
  'Firewall',
  'Server',
  'Linux Server',
  'ESXi Server',
  'Windows PC',
  'Workstation',
  'Access Point',
  'Printer',
  'Hypervisor',
  'NAS',
  'IP Camera',
  'WiFi Camera',
  'IP Phone',
  'NVR',
  'Unknown Device',
  'Other',
] as const

export type DeviceTypeOption = (typeof DEVICE_TYPES)[number]

export const DEFAULT_DEVICE_TYPE: DeviceTypeOption = 'Server'

/** Display helper: low-confidence auto classifications show as Unknown Device. */
export function displayDeviceType(
  deviceType: string | null | undefined,
  _confidence?: number | null,
): string {
  return (deviceType || 'Unknown Device').trim() || 'Unknown Device'
}

export const SERVER_HARDWARE_DEVICE_TYPES = ['Server', 'Linux Server', 'ESXi Server'] as const

export type ServerHardwareDeviceType = (typeof SERVER_HARDWARE_DEVICE_TYPES)[number]

export function isServerHardwareDevice(deviceType: string | null | undefined): boolean {
  if (!deviceType) return false
  return SERVER_HARDWARE_DEVICE_TYPES.includes(deviceType.trim() as ServerHardwareDeviceType)
}

