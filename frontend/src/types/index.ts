export type DeviceStatus =
  | 'Online'
  | 'Not Reachable'
  | 'Offline (Critical)'
  | 'Offline'
  | 'Unknown'

export type ScanType = 'Manual' | 'Automatic'
export type UserRole = 'admin' | 'viewer'

export interface User {
  _id: string
  username: string
  role: UserRole
  createdAt?: string
}

export interface NetworkPort {
  port: number
  protocol: string
  state: string
  service: string
  product: string
  version: string
  extraInfo: string
}

export interface NetworkInfo {
  hostname: string
  macAddress: string
  vendor: string
  os: {
    name: string
    family: string
    generation: string
    accuracy: string
  }
  deviceType: string
  ports: NetworkPort[]
  services: string[]
  lastScan: string
}

export interface Device {
  _id: string
  hostname: string
  ipAddress: string
  deviceType: string
  critical: boolean
  monitor: boolean
  status: DeviceStatus
  lastSeen: string | null
  lastCheckedAt?: string | null
  responseTime: number | null
  consecutiveFailures?: number
  pingInterval?: number | null
  pingTimeoutMs?: number | null
  pingRetries?: number | null
  createdAt: string
  updatedAt: string
  networkInfo?: NetworkInfo | null
}

export interface DevicePayload {
  hostname: string
  ipAddress: string
  deviceType: string
  critical?: boolean
  monitor?: boolean
  pingInterval?: number | null
  pingTimeoutMs?: number | null
  pingRetries?: number | null
}

export interface PingHistory {
  _id: string
  deviceId: string
  hostname: string
  ipAddress: string
  status: DeviceStatus
  responseTime: number | null
  scanType: ScanType
  timestamp: string
}

export interface DashboardSummary {
  totalDevices: number
  onlineDevices: number
  notReachableDevices: number
  criticalOfflineDevices: number
  unknownDevices: number
  criticalDevices: number
  monitoredDevices: number
  onlinePercentage: number
  notReachablePercentage: number
  criticalOfflinePercentage: number
  offlineDevices?: number
}

export interface DashboardStatistics {
  totalScans: number
  averageResponseTime: number | null
  onlinePercentage: number
  notReachablePercentage: number
  criticalOfflinePercentage: number
  offlinePercentage: number
  unknownPercentage: number
  criticalOnline: number
  criticalOffline: number
}

export interface DeviceStatusRow {
  _id: string
  hostname: string
  ipAddress: string
  deviceType: string
  status: DeviceStatus
  responseTime: number | null
  lastSeen: string | null
  critical: boolean
  monitor: boolean
  consecutiveFailures?: number
}

export interface ChartSlice {
  name: string
  value: number
}

export interface ResponseTimeChartPoint {
  hostname: string
  responseTime: number
}

export interface ScanActivityChartPoint {
  date: string
  scans: number
}

export interface AlertItem {
  _id: string
  deviceId: string | null
  hostname: string
  ipAddress: string
  deviceType?: string
  status: string
  message: string
  scanType: string
  emailSent: boolean
  acknowledged: boolean
  dismissed: boolean
  acknowledgedAt: string | null
  dismissedAt: string | null
  createdAt: string
}

export interface AppSettings {
  pingInterval: number
  pingTimeoutMs: number
  pingRetries: number
  smtp: {
    enabled: boolean
    host: string
    port: number
    user: string
    passwordSet: boolean
    fromAddress: string
    toAddress: string
    useTls: boolean
  }
  updatedAt: string | null
}

export interface UptimeRow {
  deviceId: string
  hostname: string
  ipAddress: string
  deviceType: string
  status: DeviceStatus
  totalChecks: number
  onlineChecks: number
  downtimeChecks: number
  uptimePercentage: number | null
  downtimePercentage: number | null
}

export interface DeviceHistoryResponse {
  success: boolean
  device: Device
  uptime: {
    totalChecks: number
    onlineChecks: number
    downtimeChecks: number
    uptimePercentage: number | null
    downtimePercentage: number | null
  }
  history: PingHistory[]
  responseTimeTrend: Array<{
    timestamp: string
    responseTime: number
    status: string
  }>
  count: number
}

export interface DiscoveryDevice {
  hostname: string | null
  ipAddress: string
  status: 'Online' | 'Offline'
  responseTime: number | null
  saved: boolean
}

export interface DiscoverySummary {
  totalScanned: number
  online: number
  offline: number
  newlySaved?: number
}

export interface NetworkHint {
  localIP: string
  startIP: string
  endIP: string
  network: string
}

export interface DiscoveryResult {
  success: boolean
  summary: DiscoverySummary
  devices: DiscoveryDevice[]
}

