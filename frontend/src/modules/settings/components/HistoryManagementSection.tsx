import { useState } from 'react'
import { Trash2 } from 'lucide-react'
import { useHistoryDeletionMutation } from '@/hooks/queries'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'

export type HistoryDeletionScope = 'ping' | 'telemetry' | 'incidents' | 'all'

const SCOPE_LABELS: Record<HistoryDeletionScope, string> = {
  ping: 'Delete Ping History',
  telemetry: 'Delete Telemetry History',
  incidents: 'Delete Incident History',
  all: 'Delete All History',
}

const SCOPE_DESCRIPTIONS: Record<HistoryDeletionScope, string> = {
  ping: `Delete all stored ping history?

This permanently deletes records from pingHistory.
Current device configuration and monitoring will not be affected.`,
  telemetry: `Delete all telemetry history?

This permanently deletes interface statistics, eligibility results,
and stored storm telemetry history.

Current monitoring will continue normally.`,
  incidents: `Delete all incident history?

This permanently deletes stored mitigation and recovery history.`,
  all: `Delete ALL retained monitoring history?

This permanently deletes all retention-managed historical records.

Device configuration, interfaces, topology, current status,
and monitoring configuration will NOT be deleted.

This action cannot be undone.`,
}

export function HistoryManagementSection() {
  const deleteHistory = useHistoryDeletionMutation()
  const [activeScope, setActiveScope] = useState<HistoryDeletionScope | null>(null)
  const [confirmText, setConfirmText] = useState('')

  const closeDialog = () => {
    setActiveScope(null)
    setConfirmText('')
  }

  const canConfirm =
    activeScope !== null &&
    !deleteHistory.isPending &&
    (activeScope !== 'all' || confirmText === 'DELETE')

  const handleConfirm = async () => {
    if (!activeScope || !canConfirm) return
    try {
      await deleteHistory.mutateAsync(activeScope)
      closeDialog()
    } catch {
      // Toast handled by mutation onError.
    }
  }

  const scopes: HistoryDeletionScope[] = ['ping', 'telemetry', 'incidents', 'all']

  return (
    <>
      <Card className="glass">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Trash2 className="h-5 w-5 text-primary" />
            History Management
          </CardTitle>
          <CardDescription>Manually delete stored monitoring history.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-3">
          {scopes.map((scope) => (
            <Button
              key={scope}
              type="button"
              variant={scope === 'all' ? 'destructive' : 'outline'}
              disabled={deleteHistory.isPending}
              onClick={() => {
                setConfirmText('')
                setActiveScope(scope)
              }}
            >
              {SCOPE_LABELS[scope]}
            </Button>
          ))}
        </CardContent>
      </Card>

      <Dialog
        open={activeScope !== null}
        onOpenChange={(open) => {
          if (!open) closeDialog()
        }}
      >
        <DialogContent
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
            }
          }}
        >
          <DialogHeader>
            <DialogTitle>
              {activeScope ? SCOPE_LABELS[activeScope] : 'Delete history'}
            </DialogTitle>
            <DialogDescription className="whitespace-pre-line">
              {activeScope ? SCOPE_DESCRIPTIONS[activeScope] : ''}
            </DialogDescription>
          </DialogHeader>

          {activeScope === 'all' ? (
            <div className="space-y-2">
              <Label htmlFor="history-delete-confirm">
                Type <span className="font-semibold">DELETE</span> to confirm
              </Label>
              <Input
                id="history-delete-confirm"
                value={confirmText}
                onChange={(event) => setConfirmText(event.target.value)}
                autoComplete="off"
                disabled={deleteHistory.isPending}
              />
            </div>
          ) : null}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={closeDialog}
              disabled={deleteHistory.isPending}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={!canConfirm}
              onClick={() => void handleConfirm()}
            >
              {deleteHistory.isPending ? 'Deleting…' : 'Delete history'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
