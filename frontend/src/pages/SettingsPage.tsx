import { useEffect } from 'react'
import { Navigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import { Mail, Save, Timer } from 'lucide-react'
import { useAuth } from '@/auth/AuthContext'
import { useSettingsMutation, useSettingsQuery } from '@/hooks/queries'
import { ErrorState } from '@/components/shared/ErrorState'
import { LoadingState } from '@/components/shared/LoadingState'
import { PageHeader } from '@/components/shared/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

const schema = z.object({
  pingInterval: z.number().min(5),
  pingTimeoutMs: z.number().min(100),
  pingRetries: z.number().min(1),
  smtpEnabled: z.boolean(),
  smtpHost: z.string(),
  smtpPort: z.number(),
  smtpUser: z.string(),
  smtpPassword: z.string(),
  smtpFrom: z.string(),
  smtpTo: z.string(),
  useTls: z.boolean(),
})

type FormValues = z.infer<typeof schema>

export function SettingsPage() {
  const { isAdmin } = useAuth()
  const settingsQuery = useSettingsQuery(isAdmin)
  const save = useSettingsMutation()

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      pingInterval: 30,
      pingTimeoutMs: 1000,
      pingRetries: 3,
      smtpEnabled: true,
      smtpHost: '',
      smtpPort: 587,
      smtpUser: '',
      smtpPassword: '',
      smtpFrom: '',
      smtpTo: '',
      useTls: true,
    },
  })

  useEffect(() => {
    const data = settingsQuery.data
    if (!data) return
    form.reset({
      pingInterval: data.pingInterval,
      pingTimeoutMs: data.pingTimeoutMs,
      pingRetries: data.pingRetries,
      smtpEnabled: data.smtp.enabled,
      smtpHost: data.smtp.host,
      smtpPort: data.smtp.port,
      smtpUser: data.smtp.user,
      smtpPassword: '',
      smtpFrom: data.smtp.fromAddress,
      smtpTo: data.smtp.toAddress,
      useTls: data.smtp.useTls,
    })
  }, [settingsQuery.data, form])

  if (!isAdmin) return <Navigate to="/" replace />

  if (settingsQuery.isLoading && !settingsQuery.data) {
    return <LoadingState label="Loading settings…" />
  }

  if (settingsQuery.error && !settingsQuery.data) {
    return (
      <ErrorState
        message={settingsQuery.error instanceof Error ? settingsQuery.error.message : 'Failed to load'}
        onRetry={() => void settingsQuery.refetch()}
      />
    )
  }

  const onSubmit = form.handleSubmit(async (values) => {
    const smtp: Record<string, unknown> = {
      enabled: values.smtpEnabled,
      host: values.smtpHost,
      port: values.smtpPort,
      user: values.smtpUser,
      fromAddress: values.smtpFrom,
      toAddress: values.smtpTo,
      useTls: values.useTls,
    }
    if (values.smtpPassword.trim()) smtp.password = values.smtpPassword.trim()

    await save.mutateAsync({
      pingInterval: values.pingInterval,
      pingTimeoutMs: values.pingTimeoutMs,
      pingRetries: values.pingRetries,
      smtp,
    })
    form.setValue('smtpPassword', '')
  })

  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        description="Configure global ping parameters and SMTP email alerts"
      />

      <form className="space-y-6" onSubmit={(e) => void onSubmit(e)}>
        <Card className="glass">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Timer className="h-5 w-5 text-primary" />
              Ping monitoring
            </CardTitle>
            <CardDescription>
              Global defaults for automatic device checks. Per-device overrides can be set when editing a device.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="pingInterval">Interval (seconds)</Label>
              <Input
                id="pingInterval"
                type="number"
                min={5}
                {...form.register('pingInterval', { valueAsNumber: true })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="pingTimeoutMs">Timeout (ms)</Label>
              <Input
                id="pingTimeoutMs"
                type="number"
                min={100}
                {...form.register('pingTimeoutMs', { valueAsNumber: true })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="pingRetries">Retry count</Label>
              <Input
                id="pingRetries"
                type="number"
                min={1}
                {...form.register('pingRetries', { valueAsNumber: true })}
              />
            </div>
          </CardContent>
        </Card>

        <Card className="glass">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Mail className="h-5 w-5 text-primary" />
              SMTP alerts
            </CardTitle>
            <CardDescription>
              Email notifications for critical offline devices. Leave password blank to keep the current value.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={form.watch('smtpEnabled')}
                onCheckedChange={(checked) => form.setValue('smtpEnabled', Boolean(checked))}
              />
              Enable email alerts for critical offline devices
            </label>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="smtpHost">SMTP host</Label>
                <Input id="smtpHost" {...form.register('smtpHost')} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="smtpPort">Port</Label>
                <Input id="smtpPort" type="number" {...form.register('smtpPort', { valueAsNumber: true })} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="smtpUser">Username</Label>
                <Input id="smtpUser" {...form.register('smtpUser')} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="smtpPassword">
                  Password {settingsQuery.data?.smtp.passwordSet ? '(set — leave blank to keep)' : ''}
                </Label>
                <Input
                  id="smtpPassword"
                  type="password"
                  placeholder={settingsQuery.data?.smtp.passwordSet ? '••••••••' : ''}
                  {...form.register('smtpPassword')}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="smtpFrom">From address</Label>
                <Input id="smtpFrom" {...form.register('smtpFrom')} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="smtpTo">Alert recipient</Label>
                <Input id="smtpTo" {...form.register('smtpTo')} />
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={form.watch('useTls')}
                onCheckedChange={(checked) => form.setValue('useTls', Boolean(checked))}
              />
              Use TLS
            </label>
          </CardContent>
        </Card>

        <div className="flex justify-end">
          <Button type="submit" disabled={save.isPending}>
            <Save className="h-4 w-4" />
            {save.isPending ? 'Saving…' : 'Save settings'}
          </Button>
        </div>
      </form>
    </div>
  )
}
