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
  firewall: Shield,
  server: Server,
  'access-point': Wifi,
  'access point': Wifi,
  workstation: Monitor,
  printer: Printer,
  other: HardDrive,
  unknown: Activity,
}

export function deviceTypeIcon(type: string | null | undefined): LucideIcon {
  const key = (type || 'unknown').trim().toLowerCase().replace(/[\s_]+/g, '-')
  return TYPE_ICONS[key] || Activity
}
