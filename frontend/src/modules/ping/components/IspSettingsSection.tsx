import { useEffect, useState } from 'react'
import { Globe, Radar, Save } from 'lucide-react'
import { useIspMutations, useIspsQuery } from '@/hooks/queries'
import { normalizeIspSlots } from '@/modules/ping/components/IspConnectivitySection'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/ui/card'
import { Checkbox } from '@/shared/ui/checkbox'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'
import { cn } from '@/lib/utils'
import { formatMs, formatRelative } from '@/utils/format'
import type { IspConnection } from '@/types'

type IspDraft = {
  name: string
  target: string
  monitor: boolean
}

function draftFromIsp(isp: IspConnection): IspDraft {
  return {
    name: isp.name,
    target: isp.target,
    monitor: isp.monitor,
  }
}

function draftsEqual(a: IspDraft, b: IspDraft): boolean {
  return a.name === b.name && a.target === b.target && a.monitor === b.monitor
}

export function IspSettingsSection() {
  const ispsQuery = useIspsQuery()
  const { update, scan } = useIspMutations()
  const slots = normalizeIspSlots(ispsQuery.data)
  const [drafts, setDrafts] = useState<Record<string, IspDraft>>({})
  const [dirtyIds, setDirtyIds] = useState<Set<string>>(() => new Set())

  useEffect(() => {
    if (!ispsQuery.data) return
    setDrafts((prev) => {
      const next = { ...prev }
      for (const isp of normalizeIspSlots(ispsQuery.data)) {
        if (!dirtyIds.has(isp.id)) {
          next[isp.id] = draftFromIsp(isp)
        }
      }
      return next
    })
  }, [ispsQuery.data, dirtyIds])

  const setDraft = (id: string, patch: Partial<IspDraft>) => {
    setDirtyIds((prev) => new Set(prev).add(id))
    setDrafts((prev) => ({
      ...prev,
      [id]: { ...prev[id], ...patch },
    }))
  }

  const handleSave = async (isp: IspConnection) => {
    const draft = drafts[isp.id]
    if (!draft?.name.trim()) return
    await update.mutateAsync({
      id: isp.id,
      payload: {
        name: draft.name.trim(),
        target: draft.target.trim(),
        monitor: draft.monitor,
      },
    })
    setDirtyIds((prev) => {
      const next = new Set(prev)
      next.delete(isp.id)
      return next
    })
  }

  const handleScan = async (isp: IspConnection) => {
    const draft = drafts[isp.id]
    if (!draft?.target.trim()) return
    if (!draftsEqual(draft, draftFromIsp(isp))) {
      await update.mutateAsync({
        id: isp.id,
        payload: {
          name: draft.name.trim(),
          target: draft.target.trim(),
          monitor: draft.monitor,
        },
      })
    }
    await scan.mutateAsync(isp.id)
  }

  const savingId = update.isPending ? update.variables?.id : null
  const scanningId = scan.isPending ? scan.variables : null

  return (
    <Card className="glass">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Globe className="h-5 w-5 text-primary" />
          ISP connectivity
        </CardTitle>
        <CardDescription>
          Configure up to three upstream ISP links. Each target is pinged on the global ping
          interval when monitoring is enabled. Use a target that represents that link (gateway,
          ISP DNS, etc.).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {ispsQuery.isLoading && !ispsQuery.data ? (
          <p className="text-sm text-muted-foreground">Loading ISP connections…</p>
        ) : (
          slots.map((isp) => {
            const draft = drafts[isp.id] ?? draftFromIsp(isp)
            const dirty = dirtyIds.has(isp.id)
            const canScan = Boolean(draft.target.trim())
            const isSaving = savingId === isp.id
            const isScanning = scanningId === isp.id

            return (
              <div
                key={isp.id}
                className={cn(
                  'rounded-xl border border-border/60 bg-secondary/15 p-4 space-y-4',
                  isp.status === 'Offline' && 'border-danger/30',
                  isp.status === 'Online' && 'border-success/25',
                )}
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-semibold">{isp.id}</p>
                    <StatusBadge status={isp.status} pulse={isp.status === 'Online'} />
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {isp.status === 'Online' && isp.responseTime != null ? (
                      <span>{formatMs(isp.responseTime)}</span>
                    ) : null}
                    {isp.lastSeen ? (
                      <span className={isp.status === 'Online' ? ' ml-2' : ''}>
                        Last seen {formatRelative(isp.lastSeen)}
                      </span>
                    ) : null}
                  </div>
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor={`isp-name-${isp.id}`}>Name</Label>
                    <Input
                      id={`isp-name-${isp.id}`}
                      value={draft.name}
                      onChange={(e) => setDraft(isp.id, { name: e.target.value })}
                      placeholder="Primary Fiber"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={`isp-target-${isp.id}`}>Ping target</Label>
                    <Input
                      id={`isp-target-${isp.id}`}
                      value={draft.target}
                      onChange={(e) => setDraft(isp.id, { target: e.target.value })}
                      placeholder="8.8.8.8 or isp-gateway.example"
                    />
                  </div>
                </div>

                <label className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={draft.monitor}
                    onCheckedChange={(checked) =>
                      setDraft(isp.id, { monitor: Boolean(checked) })
                    }
                  />
                  Enable automatic monitoring
                </label>

                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    disabled={!draft.name.trim() || isSaving || (!dirty && !isSaving)}
                    onClick={() => void handleSave(isp)}
                  >
                    <Save className="h-4 w-4" />
                    {isSaving ? 'Saving…' : dirty ? 'Save' : 'Saved'}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={!canScan || isScanning || isSaving}
                    onClick={() => void handleScan(isp)}
                  >
                    <Radar className="h-4 w-4" />
                    {isScanning ? 'Scanning…' : 'Scan now'}
                  </Button>
                </div>
              </div>
            )
          })
        )}
      </CardContent>
    </Card>
  )
}
