import { useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Activity, Eye, EyeOff, Loader2, Lock, Shield, User, Wifi } from 'lucide-react'
import { useAuth } from '@/auth/AuthContext'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

const FADE_UP = { hidden: { opacity: 0, y: 12 }, show: { opacity: 1, y: 0 } }

/** Demo credential fill buttons — Vite DEV or explicit VITE_DEMO_MODE=true only. */
const SHOW_DEMO_CREDENTIALS =
  import.meta.env.DEV || import.meta.env.VITE_DEMO_MODE === 'true'

export function LoginPage() {
  const { user, loading, login } = useAuth()

  const [username, setUsername] = useState(SHOW_DEMO_CREDENTIALS ? 'admin' : '')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (!loading && user) {
    return <Navigate to={user.mustChangePassword ? '/account' : '/'} replace />
  }

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await login(username.trim(), password)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="relative flex min-h-screen overflow-hidden bg-background">
      {/* Background effects */}
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top_left,_rgba(59,130,246,0.15),_transparent_50%),radial-gradient(ellipse_at_bottom_right,_rgba(34,197,94,0.08),_transparent_45%)]" />

      {/* Left panel — branding */}
      <div className="relative hidden w-[45%] flex-col justify-between overflow-hidden border-r border-border/50 p-10 lg:flex">
        {/* Decorative grid */}
        <div className="pointer-events-none absolute inset-0 opacity-[0.03]" style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg width=\'40\' height=\'40\' xmlns=\'http://www.w3.org/2000/svg\'%3E%3Cpath d=\'M0 0h40v40H0z\' fill=\'none\' stroke=\'%23fff\' stroke-width=\'.5\'/%3E%3C/svg%3E")' }} />
        <div className="pointer-events-none absolute -bottom-32 -left-32 h-96 w-96 rounded-full bg-primary/10 blur-[120px]" />
        <div className="pointer-events-none absolute -right-20 top-1/4 h-72 w-72 rounded-full bg-cyan-500/8 blur-[100px]" />

        {/* Logo */}
        <motion.div
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.5 }}
          className="flex items-center gap-3"
        >
          <div className="relative flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-primary to-cyan-500 shadow-lg shadow-primary/30">
            <Activity className="h-5 w-5 text-white" />
            <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 animate-pulse rounded-full bg-success ring-2 ring-background" />
          </div>
          <div>
            <p className="text-lg font-bold tracking-tight text-foreground">NetPulse</p>
            <p className="text-xs text-muted-foreground">Network Monitor</p>
          </div>
        </motion.div>

        {/* Hero text */}
        <motion.div
          initial="hidden"
          animate="show"
          transition={{ staggerChildren: 0.1, delayChildren: 0.2 }}
          className="max-w-sm space-y-6"
        >
          <motion.h1
            variants={FADE_UP}
            transition={{ duration: 0.5 }}
            className="text-3xl font-bold leading-tight tracking-tight text-foreground"
          >
            Network Operations{' '}
            <span className="bg-gradient-to-r from-primary to-cyan-400 bg-clip-text text-transparent">
              Center
            </span>
          </motion.h1>
          <motion.p variants={FADE_UP} transition={{ duration: 0.5 }} className="text-sm leading-relaxed text-muted-foreground">
            Real-time monitoring, intelligent alerts, and deep visibility across your entire network infrastructure.
          </motion.p>
          <motion.div variants={FADE_UP} transition={{ duration: 0.5 }} className="flex flex-col gap-3 pt-2">
            <FeatureRow icon={<Wifi className="h-3.5 w-3.5" />} text="Live device health & latency tracking" />
            <FeatureRow icon={<Shield className="h-3.5 w-3.5" />} text="Nmap-powered port & service discovery" />
            <FeatureRow icon={<Activity className="h-3.5 w-3.5" />} text="Automated alerts & uptime reporting" />
          </motion.div>
        </motion.div>

        {/* Footer */}
        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.6, duration: 0.5 }}
          className="text-xs text-muted-foreground/60"
        >
          &copy; {new Date().getFullYear()} NetPulse &middot; Enterprise Network Monitoring
        </motion.p>
      </div>

      {/* Right panel — login form */}
      <div className="relative flex flex-1 items-center justify-center px-6 py-10">
        <motion.div
          initial={{ opacity: 0, y: 20, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: 0.4, ease: 'easeOut' }}
          className="w-full max-w-[400px]"
        >
          {/* Mobile logo */}
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <div className="relative flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-primary to-cyan-500 shadow-lg shadow-primary/30">
              <Activity className="h-5 w-5 text-white" />
              <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 animate-pulse rounded-full bg-success ring-2 ring-background" />
            </div>
            <div>
              <p className="text-lg font-bold tracking-tight text-foreground">NetPulse</p>
              <p className="text-xs text-muted-foreground">Network Monitor</p>
            </div>
          </div>

          {/* Form header */}
          <div className="mb-8">
            <h2 className="text-2xl font-bold tracking-tight text-foreground">Welcome back</h2>
            <p className="mt-1.5 text-sm text-muted-foreground">
              Sign in to access the operations center
            </p>
          </div>

          {/* Card */}
          <div className="glass rounded-xl p-6">
            <form className="space-y-5" onSubmit={(e) => void onSubmit(e)}>
              {error ? (
                <motion.div
                  initial={{ opacity: 0, y: -8 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="flex items-center gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2.5 text-sm text-danger"
                  role="alert"
                >
                  <Lock className="h-3.5 w-3.5 shrink-0" />
                  {error}
                </motion.div>
              ) : null}

              <div className="space-y-2">
                <Label htmlFor="login-username">Username</Label>
                <div className="relative">
                  <User className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    id="login-username"
                    autoComplete="username"
                    required
                    placeholder="Enter your username"
                    className="pl-9"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="login-password">Password</Label>
                <div className="relative">
                  <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    id="login-password"
                    type={showPassword ? 'text' : 'password'}
                    autoComplete="current-password"
                    required
                    placeholder="Enter your password"
                    className="pl-9 pr-10"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                  <button
                    type="button"
                    tabIndex={-1}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
                    onClick={() => setShowPassword((v) => !v)}
                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                  >
                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>

              <Button type="submit" className="w-full" disabled={submitting || loading}>
                {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                {submitting ? 'Signing in…' : 'Sign in'}
              </Button>
            </form>
          </div>

          {/* Demo credential fills — excluded from production builds unless VITE_DEMO_MODE=true */}
          {SHOW_DEMO_CREDENTIALS ? (
          <div className="mt-5 rounded-lg border border-border/60 bg-secondary/30 px-4 py-3">
            <p className="mb-2 text-xs font-medium text-muted-foreground">Demo credentials</p>
            <div className="flex flex-col gap-2 sm:flex-row sm:gap-4">
              <button
                type="button"
                className="group flex flex-1 items-center gap-2 rounded-md bg-secondary/50 px-3 py-2 text-left transition-colors hover:bg-secondary"
                onClick={() => { setUsername('superadmin'); setPassword('superadmin123') }}
              >
                <div className="flex h-7 w-7 items-center justify-center rounded-md bg-danger/15 text-danger">
                  <Shield className="h-3.5 w-3.5" />
                </div>
                <div>
                  <p className="text-xs font-semibold text-foreground">Super Admin</p>
                  <p className="text-[10px] text-muted-foreground">Emergency ops</p>
                </div>
              </button>
              <button
                type="button"
                className="group flex flex-1 items-center gap-2 rounded-md bg-secondary/50 px-3 py-2 text-left transition-colors hover:bg-secondary"
                onClick={() => { setUsername('admin'); setPassword('admin123') }}
              >
                <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/15 text-primary">
                  <Shield className="h-3.5 w-3.5" />
                </div>
                <div>
                  <p className="text-xs font-semibold text-foreground">Admin</p>
                  <p className="text-[10px] text-muted-foreground">Full access</p>
                </div>
              </button>
              <button
                type="button"
                className="group flex flex-1 items-center gap-2 rounded-md bg-secondary/50 px-3 py-2 text-left transition-colors hover:bg-secondary"
                onClick={() => { setUsername('viewer'); setPassword('viewer123') }}
              >
                <div className="flex h-7 w-7 items-center justify-center rounded-md bg-success/15 text-success">
                  <Eye className="h-3.5 w-3.5" />
                </div>
                <div>
                  <p className="text-xs font-semibold text-foreground">Viewer</p>
                  <p className="text-[10px] text-muted-foreground">Read only</p>
                </div>
              </button>
            </div>
          </div>
          ) : null}
        </motion.div>
      </div>
    </div>
  )
}

function FeatureRow({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/10 text-primary">
        {icon}
      </div>
      <span className="text-sm text-muted-foreground">{text}</span>
    </div>
  )
}
