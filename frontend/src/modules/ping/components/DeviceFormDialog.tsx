import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import { Eye, EyeOff } from 'lucide-react'
import { DEFAULT_DEVICE_TYPE, DEVICE_TYPES } from '@/modules/ping/constants/devices'
import { SITE_LOCATIONS } from '@/modules/ping/constants/locations'
import type { Device, DevicePayload } from '@/types'
import { useDeviceMutations } from '@/hooks/queries'
import { Button } from '@/shared/ui/button'
import { Checkbox } from '@/shared/ui/checkbox'
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select'

const IPV4_RE = /^(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)$/
const ILO_ONLY_TYPES = new Set(['server', 'linux server', 'esxi server'])

/** Reject URL-like iLO values; backend does authoritative validation. */
function looksLikeUrlAddress(value: string): boolean {
  const v = value.trim().toLowerCase()
  return (
    v.includes('://') ||
    v.includes('/') ||
    v.includes('@') ||
    v.includes('?') ||
    v.includes('#') ||
    v.includes(':')
  )
}

export function isIloOnlyDeviceType(deviceType: string): boolean {
  return ILO_ONLY_TYPES.has(deviceType.trim().toLowerCase())
}

const baseSchema = z.object({
  hostname: z.string().trim().min(1, 'Hostname is required'),
  ipAddress: z.string().trim(),
  iloAddress: z.string().trim(),
  deviceType: z.string().trim().min(1, 'Device type is required'),
  vendor: z.string().trim(),
  username: z.string().trim(),
  password: z.string(),
  enableSecret: z.string(),
  iloUsername: z.string().trim(),
  iloPassword: z.string(),
  critical: z.boolean(),
  monitor: z.boolean(),
  showOnDashboard: z.boolean(),
  pingInterval: z.number().min(5).nullable().optional(),
  pingTimeoutMs: z.number().min(100).nullable().optional(),
  pingRetries: z.number().min(1).nullable().optional(),
  location: z.string().trim().optional(),
})

export const deviceFormSchema = baseSchema.superRefine((values, ctx) => {
  const ip = values.ipAddress.trim()
  const ilo = values.iloAddress.trim()

  if (!ip && !ilo) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: 'Provide an OS IP address and/or an iLO address',
      path: ['ipAddress'],
    })
  }

  if (ip && !IPV4_RE.test(ip)) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: 'Enter a valid IPv4 address',
      path: ['ipAddress'],
    })
  }

  if (ilo) {
    if (looksLikeUrlAddress(ilo) || /\s/.test(values.iloAddress)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'Enter an IPv4 or DNS hostname (not a URL)',
        path: ['iloAddress'],
      })
    }
  }

  if (!ip && ilo && !isIloOnlyDeviceType(values.deviceType)) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: 'iLO-only devices must be Server, Linux Server, or ESXi Server',
      path: ['deviceType'],
    })
  }
})

export type DeviceFormValues = z.infer<typeof baseSchema>

interface DeviceFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  device?: Device | null
}

