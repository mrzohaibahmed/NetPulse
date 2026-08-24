import {
  Activity,
  HardDrive,
  Monitor,
  Network,
  Printer,
  Router,
  Server,
  Shield,
  Wifi,
  type LucideIcon,
} from 'lucide-react'

const TYPE_ICONS: Record<string, LucideIcon> = {
  router: Router,
  switch: Network,
  'managed-switch': Network,
  firewall: Shield,
  server: Server,
  'linux-server': Server,
  'windows-pc': Monitor,
  'access-point': Wifi,
  'access point': Wifi,
  workstation: Monitor,
  printer: Printer,
  hypervisor: HardDrive,
  nas: HardDrive,
  'ip-camera': Activity,
  camera: Activity,
  laptop: Monitor,
  pc: Monitor,
  phone: Wifi,
  'ip-phone': Wifi,
  iot: HardDrive,
  'network-device': Network,
  'unknown-device': Activity,
  other: HardDrive,
  unknown: Activity,
}

export function deviceTypeIcon(type: string | null | undefined): LucideIcon {
  const key = (type || 'unknown').trim().toLowerCase().replace(/[\s_]+/g, '-')
  return TYPE_ICONS[key] || Activity
}
