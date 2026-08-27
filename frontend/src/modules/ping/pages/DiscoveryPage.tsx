import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  CheckCircle2,
  Loader2,
  Network as NetIcon,
  Radar,
  Sparkles,
  Table2,
  Upload,
  Plus,
  Trash2,
  Edit2,
  Play,
  Settings,
  Wifi,
  Globe,
} from 'lucide-react'
import { useAuth } from '@/shared/auth/AuthContext'
import { useClientPagination } from '@/hooks/useClientPagination'
import {
  useNetworksQuery,
  useNetworkMutation,
  useScanNetworksMutation,
} from '@/hooks/queries'
import { queryKeys } from '@/hooks/queryKeys'
import { useQueryClient } from '@tanstack/react-query'
import { getDevices, getDiscoveryEnrichmentStatus, getDiscoveryScanProgress } from '@/api'
import { ApiRequestError } from '@/shared/api/client'
import { formatMs } from '@/utils/format'
import type { Device, DiscoveryDevice, DiscoverySummary, NetworkProfile } from '@/types'
import { displayDeviceType } from '@/modules/ping/constants/devices'
import { EmptyState } from '@/shared/components/EmptyState'
import { KpiCard } from '@/shared/components/KpiCard'
import { PageHeader } from '@/shared/components/PageHeader'
import { PaginationControls } from '@/shared/components/PaginationControls'
import { SectionHeading } from '@/shared/components/SectionHeading'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Checkbox } from '@/shared/ui/checkbox'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'
import { Progress } from '@/shared/ui/progress'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/shared/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/ui/select'
import { toast } from 'sonner'

// Helper utilities for IP and Subnet matching
function formatElapsed(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remainder = seconds % 60
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
  }
  return `${minutes}:${String(remainder).padStart(2, '0')}`
}

function ipToLong(ip: string): number {
  return ip.split('.').reduce((ipInt, octet) => (ipInt << 8) + parseInt(octet, 10), 0) >>> 0
}

function isIpInCidr(ip: string, cidr: string): boolean {
  try {
    const trimmedCidr = cidr.trim()
    if (!trimmedCidr.includes('/')) {
      if (trimmedCidr.includes('-')) {
        const [startStr, endStr] = trimmedCidr.split('-')
        const ipLong = ipToLong(ip)
        const startLong = ipToLong(startStr.trim())
        let endLong: number
        if (endStr.includes('.')) {
          endLong = ipToLong(endStr.trim())
        } else {
          const octets = startStr.split('.')
          octets[3] = endStr.trim()
          endLong = ipToLong(octets.join('.'))
        }
        return ipLong >= startLong && ipLong <= endLong
      }
      return ip === trimmedCidr
    }
    const [range, bitsStr] = trimmedCidr.split('/')
    const bits = parseInt(bitsStr, 10)
    const ipLong = ipToLong(ip)
    const rangeLong = ipToLong(range)
    const mask = bits === 0 ? 0 : ~((1 << (32 - bits)) - 1)
    return (ipLong & mask) === (rangeLong & mask)
  } catch {
    return false
  }
}

function mapInventoryToDiscoveryDevices(
  inventory: Device[],
  enrichmentByIp: Map<
    string,
    Awaited<ReturnType<typeof getDiscoveryEnrichmentStatus>>['devices'][number]
  >,
): DiscoveryDevice[] {
  return inventory.map((device) => {
    const enrichment = enrichmentByIp.get(device.ipAddress)
    return {
      hostname: device.hostname ?? null,
      ipAddress: device.ipAddress,
      status: device.status === 'Online' ? 'Online' : 'Offline',
      responseTime: device.responseTime,
      saved: true,
      deviceType: device.deviceType,
      vendor: device.vendor,
      operatingSystem: device.operatingSystem,
      classificationConfidence: device.classificationConfidence,
      classificationMethod: device.classificationMethod,
      discoveryStatus: enrichment?.discoveryStatus ?? null,
      nmapError: enrichment?.discoveryEnrichmentError ?? null,
    }
  })
}

