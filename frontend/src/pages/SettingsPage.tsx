import { useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import { Mail, Save, Timer, Activity, Send, CheckCircle2, AlertCircle, Shield, Database } from 'lucide-react'
import { useAuth } from '@/shared/auth/AuthContext'
import { useSettingsMutation, useSettingsQuery } from '@/hooks/queries'
import { IspSettingsSection } from '@/modules/ping/components/IspSettingsSection'
import { ErrorState } from '@/shared/components/ErrorState'
import { LoadingState } from '@/shared/components/LoadingState'
import { PageHeader } from '@/shared/components/PageHeader'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/ui/card'
import { Checkbox } from '@/shared/ui/checkbox'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'
import { apiRequest } from '@/shared/api/client'

// ---------------------------------------------------------------------------
// Provider presets — fill SMTP host/port/TLS when the user picks a provider
// ---------------------------------------------------------------------------
const PROVIDER_PRESETS = {
  gmail: {
    label: 'Gmail',
    host: 'smtp.gmail.com',
    port: 587,
    useTls: true,
  },
  outlook: {
    label: 'Outlook / Microsoft 365',
    host: 'smtp.office365.com',
    port: 587,
    useTls: true,
  },
} as const

type Provider = keyof typeof PROVIDER_PRESETS

const schema = z.object({
  pingInterval: z.number().min(5),
  pingTimeoutMs: z.number().min(100),
  pingRetries: z.number().min(1),
  smtpEnabled: z.boolean(),
  smtpProvider: z.enum(['gmail', 'outlook']),
  smtpHost: z.string(),
  smtpPort: z.number(),
  smtpUser: z.string(),
  smtpPassword: z.string(),
  smtpFrom: z.string(),
  smtpFromName: z.string(),
  smtpTo: z.string(),
  useTls: z.boolean(),
  cooldownMinutes: z.number().min(1),
  stabilizationSeconds: z.number().min(5),
  maximumRecoveryAttempts: z.number().min(1),
  reMitigationThreshold: z.number().min(1).max(100),
  requiredConfirmations: z.number().min(1).max(20),
  pingHistoryRetentionDays: z.number().min(1).max(3650),
  dataRetentionDays: z.number().min(1).max(3650),
  incidentRetentionDays: z.number().min(1).max(3650),
  stormNotificationsEnabled: z.boolean(),
  stormShutdownEmails: z.boolean(),
  stormRecoveryEmails: z.boolean(),
  stormFailureEmails: z.boolean(),
  stormEmailTo: z.string(),
})

type FormValues = z.infer<typeof schema>

export function SettingsPage() {
  const { isAdmin } = useAuth()
  const settingsQuery = useSettingsQuery(isAdmin)
  const save = useSettingsMutation()

  const [testEmailState, setTestEmailState] = useState<'idle' | 'sending' | 'success' | 'error'>('idle')
  const [testEmailMessage, setTestEmailMessage] = useState('')

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      pingInterval: 60,
      pingTimeoutMs: 1000,
      pingRetries: 3,
      smtpEnabled: true,
      smtpProvider: 'gmail',
      smtpHost: '',
      smtpPort: 587,
      smtpUser: '',
      smtpPassword: '',
      smtpFrom: '',
      smtpFromName: 'NetPulse',
      smtpTo: '',
      useTls: true,
      cooldownMinutes: 5,
      stabilizationSeconds: 60,
      maximumRecoveryAttempts: 3,
      reMitigationThreshold: 60,
      requiredConfirmations: 4,
      pingHistoryRetentionDays: 7,
      dataRetentionDays: 90,
      incidentRetentionDays: 365,
      stormNotificationsEnabled: true,
      stormShutdownEmails: true,
      stormRecoveryEmails: true,
      stormFailureEmails: true,
      stormEmailTo: '',
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
      smtpProvider: (data.smtp.provider === 'outlook' ? 'outlook' : 'gmail') as Provider,
      smtpHost: data.smtp.host,
      smtpPort: data.smtp.port,
      smtpUser: data.smtp.user,
      smtpPassword: '',
      smtpFrom: data.smtp.fromAddress,
      smtpFromName: data.smtp.fromName ?? 'NetPulse',
      smtpTo: data.smtp.toAddress,
      useTls: data.smtp.useTls,
      cooldownMinutes: data.cooldownMinutes ?? 5,
      stabilizationSeconds: data.stabilizationSeconds ?? 60,
      maximumRecoveryAttempts: data.maximumRecoveryAttempts ?? 3,
      reMitigationThreshold: data.reMitigationThreshold ?? 60,
      requiredConfirmations: data.requiredConfirmations ?? 4,
      pingHistoryRetentionDays: data.pingHistoryRetentionDays ?? 7,
      dataRetentionDays: data.dataRetentionDays ?? 90,
      incidentRetentionDays: data.incidentRetentionDays ?? 365,
      stormNotificationsEnabled: data.stormNotifications?.enabled ?? true,
      stormShutdownEmails: data.stormNotifications?.shutdownEmails ?? true,
      stormRecoveryEmails: data.stormNotifications?.recoveryEmails ?? true,
      stormFailureEmails: data.stormNotifications?.failureEmails ?? true,
      stormEmailTo: data.stormNotifications?.toAddress ?? '',
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

  const applyProviderPreset = (provider: Provider) => {
    const preset = PROVIDER_PRESETS[provider]
    form.setValue('smtpProvider', provider)
    form.setValue('smtpHost', preset.host)
    form.setValue('smtpPort', preset.port)
    form.setValue('useTls', preset.useTls)
  }

  const handleTestEmail = async () => {
    setTestEmailState('sending')
    setTestEmailMessage('')
    try {
      const res = await apiRequest<{ success: boolean; message: string }>(
        '/api/settings/test-email',
        { method: 'POST' },
      )
      if (res.success) {
        setTestEmailState('success')
        setTestEmailMessage('Test email sent successfully.')
      } else {
        setTestEmailState('error')
        setTestEmailMessage(res.message || 'Failed to send test email.')
      }
    } catch (err) {
      setTestEmailState('error')
      setTestEmailMessage(err instanceof Error ? err.message : 'Failed to send test email.')
    }
  }

  const onSubmit = form.handleSubmit(async (values) => {
    const smtp: Record<string, unknown> = {
      enabled: values.smtpEnabled,
      provider: values.smtpProvider,
      host: values.smtpHost,
      port: values.smtpPort,
      user: values.smtpUser,
      fromAddress: values.smtpFrom,
      fromName: values.smtpFromName,
      toAddress: values.smtpTo,
      useTls: values.useTls,
    }
    if (values.smtpPassword.trim()) smtp.password = values.smtpPassword.trim()

    await save.mutateAsync({
      pingInterval: values.pingInterval,
      pingTimeoutMs: values.pingTimeoutMs,
      pingRetries: values.pingRetries,
      smtp,
      cooldownMinutes: values.cooldownMinutes,
      stabilizationSeconds: values.stabilizationSeconds,
      maximumRecoveryAttempts: values.maximumRecoveryAttempts,
      reMitigationThreshold: values.reMitigationThreshold,
      requiredConfirmations: values.requiredConfirmations,
      pingHistoryRetentionDays: values.pingHistoryRetentionDays,
      dataRetentionDays: values.dataRetentionDays,
      incidentRetentionDays: values.incidentRetentionDays,
      stormNotifications: {
        enabled: values.stormNotificationsEnabled,
        shutdownEmails: values.stormShutdownEmails,
        recoveryEmails: values.stormRecoveryEmails,
        failureEmails: values.stormFailureEmails,
        toAddress: values.stormEmailTo.trim(),
      },
    })
    form.setValue('smtpPassword', '')
    setTestEmailState('idle')
    setTestEmailMessage('')
  })

  const currentProvider = form.watch('smtpProvider')

  return (
    <div className="np-page">
      <PageHeader
        title="Settings"
        description="Configure ISP connectivity, ping monitoring, SMTP, and storm notifications"
      />

      <div className="space-y-6">
        <IspSettingsSection />

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
              Email notifications for critical offline devices. The recipient is independent of the
              provider — any valid email address can receive alerts. Leave password blank to keep
              the current value.
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

            {/* Provider selector */}
            <div className="space-y-2">
              <Label>Email provider</Label>
              <div className="flex flex-wrap gap-3">
                {(Object.keys(PROVIDER_PRESETS) as Provider[]).map((p) => (
                  <label key={p} className="flex items-center gap-2 text-sm cursor-pointer">
                    <input
                      type="radio"
                      name="smtpProvider"
                      value={p}
                      checked={currentProvider === p}
                      onChange={() => applyProviderPreset(p)}
                      className="accent-primary"
                    />
                    {PROVIDER_PRESETS[p].label}
                  </label>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                Selecting a provider fills the SMTP host, port, and security settings automatically.
                You can still override them below.
              </p>
            </div>

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
                  Password {settingsQuery.data?.smtp.passwordSet ? '(configured — leave blank to keep)' : ''}
                </Label>
                <Input
                  id="smtpPassword"
                  type="password"
                  autoComplete="new-password"
                  placeholder={settingsQuery.data?.smtp.passwordSet ? '••••••••' : ''}
                  {...form.register('smtpPassword')}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="smtpFrom">From address (sender)</Label>
                <Input id="smtpFrom" type="email" {...form.register('smtpFrom')} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="smtpFromName">From name</Label>
                <Input id="smtpFromName" placeholder="NetPulse" {...form.register('smtpFromName')} />
              </div>
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="smtpTo">
                  Alert recipient
                  <span className="ml-1.5 text-xs text-muted-foreground font-normal">
                    (any valid email — Gmail, Outlook, or other)
                  </span>
                </Label>
                <Input id="smtpTo" type="email" {...form.register('smtpTo')} />
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={form.watch('useTls')}
                onCheckedChange={(checked) => form.setValue('useTls', Boolean(checked))}
                disabled={form.watch('smtpPort') === 465}
              />
              Use STARTTLS
            </label>
            <p className="text-xs text-muted-foreground -mt-2">
              {form.watch('smtpPort') === 465
                ? 'Port 465 uses SSL/TLS (implicit encryption) automatically. STARTTLS is not used.'
                : 'Port 587 typically uses STARTTLS. Port 465 uses SSL/TLS instead.'}
            </p>

            {/* Test email */}
            <div className="flex items-center gap-3 pt-1">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={testEmailState === 'sending'}
                onClick={() => void handleTestEmail()}
              >
                <Send className="h-3.5 w-3.5" />
                {testEmailState === 'sending' ? 'Sending…' : 'Send test email'}
              </Button>
              {testEmailState === 'success' && (
                <span className="flex items-center gap-1.5 text-sm text-green-600">
                  <CheckCircle2 className="h-4 w-4" />
                  {testEmailMessage}
                </span>
              )}
              {testEmailState === 'error' && (
                <span className="flex items-center gap-1.5 text-sm text-destructive">
                  <AlertCircle className="h-4 w-4" />
                  {testEmailMessage}
                </span>
              )}
            </div>
          </CardContent>
        </Card>

        <Card className="glass">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Mail className="h-5 w-5 text-primary" />
              Storm email notifications
            </CardTitle>
            <CardDescription>
              Automatic emails after verified shutdown/recovery (and failures). Uses the SMTP
              settings above. Leave recipient blank to use the alert recipient.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={form.watch('stormNotificationsEnabled')}
                onCheckedChange={(checked) =>
                  form.setValue('stormNotificationsEnabled', Boolean(checked))
                }
              />
              Enable storm notifications
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={form.watch('stormShutdownEmails')}
                onCheckedChange={(checked) =>
                  form.setValue('stormShutdownEmails', Boolean(checked))
                }
              />
              Enable shutdown emails
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={form.watch('stormRecoveryEmails')}
                onCheckedChange={(checked) =>
                  form.setValue('stormRecoveryEmails', Boolean(checked))
                }
              />
              Enable recovery emails
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={form.watch('stormFailureEmails')}
                onCheckedChange={(checked) =>
                  form.setValue('stormFailureEmails', Boolean(checked))
                }
              />
              Enable failure emails
            </label>
            <div className="space-y-1.5 max-w-md">
              <Label htmlFor="stormEmailTo">Storm notification recipient</Label>
              <Input
                id="stormEmailTo"
                placeholder="Leave blank to use SMTP alert recipient"
                {...form.register('stormEmailTo')}
              />
            </div>
          </CardContent>
        </Card>

        <Card className="glass">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Shield className="h-5 w-5 text-primary" />
              Storm confirmation
            </CardTitle>
            <CardDescription>
              Controls when elevated interface risk is treated as a confirmed storm.
              These values apply to confirmation, safety checks, and re-mitigation.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="reMitigationThreshold">Storm risk threshold (%)</Label>
              <Input
                id="reMitigationThreshold"
                type="number"
                min={1}
                max={100}
                {...form.register('reMitigationThreshold', { valueAsNumber: true })}
              />
              <p className="text-xs text-muted-foreground">
                Risk at or above this score counts as high across confirmation,
                safety validation, and prepare / re-mitigation.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="requiredConfirmations">Required confirmation polls</Label>
              <Input
                id="requiredConfirmations"
                type="number"
                min={1}
                max={20}
                {...form.register('requiredConfirmations', { valueAsNumber: true })}
              />
              <p className="text-xs text-muted-foreground">
                Consecutive high-risk polling cycles required before a storm is
                confirmed for mitigation.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card className="glass">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Activity className="h-5 w-5 text-primary" />
              Recovery protection
            </CardTitle>
            <CardDescription>
              Adjust validation thresholds, recovery limits, and stabilization timing for port automatic recovery.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="cooldownMinutes">Cooldown (minutes)</Label>
              <Input
                id="cooldownMinutes"
                type="number"
                min={1}
                {...form.register('cooldownMinutes', { valueAsNumber: true })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="stabilizationSeconds">Stabilization (seconds)</Label>
              <Input
                id="stabilizationSeconds"
                type="number"
                min={5}
                {...form.register('stabilizationSeconds', { valueAsNumber: true })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="maximumRecoveryAttempts">Max attempts</Label>
              <Input
                id="maximumRecoveryAttempts"
                type="number"
                min={1}
                {...form.register('maximumRecoveryAttempts', { valueAsNumber: true })}
              />
            </div>
          </CardContent>
        </Card>

        <Card className="glass">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database className="h-5 w-5 text-primary" />
              Data retention
            </CardTitle>
            <CardDescription>
              How long historical records are kept before MongoDB TTL indexes remove them automatically.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="pingHistoryRetentionDays">Ping history (days)</Label>
              <Input
                id="pingHistoryRetentionDays"
                type="number"
                min={1}
                max={3650}
                {...form.register('pingHistoryRetentionDays', { valueAsNumber: true })}
              />
              <p className="text-xs text-muted-foreground">
                Manual and automatic ping results in pingHistory.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="dataRetentionDays">Telemetry &amp; storm evaluation (days)</Label>
              <Input
                id="dataRetentionDays"
                type="number"
                min={1}
                max={3650}
                {...form.register('dataRetentionDays', { valueAsNumber: true })}
              />
              <p className="text-xs text-muted-foreground">
                Interface stats and storm eligibility, risk, confirmation, and safety history.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="incidentRetentionDays">Incident actions (days)</Label>
              <Input
                id="incidentRetentionDays"
                type="number"
                min={1}
                max={3650}
                {...form.register('incidentRetentionDays', { valueAsNumber: true })}
              />
              <p className="text-xs text-muted-foreground">
                Mitigation/recovery attempt logs and closed (RESOLVED) storm incidents.
              </p>
            </div>
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
    </div>
  )
}
