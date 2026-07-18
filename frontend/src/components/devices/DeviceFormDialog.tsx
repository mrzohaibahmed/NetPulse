import { useEffect } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import { DEFAULT_DEVICE_TYPE, DEVICE_TYPES } from '@/constants/devices'
import type { Device, DevicePayload } from '@/types'
import { useDeviceMutations } from '@/hooks/queries'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

const schema = z.object({
  hostname: z.string().min(1, 'Hostname is required'),
  ipAddress: z
    .string()
    .regex(
      /^(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)$/,
      'Enter a valid IPv4 address',
    ),
  deviceType: z.string().min(1),
  critical: z.boolean(),
  monitor: z.boolean(),
  pingInterval: z.number().min(5).nullable().optional(),
  pingTimeoutMs: z.number().min(100).nullable().optional(),
  pingRetries: z.number().min(1).nullable().optional(),
})

type FormValues = z.infer<typeof schema>

interface DeviceFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  device?: Device | null
}

export function DeviceFormDialog({ open, onOpenChange, device }: DeviceFormDialogProps) {
  const { create, update } = useDeviceMutations()
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      hostname: '',
      ipAddress: '',
      deviceType: DEFAULT_DEVICE_TYPE,
      critical: false,
      monitor: true,
      pingInterval: null,
      pingTimeoutMs: null,
      pingRetries: null,
    },
  })

  useEffect(() => {
    if (!open) return
    if (device) {
      form.reset({
        hostname: device.hostname,
        ipAddress: device.ipAddress,
        deviceType: device.deviceType,
        critical: device.critical,
        monitor: device.monitor,
        pingInterval: device.pingInterval ?? null,
        pingTimeoutMs: device.pingTimeoutMs ?? null,
        pingRetries: device.pingRetries ?? null,
      })
    } else {
      form.reset({
        hostname: '',
        ipAddress: '',
        deviceType: DEFAULT_DEVICE_TYPE,
        critical: false,
        monitor: true,
        pingInterval: null,
        pingTimeoutMs: null,
        pingRetries: null,
      })
    }
  }, [device, open, form])

  const onSubmit = form.handleSubmit(async (values) => {
    const payload: DevicePayload = {
      hostname: values.hostname,
      ipAddress: values.ipAddress,
      deviceType: values.deviceType,
      critical: values.critical,
      monitor: values.monitor,
      pingInterval: values.pingInterval ?? null,
      pingTimeoutMs: values.pingTimeoutMs ?? null,
      pingRetries: values.pingRetries ?? null,
    }

    if (device) {
      await update.mutateAsync({ id: device._id, payload })
    } else {
      await create.mutateAsync(payload)
    }
    onOpenChange(false)
  })

  const saving = create.isPending || update.isPending

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{device ? 'Edit device' : 'Add device'}</DialogTitle>
          <DialogDescription>
            {device ? 'Update monitoring settings for this host.' : 'Register a new host for monitoring.'}
          </DialogDescription>
        </DialogHeader>

        <form className="space-y-4" onSubmit={(e) => void onSubmit(e)}>
          <div className="space-y-1.5">
            <Label htmlFor="hostname">Hostname</Label>
            <Input id="hostname" {...form.register('hostname')} />
            {form.formState.errors.hostname ? (
              <p className="text-xs text-danger">{form.formState.errors.hostname.message}</p>
            ) : null}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ipAddress">IP address</Label>
            <Input id="ipAddress" className="mono" {...form.register('ipAddress')} />
            {form.formState.errors.ipAddress ? (
              <p className="text-xs text-danger">{form.formState.errors.ipAddress.message}</p>
            ) : null}
          </div>

          <div className="space-y-1.5">
            <Label>Device type</Label>
            <Select
              value={form.watch('deviceType')}
              onValueChange={(value) => form.setValue('deviceType', value)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DEVICE_TYPES.map((type) => (
                  <SelectItem key={type} value={type}>
                    {type}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="pingInterval">Interval (s)</Label>
              <Input
                id="pingInterval"
                type="number"
                min={5}
                value={form.watch('pingInterval') ?? ''}
                onChange={(e) =>
                  form.setValue('pingInterval', e.target.value ? Number(e.target.value) : null)
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="pingTimeoutMs">Timeout (ms)</Label>
              <Input
                id="pingTimeoutMs"
                type="number"
                min={100}
                value={form.watch('pingTimeoutMs') ?? ''}
                onChange={(e) =>
                  form.setValue('pingTimeoutMs', e.target.value ? Number(e.target.value) : null)
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="pingRetries">Retries</Label>
              <Input
                id="pingRetries"
                type="number"
                min={1}
                value={form.watch('pingRetries') ?? ''}
                onChange={(e) =>
                  form.setValue('pingRetries', e.target.value ? Number(e.target.value) : null)
                }
              />
            </div>
          </div>

          <div className="flex flex-wrap gap-4">
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={form.watch('monitor')}
                onCheckedChange={(checked) => form.setValue('monitor', Boolean(checked))}
              />
              Include in automatic monitoring
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={form.watch('critical')}
                onCheckedChange={(checked) => form.setValue('critical', Boolean(checked))}
              />
              Mark as critical
            </label>
          </div>

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? 'Saving…' : device ? 'Save changes' : 'Create device'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
