export type DeviceStatus =
  | 'Online'
  | 'Not Reachable'
  | 'Offline (Critical)'
  | 'Offline'
  | 'Unknown'

export type ScanType = 'Manual' | 'Automatic'
export type UserRole = 'admin' | 'viewer' | 'operator' | 'super-admin'

export interface User {
  _id: string
  username: string
  role: UserRole
  mustChangePassword?: boolean
  createdAt?: string
  updatedAt?: string
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
  /** Auto-detected hardware / OUI vendor (optional; older docs may omit). */
  vendor?: string | null
  /** Auto-detected OS name from Nmap (optional). */
  operatingSystem?: string | null
  /** 0–100 classification confidence (optional). */
  classificationConfidence?: number | null
  classificationMethod?: string | null
  discoverySource?: string | null
  networkInfo?: NetworkInfo | null
  /**
   * SSH/SNMP credentials metadata.
   * Secrets are never returned (passwords are represented via configured flags).
   */
  credentials?: DeviceCredentials | null
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
  /**
   * Per-device credentials update/creation payload.
   * Backend expects this nested under `credentials` and normalizes partial updates.
   */
  credentials?: DeviceCredentialsPayload
}

export interface DeviceCredentials {
  sshUsername: string
  sshPort: number
  sshVendor: string
  sshPasswordConfigured: boolean
  sshSecretConfigured: boolean
  snmpCommunityConfigured: boolean
  snmpPort: number
  snmpVersion: string
}

export interface DeviceCredentialsPayload {
  sshUsername?: string
  sshPassword?: string
  sshVendor?: string
  sshSecret?: string
  sshPort?: number
  snmpCommunity?: string
  snmpVersion?: string
  snmpPort?: number
  snmpTimeout?: number
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
  deviceName?: string | null
  status: string
  message: string
  title?: string | null
  scanType: string
  alertType?: string | null
  category?: string | null
  severity?: string | null
  interface?: string | null
  incidentId?: string | null
  riskScore?: number | null
  action?: string | null
  generatedBy?: string | null
  recoveryDuration?: string | null
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
  mitigationMode?: string
  autoRecovery?: boolean
  cooldownMinutes?: number
  stabilizationSeconds?: number
  maximumRecoveryAttempts?: number
  reMitigationThreshold?: number
  dataRetentionDays?: number
  incidentRetentionDays?: number
  stormNotifications?: {
    enabled: boolean
    shutdownEmails: boolean
    recoveryEmails: boolean
    failureEmails: boolean
    toAddress: string
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
  deviceType?: string | null
  vendor?: string | null
  operatingSystem?: string | null
  classificationConfidence?: number | null
  classificationMethod?: string | null
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
  state?: string
  safetyStatus?: string
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
  /** Preference mirror: true when monitoringMode is AUTO. */
  monitoringEnabled: boolean
  /** Administrator intent: AUTO | DISABLED_BY_USER */
  monitoringMode?: 'AUTO' | 'DISABLED_BY_USER' | string
  administratorDisabled?: boolean
  /** Preference + admin/oper up. */
  effectiveMonitoring?: boolean
  monitoringReason?:
    | 'active'
    | 'administrator_disabled'
    | 'administrative_down'
    | 'operational_down'
    | string
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
  rxBroadcastPackets?: number | null
  txBroadcastPackets?: number | null
  rxMulticastPackets?: number | null
  txMulticastPackets?: number | null
  inputErrors: number
  outputErrors: number
  discards: number
  rxDiscards?: number | null
  txDiscards?: number | null
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
  rxBroadcastPackets?: number | null
  txBroadcastPackets?: number | null
  rxMulticastPackets?: number | null
  txMulticastPackets?: number | null
  inputErrors?: number | null
  outputErrors?: number | null
  discards?: number | null
  rxDiscards?: number | null
  txDiscards?: number | null
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
  monitoringMode?: 'AUTO' | 'DISABLED_BY_USER' | string
  administratorDisabled?: boolean
  effectiveMonitoring?: boolean
  monitoringReason?: string
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
  confirmation?: {
    confirmationEnabled?: boolean
    requiredConfirmations?: number
    riskThreshold?: number
    resetOnPollFailure?: boolean
    resetOnIneligible?: boolean
    resetOnLowRisk?: boolean
    pollStaleSeconds?: number
  }
  safety?: {
    safetyEnabled?: boolean
    automationEnabled?: boolean
    cooldownMinutes?: number
    cpuThreshold?: number
    memoryThreshold?: number
    maximumAttempts?: number
    allowManualOverride?: boolean
    riskThreshold?: number
    requireSsh?: boolean
  }
}

export type RiskSeverity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'

export type ConfirmationState = 'NOT_CONFIRMED' | 'PENDING' | 'CONFIRMED'

export interface RiskContributor {
  metric: string
  value: number | null
  score: number
  weight: number
}

export type StormSourceClassification =
  | 'LIKELY_SOURCE'
  | 'POSSIBLE_SOURCE'
  | 'LIKELY_FORWARDER'
  | 'LIKELY_RECEIVER'
  | 'NORMAL'
  | 'UNKNOWN'

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
  sourceClassification?: StormSourceClassification | string | null
  sourceConfidence?: number | null
  sourceRationale?: string | null
}

