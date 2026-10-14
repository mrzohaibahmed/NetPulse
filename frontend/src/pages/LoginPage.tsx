import { useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowRight, Eye, EyeOff, Loader2, Lock, User } from 'lucide-react'
import { useAuth } from '@/shared/auth/AuthContext'
import { Button } from '@/shared/ui/button'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'

export function LoginPage() {
  const { user, loading, login } = useAuth()

  const [username, setUsername] = useState('')
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
    <main className="min-h-screen overflow-x-hidden bg-[#061426] text-slate-100 lg:grid lg:h-screen lg:grid-cols-[minmax(0,63fr)_minmax(0,37fr)] lg:overflow-hidden">
      {/* Image panel — full height on desktop, short banner on mobile. */}
      <section className="relative h-[clamp(13rem,34vh,18rem)] overflow-hidden border-b border-white/10 lg:h-full lg:border-b-0 lg:border-r">
        <img
          src="/login-network.jpeg"
          alt="Network operations monitoring screens"
          className="absolute inset-0 h-full w-full object-cover object-center lg:object-[58%_center]"
        />
        <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(2,9,22,0.42)_0%,rgba(2,9,22,0.08)_48%,rgba(2,9,22,0.9)_100%)] lg:bg-[linear-gradient(180deg,rgba(2,9,22,0.14)_0%,rgba(2,9,22,0.04)_58%,rgba(2,9,22,0.88)_100%)]" />

        <div className="absolute left-5 top-5 flex items-center gap-3 sm:left-8 sm:top-7 lg:left-[5.5%] lg:top-[5.5%] lg:items-start lg:gap-4">
          <svg
            viewBox="0 0 112 64"
            className="h-10 w-12 shrink-0 overflow-visible text-cyan-400 drop-shadow-[0_0_12px_rgba(34,211,238,0.35)] lg:h-16 lg:w-[5.5rem]"
            aria-hidden="true"
          >
            <path
              d="M2 34h22l9-20 12 40L58 3l14 51 9-20h29"
              fill="none"
              stroke="currentColor"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="7"
            />
          </svg>
          <div className="drop-shadow-md lg:pt-1.5">
            <p className="text-xl font-bold tracking-tight text-white lg:text-[2.625rem] lg:leading-none">
              Net<span className="text-cyan-400">Pulse</span>
            </p>
            <p className="mt-0.5 text-[11px] font-medium text-slate-200 lg:mt-2.5 lg:text-lg">
              Enterprise Network Monitoring
            </p>
          </div>
        </div>

        <div className="absolute bottom-5 left-5 right-5 sm:bottom-7 sm:left-8 lg:bottom-[6%] lg:left-[5%] lg:right-[3%]">
          <h1 className="text-2xl font-bold leading-tight tracking-tight text-white drop-shadow-md lg:text-5xl xl:text-[3.5rem]">
            Your network. One clear view.
          </h1>
          <p className="mt-1.5 text-sm leading-relaxed text-blue-100/80 drop-shadow-md lg:mt-2.5 lg:text-2xl">
            Monitor connectivity and devices across every location.
          </p>
        </div>
      </section>

      {/* Login panel */}
      <section className="relative flex min-h-[calc(100vh-clamp(13rem,34vh,18rem))] flex-col items-center justify-center bg-[#061426] px-5 py-10 sm:px-8 lg:min-h-0 lg:items-start lg:justify-start lg:pb-10 lg:pl-[clamp(3.5rem,5.5vw,6.5rem)] lg:pr-[clamp(2.5rem,3.5vw,3.75rem)] lg:pt-[clamp(6rem,18vh,10rem)]">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(20,94,180,0.18),transparent_48%),linear-gradient(180deg,#07182d_0%,#061426_100%)]" />
        <div className="relative w-full max-w-[530px]">
          <div className="mb-8 lg:mb-10">
            <h2 className="text-3xl font-bold tracking-tight text-white lg:text-5xl lg:leading-none">Welcome back</h2>
            <p className="mt-2 text-sm text-blue-100/60 lg:mt-3 lg:text-xl">
              Sign in to your NetPulse account.
            </p>
          </div>

          <form className="space-y-5 lg:space-y-6" onSubmit={(e) => void onSubmit(e)}>
            {error ? (
              <motion.div
                initial={{ opacity: 0, y: -8 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2.5 text-sm text-red-400"
                role="alert"
              >
                <Lock className="h-3.5 w-3.5 shrink-0" />
                {error}
              </motion.div>
            ) : null}

            <div className="space-y-2 lg:space-y-3">
              <Label htmlFor="login-username" className="text-slate-200 lg:text-base">Username</Label>
              <div className="relative">
                <User className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-blue-200/65 lg:left-5 lg:h-6 lg:w-6" />
                <Input
                  id="login-username"
                  autoComplete="username"
                  required
                  placeholder="Enter your username"
                  className="h-11 border-blue-200/45 bg-blue-950/10 pl-9 text-slate-100 placeholder:text-blue-100/55 focus-visible:border-cyan-400 focus-visible:ring-cyan-400/20 lg:h-16 lg:rounded-lg lg:pl-16 lg:text-lg"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </div>
            </div>

            <div className="space-y-2 lg:space-y-3">
              <Label htmlFor="login-password" className="text-slate-200 lg:text-base">Password</Label>
              <div className="relative">
                <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-blue-200/65 lg:left-5 lg:h-6 lg:w-6" />
                <Input
                  id="login-password"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="current-password"
                  required
                  placeholder="Enter your password"
                  className="h-11 border-blue-200/45 bg-blue-950/10 pl-9 pr-10 text-slate-100 placeholder:text-blue-100/55 focus-visible:border-cyan-400 focus-visible:ring-cyan-400/20 lg:h-16 lg:rounded-lg lg:pl-16 lg:pr-14 lg:text-lg"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  className="absolute right-2 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md text-blue-200/70 transition-colors hover:bg-blue-900/50 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 lg:right-4 lg:h-10 lg:w-10"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                >
                  {showPassword ? <EyeOff className="h-4 w-4 lg:h-5 lg:w-5" /> : <Eye className="h-4 w-4 lg:h-5 lg:w-5" />}
                </button>
              </div>
            </div>

            <Button
              type="submit"
              className="h-11 w-full bg-gradient-to-r from-blue-500 to-cyan-400 font-semibold text-white shadow-lg shadow-blue-950/30 hover:from-blue-400 hover:to-cyan-300 lg:h-16 lg:text-xl"
              disabled={submitting || loading}
            >
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {submitting ? 'Signing in…' : 'Sign in'}
              {!submitting ? <ArrowRight className="ml-2 size-5 lg:ml-2.5 lg:size-5" /> : null}
            </Button>
          </form>

          <p className="mt-8 text-center text-xs text-blue-100/60 lg:mt-7 lg:text-sm">
            Contact your administrator for account access.
          </p>
        </div>

        <div className="relative mt-12 hidden w-full max-w-[540px] border-t border-blue-100/15 pt-5 text-center text-sm text-blue-100/60 lg:mt-auto lg:block">
          NetPulse <span aria-hidden="true">•</span> Enterprise Network Monitoring
        </div>
      </section>
    </main>
  )
}