export function DiscoveryPage() {
  const { isAdmin } = useAuth()
  const queryClient = useQueryClient()

  // Queries & Mutations
  const networksQuery = useNetworksQuery()
  const networkMutation = useNetworkMutation()
  const scanMutation = useScanNetworksMutation()

  // Selection states
  const [selectedIds, setSelectedIds] = useState<string[]>([])

  // Dialog state
  const [isDialogOpen, setIsDialogOpen] = useState(false)
  const [editingNetwork, setEditingNetwork] = useState<NetworkProfile | null>(null)

  // Dialog Form fields
  const [name, setName] = useState('')
  const [type, setType] = useState<'ETHERNET' | 'WIFI'>('ETHERNET')
  const [enabled, setEnabled] = useState(true)
  const [cidr, setCidr] = useState('')
  const [scanTargets, setScanTargets] = useState('')
  const [gateway, setGateway] = useState('')
  const [description, setDescription] = useState('')
  const [sshUsername, setSshUsername] = useState('')
  const [sshPassword, setSshPassword] = useState('')
  const [snmpCommunity, setSnmpCommunity] = useState('public')

  // Scan states
  const [summary, setSummary] = useState<DiscoverySummary | null>(null)
  const [devices, setDevices] = useState<DiscoveryDevice[]>([])
  const [showOnlineOnly, setShowOnlineOnly] = useState(false)
  const [scanId, setScanId] = useState<string | null>(null)
  const [scanStartedAt, setScanStartedAt] = useState<number | null>(null)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [scanProgress, setScanProgress] = useState<{
    percent: number
    completed: number
    total: number
    online: number
    newlySaved: number
    status: string
    error?: string | null
  } | null>(null)
  const scanCompletionHandledRef = useRef<string | null>(null)

  // Filter Discovered Devices by Network
  const [filterNetworkId, setFilterNetworkId] = useState<string>('all')

  // Load form fields when editing
  useEffect(() => {
    if (editingNetwork) {
      setName(editingNetwork.name)
      setType(editingNetwork.type)
      setEnabled(editingNetwork.enabled)
      setCidr(editingNetwork.cidr)
      setScanTargets(editingNetwork.scanTargets)
      setGateway(editingNetwork.gateway ?? '')
      setDescription(editingNetwork.description ?? '')
      setSshUsername(editingNetwork.sshUsername ?? '')
      setSshPassword('') // Don't show password
      // Community is never returned by API — leave blank; placeholder indicates configured.
      setSnmpCommunity('')
    } else {
      setName('')
      setType('ETHERNET')
      setEnabled(true)
      setCidr('')
      setScanTargets('')
      setGateway('')
      setDescription('')
      setSshUsername('')
      setSshPassword('')
      setSnmpCommunity('public')
    }
  }, [editingNetwork, isDialogOpen])

  const networks = networksQuery.data ?? []

  // Dynamic Network Match Helper
  const getDeviceNetworkName = (ip: string) => {
    const matched = networks.find((net) => isIpInCidr(ip, net.cidr))
    return matched ? matched.name : 'Unknown'
  }

  // Pagination for Discovered Devices
  const visible = useMemo(() => {
    let items = devices
    if (showOnlineOnly) {
      items = items.filter((d) => d.status === 'Online')
    }
    if (filterNetworkId !== 'all') {
      const selectedNet = networks.find((n) => n.id === filterNetworkId)
      if (selectedNet) {
        items = items.filter((d) => isIpInCidr(d.ipAddress, selectedNet.cidr))
      }
    }
    return items
  }, [devices, showOnlineOnly, filterNetworkId, networks])

  const networkFilteredDevices = useMemo(() => {
    if (filterNetworkId === 'all') {
      return devices
    }
    const selectedNet = networks.find((n) => n.id === filterNetworkId)
    if (selectedNet) {
      return devices.filter((d) => isIpInCidr(d.ipAddress, selectedNet.cidr))
    }
    return devices
  }, [devices, filterNetworkId, networks])

  const scannedCount = networkFilteredDevices.length
  const onlineCount = networkFilteredDevices.filter((d) => d.status === 'Online').length
  const offlineCount = networkFilteredDevices.filter((d) => d.status !== 'Online').length
  const newSavedCount = networkFilteredDevices.filter((d) => d.saved).length
  const enrichingCount = networkFilteredDevices.filter(
    (d) => d.discoveryStatus === 'pending' || d.discoveryStatus === 'enriching',
  ).length
  const readyCount = networkFilteredDevices.filter(
    (d) => d.discoveryStatus === 'completed' || d.discoveryStatus == null,
  ).length

  const mergeEnrichmentUpdates = (
    current: DiscoveryDevice[],
    updates: Awaited<ReturnType<typeof getDiscoveryEnrichmentStatus>>['devices'],
  ) => {
    const byIp = new Map(updates.map((row) => [row.ipAddress, row]))
    return current.map((device) => {
      const update = byIp.get(device.ipAddress)
      if (!update) return device
      return {
        ...device,
        hostname: update.hostname ?? device.hostname,
        deviceType: update.deviceType ?? device.deviceType,
        vendor: update.vendor ?? device.vendor,
        operatingSystem: update.operatingSystem ?? device.operatingSystem,
        classificationConfidence:
          update.classificationConfidence ?? device.classificationConfidence,
        classificationMethod: update.classificationMethod ?? device.classificationMethod,
        discoveryStatus: update.discoveryStatus ?? device.discoveryStatus,
        nmapError: update.discoveryEnrichmentError ?? device.nmapError,
      }
    })
  }

  const pendingEnrichmentKey = useMemo(
    () =>
      devices
        .filter((d) => d.discoveryStatus === 'pending' || d.discoveryStatus === 'enriching')
        .map((d) => d.ipAddress)
        .sort()
        .join(','),
    [devices],
  )

  useEffect(() => {
    if (!pendingEnrichmentKey) {
      return
    }

    const ipAddresses = pendingEnrichmentKey.split(',')
    let attempts = 0
    let cancelled = false

    const poll = async () => {
      if (cancelled) return
      attempts += 1
      try {
        const response = await getDiscoveryEnrichmentStatus(ipAddresses)
        if (cancelled) return
        setDevices((current) => mergeEnrichmentUpdates(current, response.devices))
        const stillPending = response.devices.some(
          (d) => d.discoveryStatus === 'pending' || d.discoveryStatus === 'enriching',
        )
        if (!stillPending) {
          cancelled = true
          window.clearInterval(timer)
          const completed = response.devices.filter((d) => d.discoveryStatus === 'completed').length
          const failed = response.devices.filter((d) => d.discoveryStatus === 'failed').length
          if (failed > 0 && completed === 0) {
            toast.error(
              failed === 1
                ? 'Nmap enrichment failed for 1 device.'
                : `Nmap enrichment failed for ${failed} devices.`,
            )
          } else if (failed > 0) {
            toast.warning(
              `Nmap enrichment finished: ${completed} succeeded, ${failed} failed.`,
            )
          } else {
            toast.success(
              completed === 1
                ? 'Nmap enrichment complete for 1 device.'
                : `Nmap enrichment complete for ${completed || ipAddresses.length} devices.`,
            )
          }
        } else if (attempts >= 18) {
          cancelled = true
          window.clearInterval(timer)
        }
      } catch {
        if (attempts >= 18) {
          cancelled = true
          window.clearInterval(timer)
        }
      }
    }

    const timer = window.setInterval(() => {
      void poll()
    }, 10_000)
    void poll()

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [pendingEnrichmentKey])

  const pagination = useClientPagination(visible, 25)
  const { reset } = pagination

  useEffect(() => {
    reset()
  }, [showOnlineOnly, filterNetworkId, devices, reset])

  const scanning = Boolean(scanId)

  useEffect(() => {
    if (!scanning || scanStartedAt == null) {
      return
    }
    setElapsedSeconds(Math.floor((Date.now() - scanStartedAt) / 1000))
    const timer = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - scanStartedAt) / 1000))
    }, 250)
    return () => window.clearInterval(timer)
  }, [scanning, scanStartedAt])

  useEffect(() => {
    if (!scanId) {
      return
    }

    let cancelled = false
    let timer: number | undefined

    const finishScan = async (
      progress: Awaited<ReturnType<typeof getDiscoveryScanProgress>>['progress'],
    ) => {
      if (scanCompletionHandledRef.current === progress.scanId) {
        return
      }
      scanCompletionHandledRef.current = progress.scanId

      if (progress.status === 'failed') {
        toast.error(progress.error || 'Network scan failed')
        setScanId(null)
        setScanStartedAt(null)
        return
      }

      const baseSummary = progress.summary ?? {
        totalScanned: progress.total,
        online: progress.online,
        offline: Math.max(0, progress.total - progress.online),
        newlySaved: progress.newlySaved,
      }

      try {
        const inventory = await getDevices({ limit: 500 })
        const inventoryRows = inventory.data ?? []
        const ips = inventoryRows.map((row) => row.ipAddress).filter(Boolean)
        const enrichmentByIp = new Map<
          string,
          Awaited<ReturnType<typeof getDiscoveryEnrichmentStatus>>['devices'][number]
        >()
        if (ips.length > 0) {
          try {
            const enrichment = await getDiscoveryEnrichmentStatus(ips)
            for (const row of enrichment.devices) {
              enrichmentByIp.set(row.ipAddress, row)
            }
          } catch {
            // Non-fatal — enrichment polling can still catch up.
          }
        }
        if (cancelled) return

        const mapped = mapInventoryToDiscoveryDevices(inventoryRows, enrichmentByIp)
        const enriching = mapped.filter(
          (d) => d.discoveryStatus === 'pending' || d.discoveryStatus === 'enriching',
        ).length
        setDevices(mapped)
        setSummary({ ...baseSummary, enriching })
        setScanProgress({
          percent: 100,
          completed: baseSummary.totalScanned,
          total: baseSummary.totalScanned,
          online: baseSummary.online,
          newlySaved: baseSummary.newlySaved ?? 0,
          status: 'complete',
          error: null,
        })

        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['devices'] }),
          queryClient.invalidateQueries({ queryKey: ['networks'] }),
          queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.all }),
        ])

        if (baseSummary.online === 0) {
          toast.message('No online hosts found in the scanned targets.')
        } else if ((baseSummary.newlySaved ?? 0) === 0) {
          toast.success('Scan complete. All online hosts were already monitored.')
        } else if (enriching > 0) {
          toast.success(
            `Saved ${baseSummary.newlySaved} new device(s). ${enriching} still enriching in background.`,
          )
        } else {
          toast.success(`Scan complete. Saved ${baseSummary.newlySaved} new device(s).`)
        }
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to refresh devices after scan')
      } finally {
        if (!cancelled) {
          setScanId(null)
          setScanStartedAt(null)
        }
      }
    }

    const poll = async () => {
      try {
        const response = await getDiscoveryScanProgress(scanId)
        if (cancelled) return
        const progress = response.progress
        setScanProgress({
          percent: progress.percent,
          completed: progress.completed,
          total: progress.total,
          online: progress.online,
          newlySaved: progress.newlySaved,
          status: progress.status,
          error: progress.error,
        })
        if (progress.status === 'complete' || progress.status === 'failed') {
          if (timer !== undefined) {
            window.clearInterval(timer)
            timer = undefined
          }
          await finishScan(progress)
        }
      } catch {
        // Transient poll errors are non-fatal; keep polling while scanId is set.
      }
    }

    void poll()
    timer = window.setInterval(() => {
      void poll()
    }, 1500)

    return () => {
      cancelled = true
      if (timer !== undefined) {
        window.clearInterval(timer)
      }
    }
  }, [scanId, queryClient])

  if (!isAdmin) return <Navigate to="/" replace />

  // KPI calculations
  const totalNetworks = networks.length
  const wifiCount = networks.filter((n) => n.type === 'WIFI').length
  const ethernetCount = networks.filter((n) => n.type === 'ETHERNET').length
  const enabledCount = networks.filter((n) => n.enabled).length

  // Handlers
  const handleOpenAddDialog = () => {
    setEditingNetwork(null)
    setIsDialogOpen(true)
  }

  const handleOpenEditDialog = (net: NetworkProfile) => {
    setEditingNetwork(net)
    setIsDialogOpen(true)
  }

  const handleDeleteNetwork = async (id: string) => {
    if (confirm('Are you sure you want to delete this network?')) {
      try {
        await networkMutation.mutateAsync({ id, isDelete: true })
        setSelectedIds((prev) => prev.filter((pid) => pid !== id))
      } catch {
        // error handled in mutation
      }
    }
  }

  const handleSaveNetwork = async (e: FormEvent) => {
    e.preventDefault()
    const payload: Partial<NetworkProfile> = {
      name,
      type,
      enabled,
      cidr,
      scanTargets,
      gateway: gateway || undefined,
      description: description || undefined,
      sshUsername: sshUsername || undefined,
      sshPassword: sshPassword || undefined,
      snmpCommunity: snmpCommunity || undefined,
    }

    try {
      if (editingNetwork) {
        await networkMutation.mutateAsync({ id: editingNetwork.id, payload })
      } else {
        await networkMutation.mutateAsync({ payload })
      }
      setIsDialogOpen(false)
    } catch {
      // error handled in mutation
    }
  }

  const handleToggleSelectAll = () => {
    if (selectedIds.length === networks.length) {
      setSelectedIds([])
    } else {
      setSelectedIds(networks.map((n) => n.id))
    }
  }

  const handleToggleSelectRow = (id: string) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    )
  }

  const beginTrackingScan = (nextScanId: string) => {
    scanCompletionHandledRef.current = null
    setScanId(nextScanId)
    setScanStartedAt(Date.now())
    setElapsedSeconds(0)
    setScanProgress({
      percent: 0,
      completed: 0,
      total: 0,
      online: 0,
      newlySaved: 0,
      status: 'pending',
      error: null,
    })
  }

  const runScan = async (payload: { networkIds?: string[]; scanAllEnabled?: boolean }) => {
    if (scanId || scanMutation.isPending) {
      return
    }
    // Keep prior devices/summary visible while the new scan runs.
    setScanStartedAt(Date.now())
    setElapsedSeconds(0)
    setScanProgress({
      percent: 0,
      completed: 0,
      total: 0,
      online: 0,
      newlySaved: 0,
      status: 'pending',
      error: null,
    })
    try {
      const result = await scanMutation.mutateAsync(payload)
      beginTrackingScan(result.scanId)
    } catch (err) {
      if (err instanceof ApiRequestError && err.status === 409) {
        const payloadObj =
          typeof err.payload === 'object' && err.payload !== null
            ? (err.payload as { scanId?: unknown })
            : null
        const existingId =
          typeof payloadObj?.scanId === 'string' && payloadObj.scanId.trim()
            ? payloadObj.scanId.trim()
            : null
        if (existingId) {
          toast.message('A network scan is already in progress — showing live progress.')
          beginTrackingScan(existingId)
          return
        }
      }
      setScanStartedAt(null)
      setScanProgress(null)
    }
  }

  const scrollToProgress = () => {
    setTimeout(() => {
      document.getElementById('discovery-progress-section')?.scrollIntoView({ behavior: 'smooth' })
    }, 100)
  }

  const handleScanSelected = () => {
    if (selectedIds.length === 0 || scanning) return
    void runScan({ networkIds: selectedIds })
    scrollToProgress()
  }

  const handleScanAllEnabled = () => {
    if (scanning) return
    void runScan({ scanAllEnabled: true })
    scrollToProgress()
  }

  return (
    <div className="np-page">
      <PageHeader
        title="Ping Monitoring · Discovery"
        description="Scan custom networks and subnets, detect online hosts, and auto-save new devices to inventory."
      />

      {/* KPI Section */}
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="Network KPIs">
        <KpiCard label="Total Networks" value={totalNetworks} icon={Globe} tone="accent" />
        <KpiCard label="Wi-Fi" value={wifiCount} icon={Wifi} tone="success" />
        <KpiCard label="Ethernet" value={ethernetCount} icon={NetIcon} tone="accent" />
        <KpiCard label="Enabled" value={enabledCount} icon={CheckCircle2} tone="success" />
      </section>

      {/* Configured Networks List */}
      <section className="space-y-4" aria-label="Configured Networks">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <SectionHeading
            title="Configured Networks"
            description="Scan Wi-Fi and Ethernet ranges independently. Credentials are never shown in clear text."
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="secondary"
              disabled={scanning || selectedIds.length === 0}
              onClick={handleScanSelected}
            >
              <Play className="h-4 w-4" />
              Scan Selected ({selectedIds.length})
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={scanning || enabledCount === 0}
              onClick={handleScanAllEnabled}
            >
              <Play className="h-4 w-4" />
              Scan All Enabled
            </Button>
            <Button type="button" onClick={handleOpenAddDialog} disabled={scanning}>
              <Plus className="h-4 w-4" />
              Add Network
            </Button>
          </div>
        </div>

        <Card className="glass rounded-xl overflow-hidden">
          <CardContent className="p-0">
            {networks.length === 0 ? (
              <div className="py-8">
                <EmptyState
                  title="No networks configured"
                  description="Add a network to begin custom discovery scanning."
                />
              </div>
            ) : (
              <div className="overflow-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-12">
                        <Checkbox
                          checked={selectedIds.length === networks.length}
                          onCheckedChange={handleToggleSelectAll}
                          disabled={scanning}
                        />
                      </TableHead>
                      <TableHead>Name</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead>CIDR</TableHead>
                      <TableHead>Gateway</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead className="text-center">Devices</TableHead>
                      <TableHead className="text-center">Switches</TableHead>
                      <TableHead className="text-center">Online</TableHead>
                      <TableHead className="text-right w-24">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {networks.map((net) => (
                      <TableRow key={net.id}>
                        <TableCell>
                          <Checkbox
                            checked={selectedIds.includes(net.id)}
                            onCheckedChange={() => handleToggleSelectRow(net.id)}
                            disabled={scanning}
                          />
                        </TableCell>
                        <TableCell className="font-semibold">{net.name}</TableCell>
                        <TableCell>
                          <Badge variant={net.type === 'WIFI' ? 'secondary' : 'default'}>
                            {net.type}
                          </Badge>
                        </TableCell>
                        <TableCell className="mono">{net.cidr}</TableCell>
                        <TableCell className="mono">{net.gateway ?? '—'}</TableCell>
                        <TableCell>
                          <Badge variant={net.enabled ? 'outline' : 'danger'}>
                            {net.enabled ? 'Enabled' : 'Disabled'}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-center font-medium">{net.devices}</TableCell>
                        <TableCell className="text-center font-medium">{net.switches}</TableCell>
                        <TableCell className="text-center font-medium text-emerald-500">
                          {net.online}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-1.5">
                            <Button
                              size="icon"
                              variant="ghost"
                              className="h-8 w-8 text-muted-foreground hover:text-foreground"
                              title="Edit Network"
                              disabled={scanning}
                              onClick={() => handleOpenEditDialog(net)}
                            >
                              <Edit2 className="h-4 w-4" />
                            </Button>
                            <Button
                              size="icon"
                              variant="ghost"
                              className="h-8 w-8 text-muted-foreground hover:text-destructive"
                              title="Delete Network"
                              disabled={scanning}
                              onClick={() => handleDeleteNetwork(net.id)}
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      </section>

      {/* Discovery Progress */}
      {scanning ? (
        <section id="discovery-progress-section" className="space-y-4" aria-label="Discovery progress">
          <SectionHeading
            title="Discovery Progress"
            description="Pinging hosts in parallel across the selected target ranges."
          />
          <Card className="glass overflow-hidden rounded-xl border-l-[3px] border-l-primary">
            <CardContent className="space-y-6 py-8">
              <div className="flex flex-col items-center gap-4 text-center sm:flex-row sm:text-left">
                <motion.div
                  animate={{ rotate: 360 }}
                  transition={{ repeat: Infinity, duration: 2, ease: 'linear' }}
                  className="flex h-16 w-16 shrink-0 items-center justify-center rounded-2xl bg-primary/15 text-primary shadow-inner"
                >
                  <Radar className="h-8 w-8" />
                </motion.div>
                <div className="min-w-0 flex-1 space-y-1">
                  <p className="text-lg font-semibold tracking-tight">Network scan in progress…</p>
                  <p className="text-sm text-muted-foreground">
                    {scanProgress && scanProgress.total > 0
                      ? `Pinged ${scanProgress.completed} of ${scanProgress.total} hosts. New devices are saved immediately; Nmap enrichment continues in the background.`
                      : 'Scan job started. Waiting for host progress…'}
                  </p>
                </div>
                <div className="text-center sm:text-right">
                  <p className="text-3xl font-bold tabular-nums tracking-tight text-primary">
                    {scanProgress?.percent ?? 0}%
                  </p>
                  <p className="text-xs uppercase tracking-wider text-muted-foreground">
                    {formatElapsed(elapsedSeconds)} elapsed
                  </p>
                </div>
              </div>
              <div className="space-y-2">
                <Progress value={scanProgress?.percent ?? 0} className="h-3" />
                <div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
                  <span>
                    {scanProgress && scanProgress.total > 0
                      ? `${scanProgress.completed} / ${scanProgress.total} hosts`
                      : 'Preparing scan'}
                  </span>
                  <span>
                    Online {scanProgress?.online ?? 0}
                    {scanProgress && scanProgress.newlySaved > 0
                      ? ` · ${scanProgress.newlySaved} new`
                      : ''}
                  </span>
                </div>
              </div>
            </CardContent>
          </Card>
        </section>
      ) : null}

      {/* Discovered Devices */}
      {devices.length > 0 ? (
        <section className="space-y-4" aria-label="Discovered devices">
          <SectionHeading
            title="Discovered Devices"
            description="Hosts found in the scanned range."
          />

          <Card className="glass rounded-xl">
            <CardContent className="flex flex-wrap items-center justify-between gap-4 py-4">
              <div className="flex flex-wrap items-center gap-6">
                <label className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Checkbox
                    checked={showOnlineOnly}
                    onCheckedChange={(checked) => setShowOnlineOnly(Boolean(checked))}
                  />
                  Show online hosts only
                </label>
                <span className="text-sm text-muted-foreground">
                  {visible.length} of {devices.length} hosts
                </span>
              </div>

              {/* Network Filter Dropdown */}
              <div className="flex items-center gap-2">
                <Label htmlFor="filterNetwork" className="text-sm text-muted-foreground">
                  Filter by Network:
                </Label>
                <Select
                  value={filterNetworkId}
                  onValueChange={(val) => setFilterNetworkId(val)}
                >
                  <SelectTrigger className="w-[180px] bg-background border-border">
                    <SelectValue placeholder="All Networks" />
                  </SelectTrigger>
                  <SelectContent className="bg-card border-border">
                    <SelectItem value="all">All Networks</SelectItem>
                    {networks.map((n) => (
                      <SelectItem key={n.id} value={n.id}>
                        {n.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </CardContent>
          </Card>

          {visible.length === 0 ? (
            <EmptyState title="No hosts match the selected filters" />
          ) : (
            <Card className="glass overflow-hidden rounded-xl">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Table2 className="h-4 w-4 text-primary" />
                  Host table
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0 pt-0">
                <div className="overflow-auto px-2 pb-2">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>IP</TableHead>
                        <TableHead>Hostname</TableHead>
                        <TableHead>Network</TableHead>
                        <TableHead>Vendor</TableHead>
                        <TableHead>OS</TableHead>
                        <TableHead>Device type</TableHead>
                        <TableHead>Confidence</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>RTT</TableHead>
                        <TableHead>Enrichment</TableHead>
                        <TableHead>Saved</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {pagination.pageItems.map((device) => (
                        <TableRow key={device.ipAddress}>
                          <TableCell className="mono">{device.ipAddress}</TableCell>
                          <TableCell className="font-medium">{device.hostname ?? '—'}</TableCell>
                          <TableCell>
                            <Badge variant="outline" className="border-primary/20 text-primary">
                              {getDeviceNetworkName(device.ipAddress)}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-muted-foreground">
                            {device.vendor || '—'}
                          </TableCell>
                          <TableCell className="text-muted-foreground">
                            {device.operatingSystem || '—'}
                          </TableCell>
                          <TableCell>
                            {device.status === 'Online'
                              ? displayDeviceType(
                                  device.deviceType,
                                  device.classificationConfidence,
                                )
                              : '—'}
                          </TableCell>
                          <TableCell className="mono text-muted-foreground">
                            {device.classificationConfidence != null
                              ? `${device.classificationConfidence}%`
                              : '—'}
                          </TableCell>
                          <TableCell>
                            <StatusBadge status={device.status} />
                          </TableCell>
                          <TableCell className="mono">{formatMs(device.responseTime)}</TableCell>
                          <TableCell>
                            {device.saved &&
                            (device.discoveryStatus === 'pending' ||
                              device.discoveryStatus === 'enriching') ? (
                              <Badge variant="secondary" className="gap-1">
                                <Loader2 className="h-3 w-3 animate-spin" />
                                Enriching
                              </Badge>
                            ) : device.discoveryStatus === 'failed' ? (
                              <Badge variant="danger">Enrichment failed</Badge>
                            ) : device.saved ? (
                              <Badge variant="outline">Ready</Badge>
                            ) : (
                              <span className="text-sm text-muted-foreground">—</span>
                            )}
                          </TableCell>
                          <TableCell>
                            {device.saved ? (
                              <Badge variant="default">New device</Badge>
                            ) : device.status === 'Online' ? (
                              <span className="text-sm text-muted-foreground">Already monitored</span>
                            ) : (
                              <span className="text-sm text-muted-foreground">—</span>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <div className="border-t border-border px-4 pb-2">
                  <PaginationControls
                    page={pagination.page}
                    totalPages={pagination.totalPages}
                    total={pagination.total}
                    limit={pagination.limit}
                    onPageChange={pagination.setPage}
                    onLimitChange={pagination.setLimit}
                  />
                </div>
              </CardContent>
            </Card>
          )}
        </section>
      ) : null}

      {/* Scan Results KPI Card group */}
      {summary ? (
        <section className="space-y-4" aria-label="Import results">
          <SectionHeading
            title="Import Results"
            description="Outcome of the latest discovery sweep."
          />
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <KpiCard label="Scanned" value={scannedCount} icon={Radar} tone="accent" />
            <KpiCard label="Online" value={onlineCount} icon={CheckCircle2} tone="success" />
            <KpiCard label="Offline" value={offlineCount} icon={NetIcon} tone="danger" />
            <KpiCard label="Newly saved" value={newSavedCount} icon={Upload} tone="accent" />
            {enrichingCount > 0 ? (
              <KpiCard
                label="Enriching"
                value={enrichingCount}
                icon={Loader2}
                tone="accent"
              />
            ) : null}
            {readyCount > 0 && enrichingCount === 0 && newSavedCount > 0 ? (
              <KpiCard label="Ready" value={readyCount} icon={CheckCircle2} tone="success" />
            ) : null}
          </div>
        </section>
      ) : null}

      {/* Add / Edit Dialog Modal */}
      <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <DialogContent className="sm:max-w-[550px] max-h-[90vh] overflow-y-auto bg-card border-border">
          <form onSubmit={handleSaveNetwork} className="space-y-4">
            <DialogHeader>
              <DialogTitle className="text-lg font-bold flex items-center gap-2">
                <Settings className="h-5 w-5 text-primary" />
                {editingNetwork ? 'Edit Network' : 'Add Network'}
              </DialogTitle>
              <DialogDescription>
                Define the network settings and subnet scanning parameters.
              </DialogDescription>
            </DialogHeader>

            <div className="grid gap-4 py-2">
              {/* Name */}
              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="name" className="text-right">
                  Name *
                </Label>
                <Input
                  id="name"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Ethernet Lab"
                  className="col-span-3"
                />
              </div>

              {/* Type and Enabled flag */}
              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="type" className="text-right">
                  Type *
                </Label>
                <div className="col-span-3 flex items-center justify-between gap-4">
                  <Select
                    value={type}
                    onValueChange={(val: 'ETHERNET' | 'WIFI') => setType(val)}
                  >
                    <SelectTrigger className="w-[180px] bg-background border-border">
                      <SelectValue placeholder="Select type" />
                    </SelectTrigger>
                    <SelectContent className="bg-card border-border">
                      <SelectItem value="ETHERNET">ETHERNET</SelectItem>
                      <SelectItem value="WIFI">WIFI</SelectItem>
                    </SelectContent>
                  </Select>

                  <div className="flex items-center gap-2">
                    <Checkbox
                      id="enabled"
                      checked={enabled}
                      onCheckedChange={(checked) => setEnabled(Boolean(checked))}
                    />
                    <Label htmlFor="enabled" className="text-sm font-normal cursor-pointer select-none">
                      Network enabled for scanning
                    </Label>
                  </div>
                </div>
              </div>

              {/* CIDR */}
              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="cidr" className="text-right">
                  CIDR *
                </Label>
                <Input
                  id="cidr"
                  required
                  value={cidr}
                  onChange={(e) => setCidr(e.target.value)}
                  placeholder="192.168.10.0/24"
                  className="col-span-3 mono"
                />
              </div>

              {/* Scan Targets */}
              <div className="grid grid-cols-4 items-start gap-4">
                <Label htmlFor="scanTargets" className="text-right pt-2">
                  Scan Targets *
                </Label>
                <div className="col-span-3 space-y-1.5">
                  <Input
                    id="scanTargets"
                    required
                    value={scanTargets}
                    onChange={(e) => setScanTargets(e.target.value)}
                    placeholder="192.168.10.0/24 or 192.168.10.1-192.168.10.254"
                    className="mono"
                  />
                  <p className="text-[11px] text-muted-foreground leading-relaxed">
                    Accepts CIDR, single IPs, or hyphen ranges (e.g. 192.168.10.1-192.168.10.254). Comma-separated lists allowed.
                  </p>
                </div>
              </div>

              {/* Gateway and Description */}
              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="gateway" className="text-right">
                  Gateway
                </Label>
                <Input
                  id="gateway"
                  value={gateway}
                  onChange={(e) => setGateway(e.target.value)}
                  placeholder="192.168.10.1"
                  className="col-span-3 mono"
                />
              </div>

              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="description" className="text-right">
                  Description
                </Label>
                <Input
                  id="description"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Optional notes"
                  className="col-span-3"
                />
              </div>

              {/* Credentials Section */}
              <div className="border-t border-border/60 my-2 pt-3">
                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3 pl-2">
                  Credentials & SNMP Settings
                </p>
                <div className="space-y-4">
                  {/* SSH Username and Password */}
                  <div className="grid grid-cols-4 items-center gap-4">
                    <Label htmlFor="sshUsername" className="text-right text-xs">
                      SSH Username
                    </Label>
                    <div className="col-span-3 grid grid-cols-2 gap-2">
                      <Input
                        id="sshUsername"
                        value={sshUsername}
                        onChange={(e) => setSshUsername(e.target.value)}
                        placeholder="sshadmin"
                      />
                      <Input
                        id="sshPassword"
                        type="password"
                        value={sshPassword}
                        onChange={(e) => setSshPassword(e.target.value)}
                        placeholder={editingNetwork?.sshPasswordSet ? "Leave blank to keep" : "Password"}
                      />
                    </div>
                  </div>

                  {/* SNMP Community */}
                  <div className="grid grid-cols-4 items-center gap-4">
                    <Label htmlFor="snmpCommunity" className="text-right text-xs">
                      SNMP Community
                    </Label>
                    <Input
                      id="snmpCommunity"
                      value={snmpCommunity}
                      onChange={(e) => setSnmpCommunity(e.target.value)}
                      placeholder={
                        editingNetwork?.snmpCommunityConfigured
                          ? 'Leave blank to keep'
                          : 'public'
                      }
                      className="col-span-3"
                    />
                  </div>
                </div>
              </div>
            </div>

            <DialogFooter className="border-t border-border/40 pt-4 mt-2">
              <Button
                type="button"
                variant="secondary"
                onClick={() => setIsDialogOpen(false)}
                disabled={networkMutation.isPending}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={networkMutation.isPending}>
                {networkMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Sparkles className="h-4 w-4" />
                )}
                Save
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