export interface ConfirmationResult {
  _id?: string | null
  deviceId: string
  interface: string
  hostname?: string | null
  ipAddress?: string | null
  confirmed: boolean
  state: ConfirmationState | string
  currentRisk: number
  highestRisk: number
  averageRisk: number
  consecutiveHighSamples: number
  requiredSamples: number
  progress?: number
  reason: string
  reset?: boolean
  resetReason?: string | null
  timestamp?: string | null
}

export type SafetyStatus = 'SAFE' | 'UNSAFE' | 'WAITING'

export interface SafetyResult {
  _id?: string | null
  deviceId: string
  interface: string
  hostname?: string | null
  ipAddress?: string | null
  safe: boolean
  reason: string
  failedRule?: string | null
  confidence: number
  checks: Record<string, boolean | null | undefined>
  cooldownRemainingSeconds?: number
  mitigationAttempts?: number
  cpuPercent?: number | null
  memoryPercent?: number | null
  status: SafetyStatus | string
  sourceClassification?: StormSourceClassification | string | null
  sourceConfidence?: number | null
  sourceRationale?: string | null
  timestamp?: string | null
}

export interface StormIncidentTrigger {
  type?: string
  risk?: number | null
  confirmation?: boolean
  safety?: boolean
}

export interface StormIncidentTimelineEvent {
  event: string
  time?: string | null
  detail?: string | null
}

export interface StormIncident {
  _id?: string | null
  incidentId: string
  deviceId: string
  interface: string
  hostname?: string | null
  ipAddress?: string | null
  incidentType?: string
  type?: string
  requestedBy?: string | null
  requestedAt?: string | null
  reason?: string | null
  action?: string | null
  requiresApproval?: boolean
  approvedBy?: string | null
  executedImmediately?: boolean
  status: string
  severity: string
  trigger: StormIncidentTrigger
  interfaceSnapshot?: Record<string, unknown>
  switchportSnapshot?: Record<string, unknown>
  macTable?: Record<string, unknown>
  statistics?: Record<string, unknown>
  neighbor?: Record<string, unknown> | null
  deviceHealth?: Record<string, unknown>
  eligibility?: Record<string, unknown> | null
  risk?: Record<string, unknown> | null
  confirmation?: Record<string, unknown> | null
  safety?: Record<string, unknown> | null
  diagnosticsMeta?: Record<string, unknown>
  sourceAttribution?: Record<string, unknown> | null
  sourceClassification?: StormSourceClassification | string | null
  sourceConfidence?: number | null
  affectedInterfaces?: string[]
  relatedInterfaces?: string[]
  timeline: StormIncidentTimelineEvent[]
  createdAt?: string | null
  updatedAt?: string | null
}

export interface PrepareResult {
  ready: boolean
  status: string
  incidentId?: string | null
  deviceId: string
  interface: string
  reason?: string
  context?: Record<string, unknown>
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

export interface MitigationLog {
  _id?: string | null
  incidentId: string
  deviceId: string
  interface: string
  strategy: string
  status: string
  commandsExecuted: string[]
  verificationResult: {
    success?: boolean
    output?: string
    error?: string
    note?: string
  }
  rollbackPerformed: boolean
  operator: string
  timestamp: string
  emergency?: boolean
  executionMode?: string | null
  vendor?: string | null
  executionTimeMs?: number | null
  sourceIp?: string | null
  sessionId?: string | null
  reason?: string | null
}

export interface RecoveryLog {
  _id?: string | null
  incidentId: string
  deviceId: string
  interface: string
  recoveryStatus: string
  verificationResult: {
    success?: boolean
    error?: string | null
    note?: string
    output?: string
    failedRule?: string | null
    recoveryRule?: string | null
    previousStatus?: string
    newStatus?: string
    reason?: string
    engine?: string
    reconciled?: boolean
    detectedBy?: string
    checks?: Record<string, boolean | null>
    stats?: {
      adminStatus?: string
      operStatus?: string
      broadcastRate?: number
      multicastRate?: number
      utilization?: number
      inputErrors?: number
      outputErrors?: number
      crc?: number
      discards?: number
    }
  }
  retryCount: number
  timestamp: string
  /** Recovery Safety Engine — present on BLOCKED logs */
  failedRule?: string | null
  recoveryRule?: string | null
  previousStatus?: string
  newStatus?: string
  reason?: string
  reconciled?: boolean
  detectedBy?: string
  note?: string
  checks?: Record<string, boolean | null>
  engine?: string
  /** Manual Recovery override audit fields */
  recoveryType?: string | null
  trigger?: string | null
  safetyRules?: string | null
  executionChecks?: string | null
  executedBy?: string | null
  recoveryMethod?: string | null
}