export interface ApiListResponse<T> {
  success: boolean
  count: number
  data: T[]
  page?: number
  limit?: number
  total?: number
  totalPages?: number
}

export interface PaginationParams {
  page?: number
  limit?: number
  q?: string
  status?: string
  scanType?: string
  deviceType?: string
  deviceId?: string
  startDate?: string
  endDate?: string
  adminStatus?: string
  operStatus?: string
  mode?: string
  eligible?: boolean
  severity?: string
}

export interface InterfaceNeighbor {
  hostname: string
  ip?: string
  platform?: string
  deviceType?: string
  interface?: string
  /** @deprecated Prefer `interface` — retained for backward compatibility */
  port?: string
  protocol?: string
  managementAddress?: string
  systemDescription?: string
  capabilities?: string[]
}

export interface NetworkInterface {
  _id: string
  deviceId: string
  hostname?: string | null
  ipAddress?: string | null
  name: string
  description: string
  adminStatus: string
  operStatus: string
  mode: string
  portMode: string
  isAccess: boolean
  isTrunk: boolean
  isUplink: boolean
  isInfrastructure: boolean
  isManagement: boolean
  isProtected: boolean
  monitoringEnabled: boolean
  accessVlan: number | null
  voiceVlan?: number | null
  nativeVlan: number | null
  allowedVlans: number[]
  vlan: string | number
  speed: string
  speedMbps?: number | null
  duplex: string
  neighbor?: InterfaceNeighbor | null
  ifIndex?: number | null
  macAddress: string
  vendor: string
  collectionMethod: string
  lastUpdated: string | null
  createdAt?: string | null
  updatedAt?: string | null
}

export interface InterfaceStat {
  _id: string
  deviceId: string
  hostname?: string | null
  ipAddress?: string | null
  interfaceName: string
  ifIndex?: number | null
  rxBytes: number
  txBytes: number
  rxPackets: number
  txPackets: number
  broadcastPackets: number
  multicastPackets: number
  inputErrors: number
  outputErrors: number
  discards: number
  utilization?: number | null
  rxUtilization?: number | null
  txUtilization?: number | null
  speedBps?: number | null
  collectionMethod: string
  timestamp: string | null
}

export interface InterfaceListRow extends NetworkInterface {
  utilization?: number | null
  rxUtilization?: number | null
  txUtilization?: number | null
  broadcastPackets?: number | null
  multicastPackets?: number | null
  inputErrors?: number | null
  outputErrors?: number | null
  discards?: number | null
  statsTimestamp?: string | null
  speedBps?: number | null
}

export interface EligibilityChecks {
  monitoring?: boolean | null
  admin?: boolean | null
  oper?: boolean | null
  access?: boolean | null
  trunk?: boolean | null
  uplink?: boolean | null
  infrastructure?: boolean | null
  management?: boolean | null
  protected?: boolean | null
}

export interface EligibilityResult {
  _id?: string | null
  deviceId: string
  interface: string
  hostname?: string | null
  ipAddress?: string | null
  eligible: boolean
  reason: string
  failedRule?: string | null
  confidence: number
  checks?: EligibilityChecks
  timestamp?: string | null
  adminStatus?: string
  operStatus?: string
  portMode?: string
  isAccess?: boolean
  isTrunk?: boolean
  isUplink?: boolean
  isInfrastructure?: boolean
  isManagement?: boolean
  isProtected?: boolean
  monitoringEnabled?: boolean
}

export interface StormConfig {
  enableEligibility: boolean
  allowManagementPorts: boolean
  allowTrunks: boolean
  allowInfrastructurePorts: boolean
  allowProtectedPorts: boolean
  confidence?: number
  risk?: {
    enableRisk?: boolean
    weights?: Record<string, number>
    thresholds?: Record<string, { low: number; medium: number; high: number; critical: number }>
  }
}

export type RiskSeverity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'

export interface RiskContributor {
  metric: string
  value: number | null
  score: number
  weight: number
}

export interface RiskResult {
  _id?: string | null
  deviceId: string
  interface: string
  hostname?: string | null
  ipAddress?: string | null
  riskScore: number
  severity: RiskSeverity | string
  confidence: number
  contributors: RiskContributor[]
  rawMetrics?: Record<string, { value?: number | null; score?: number; supported?: boolean; weight?: number }>
  eligible: boolean
  skippedReason?: string | null
  timestamp?: string | null
  broadcastRate?: number | null
  multicastRate?: number | null
  unknownUnicastRate?: number | null
  utilization?: number | null
  errorRate?: number | null
  discardRate?: number | null
  crcRate?: number | null
}

export interface ApiItemResponse<T> {
  success: boolean
  message?: string
  data: T
}

export interface ApiError {
  success: false
  message: string
  error?: string
}