export function DeviceFormDialog({ open, onOpenChange, device }: DeviceFormDialogProps) {
  const { create, update } = useDeviceMutations()
  const [showPassword, setShowPassword] = useState(false)
  const [showEnableSecret, setShowEnableSecret] = useState(false)
  const [showIloPassword, setShowIloPassword] = useState(false)
  const form = useForm<DeviceFormValues>({
    resolver: zodResolver(deviceFormSchema),
    defaultValues: {
      hostname: '',
      ipAddress: '',
      iloAddress: '',
      deviceType: DEFAULT_DEVICE_TYPE,
      vendor: '',
      username: '',
      password: '',
      enableSecret: '',
      iloUsername: '',
      iloPassword: '',
      critical: false,
      monitor: true,
      showOnDashboard: false,
      pingInterval: null,
      pingTimeoutMs: null,
      pingRetries: null,
      location: '',
    },
  })

  useEffect(() => {
    if (!open) return
    if (device) {
      form.reset({
        hostname: device.hostname,
        ipAddress: device.ipAddress ?? '',
        iloAddress: device.iloAddress ?? '',
        deviceType: device.deviceType,
        vendor: device.credentials?.sshVendor ?? '',
        username: device.credentials?.sshUsername ?? '',
        password: '',
        enableSecret: '',
        iloUsername: device.credentials?.iloUsername ?? '',
        iloPassword: '',
        critical: device.critical,
        monitor: device.monitor,
        showOnDashboard: device.showOnDashboard ?? true,
        pingInterval: device.pingInterval ?? null,
        pingTimeoutMs: device.pingTimeoutMs ?? null,
        pingRetries: device.pingRetries ?? null,
        location: device.location ?? '',
      })
    } else {
      form.reset({
        hostname: '',
        ipAddress: '',
        iloAddress: '',
        deviceType: DEFAULT_DEVICE_TYPE,
        vendor: '',
        username: '',
        password: '',
        enableSecret: '',
        iloUsername: '',
        iloPassword: '',
        critical: false,
        monitor: true,
        showOnDashboard: false,
        pingInterval: null,
        pingTimeoutMs: null,
        pingRetries: null,
        location: '',
      })
    }
    setShowPassword(false)
    setShowEnableSecret(false)
    setShowIloPassword(false)
    form.clearErrors()
  }, [device, open, form])

  const watchedIp = form.watch('ipAddress')
  const watchedIlo = form.watch('iloAddress')
  const isIloOnly = !watchedIp.trim() && Boolean(watchedIlo.trim())

  useEffect(() => {
    if (!open) return
    if (isIloOnly && form.getValues('monitor')) {
      form.setValue('monitor', false)
    }
  }, [isIloOnly, open, form])

  const onSubmit = form.handleSubmit(async (values) => {
    const nextPassword = values.password.trim()
    const nextEnableSecret = values.enableSecret.trim()
    const nextUsername = values.username.trim()
    const nextIloPassword = values.iloPassword.trim()
    const nextIloUsername = values.iloUsername.trim()
    const ip = values.ipAddress.trim()
    const ilo = values.iloAddress.trim()

    const credentialsPayload = device
      ? {
          sshUsername: nextUsername,
          sshVendor: values.vendor.trim(),
          iloUsername: nextIloUsername,
          ...(nextPassword ? { sshPassword: nextPassword } : {}),
          ...(nextEnableSecret ? { sshSecret: nextEnableSecret } : {}),
          ...(nextIloPassword ? { iloPassword: nextIloPassword } : {}),
        }
      : {
          ...(nextUsername ? { sshUsername: nextUsername } : {}),
          ...(values.vendor.trim() ? { sshVendor: values.vendor.trim() } : {}),
          ...(nextIloUsername ? { iloUsername: nextIloUsername } : {}),
          ...(nextPassword ? { sshPassword: nextPassword } : {}),
          ...(nextEnableSecret ? { sshSecret: nextEnableSecret } : {}),
          ...(nextIloPassword ? { iloPassword: nextIloPassword } : {}),
        }

    const isServer = values.deviceType.trim().toLowerCase() === 'server'
    const payload: DevicePayload = {
      hostname: values.hostname,
      deviceType: values.deviceType,
      critical: values.critical,
      monitor: ip ? values.monitor : false,
      showOnDashboard: isServer ? values.showOnDashboard : false,
      pingInterval: values.pingInterval ?? null,
      pingTimeoutMs: values.pingTimeoutMs ?? null,
      pingRetries: values.pingRetries ?? null,
      location: values.location?.trim() ? values.location.trim() : null,
      ipAddress: ip || null,
      iloAddress: ilo || null,
    }

    if (device || Object.keys(credentialsPayload).length > 0) {
      payload.credentials = credentialsPayload
    }

    if (device) {
      await update.mutateAsync({ id: device._id, payload })
    } else {
      await create.mutateAsync(payload)
    }
    onOpenChange(false)
  })

  const saving = create.isPending || update.isPending
  const selectedDeviceType = form.watch('deviceType')
  const isServerType = selectedDeviceType.trim().toLowerCase() === 'server'

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{device ? 'Edit device' : 'Add device'}</DialogTitle>
          <DialogDescription>
            {device
              ? 'Update monitoring settings for this host.'
              : 'Register a new host for monitoring. iLO-only servers may omit the OS IP.'}
          </DialogDescription>
        </DialogHeader>

        <form className="space-y-5" onSubmit={(e) => void onSubmit(e)}>
          <fieldset className="space-y-3 rounded-xl border border-border/60 bg-secondary/20 p-4">
            <legend className="px-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Identity
            </legend>
            <div className="space-y-1.5">
              <Label htmlFor="hostname">Hostname</Label>
              <Input id="hostname" {...form.register('hostname')} />
              {form.formState.errors.hostname ? (
                <p className="text-xs text-danger">{form.formState.errors.hostname.message}</p>
              ) : null}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="ipAddress">OS IP address</Label>
              <Input id="ipAddress" className="mono" {...form.register('ipAddress')} />
              {form.formState.errors.ipAddress ? (
                <p className="text-xs text-danger">{form.formState.errors.ipAddress.message}</p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Optional for Server / Linux Server / ESXi Server when an iLO address is set.
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="iloAddress">iLO address</Label>
              <Input
                id="iloAddress"
                className="mono"
                {...form.register('iloAddress')}
                placeholder="IPv4 or DNS hostname"
              />
              {form.formState.errors.iloAddress ? (
                <p className="text-xs text-danger">{form.formState.errors.iloAddress.message}</p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Do not enter a URL (no https:// or /redfish path).
                </p>
              )}
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
                  {device?.deviceType &&
                  !(DEVICE_TYPES as readonly string[]).includes(device.deviceType) ? (
                    <SelectItem value={device.deviceType}>{device.deviceType}</SelectItem>
                  ) : null}
                </SelectContent>
              </Select>
              {form.formState.errors.deviceType ? (
                <p className="text-xs text-danger">{form.formState.errors.deviceType.message}</p>
              ) : null}
            </div>

            <div className="space-y-1.5">
              <Label>Location</Label>
              <Select
                value={form.watch('location') || '__none__'}
                onValueChange={(value) =>
                  form.setValue('location', value === '__none__' ? '' : value)
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select location (optional)" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">Not set</SelectItem>
                  {SITE_LOCATIONS.map((location) => (
                    <SelectItem key={location} value={location}>
                      {location}
                    </SelectItem>
                  ))}
                  {device?.location &&
                  !SITE_LOCATIONS.includes(device.location as (typeof SITE_LOCATIONS)[number]) ? (
                    <SelectItem value={device.location}>{device.location}</SelectItem>
                  ) : null}
                </SelectContent>
              </Select>
            </div>
          </fieldset>

          <fieldset className="space-y-3 rounded-xl border border-border/60 bg-secondary/20 p-4">
            <legend className="px-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              SSH credentials (optional)
            </legend>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="vendor">Vendor</Label>
                <Input id="vendor" {...form.register('vendor')} placeholder="e.g. cisco_ios" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="ssh-username">SSH Username</Label>
                <Input
                  id="ssh-username"
                  autoComplete="username"
                  {...form.register('username')}
                  placeholder="Optional SSH username"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between gap-4">
                <Label htmlFor="ssh-password">SSH Password</Label>
                {device?.credentials?.sshPasswordConfigured ? (
                  <p className="text-xs text-muted-foreground">Password Configured</p>
                ) : null}
              </div>
              <div className="relative">
                <Input
                  id="ssh-password"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete={device ? 'new-password' : 'current-password'}
                  {...form.register('password')}
                  placeholder={
                    device ? 'Leave blank to keep current password' : 'Optional SSH password'
                  }
                  className="pr-10"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-0 top-1/2 -translate-y-1/2"
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  onClick={() => setShowPassword((s) => !s)}
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </Button>
              </div>
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between gap-4">
                <Label htmlFor="ssh-enable-secret">Enable Password</Label>
                {device?.credentials?.sshSecretConfigured ? (
                  <p className="text-xs text-muted-foreground">Enable Password Configured</p>
                ) : null}
              </div>
              <div className="relative">
                <Input
                  id="ssh-enable-secret"
                  type={showEnableSecret ? 'text' : 'password'}
                  autoComplete="new-password"
                  {...form.register('enableSecret')}
                  placeholder={
                    device
                      ? 'Leave blank to keep current enable password'
                      : 'Optional enable password'
                  }
                  className="pr-10"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-0 top-1/2 -translate-y-1/2"
                  aria-label={showEnableSecret ? 'Hide enable password' : 'Show enable password'}
                  onClick={() => setShowEnableSecret((s) => !s)}
                >
                  {showEnableSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </Button>
              </div>
            </div>
          </fieldset>

          <fieldset className="space-y-3 rounded-xl border border-border/60 bg-secondary/20 p-4">
            <legend className="px-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              iLO credentials (optional)
            </legend>
            <div className="space-y-1.5">
              <Label htmlFor="ilo-username">iLO Username</Label>
              <Input
                id="ilo-username"
                autoComplete="off"
                {...form.register('iloUsername')}
                placeholder="Optional iLO username"
              />
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between gap-4">
                <Label htmlFor="ilo-password">iLO Password</Label>
                {device?.credentials?.iloPasswordConfigured ? (
                  <p className="text-xs text-muted-foreground">Password Configured</p>
                ) : null}
              </div>
              <div className="relative">
                <Input
                  id="ilo-password"
                  type={showIloPassword ? 'text' : 'password'}
                  autoComplete="new-password"
                  {...form.register('iloPassword')}
                  placeholder={
                    device ? 'Leave blank to keep current iLO password' : 'Optional iLO password'
                  }
                  className="pr-10"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-0 top-1/2 -translate-y-1/2"
                  aria-label={showIloPassword ? 'Hide iLO password' : 'Show iLO password'}
                  onClick={() => setShowIloPassword((s) => !s)}
                >
                  {showIloPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </Button>
              </div>
            </div>
          </fieldset>

          <fieldset className="space-y-3 rounded-xl border border-border/60 bg-secondary/20 p-4">
            <legend className="px-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Monitoring
            </legend>
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
                  disabled={isIloOnly}
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
              {isServerType ? (
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={form.watch('showOnDashboard')}
                    onCheckedChange={(checked) =>
                      form.setValue('showOnDashboard', Boolean(checked))
                    }
                  />
                  Show on dashboard
                </label>
              ) : null}
            </div>
            {isIloOnly ? (
              <p className="text-xs text-muted-foreground">
                iLO-only devices cannot be ping-monitored. Monitoring stays off until an OS IP is
                set.
              </p>
            ) : isServerType ? (
              <p className="text-xs text-muted-foreground">
                When enabled, this server appears under Site Monitoring for the selected location.
              </p>
            ) : null}
          </fieldset>

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
