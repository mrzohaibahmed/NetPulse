import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import {
  Bell,
  Clock,
  LogOut,
  Moon,
  RefreshCw,
  Sun,
  User,
} from 'lucide-react'
import { useEffect, useState, useRef } from 'react'
import { toast } from 'sonner'
import { useAuth } from '@/shared/auth/AuthContext'
import { useAlertsQuery, useHealthQuery, DASHBOARD_ACTIVE_ALERTS_LIMIT } from '@/hooks/queries'
import { refreshMonitoringQueries } from '@/utils/refreshMonitoringQueries'
import { useTheme } from '@/lib/theme'
import { formatRelative } from '@/utils/format'
import { Avatar, AvatarFallback } from '@/shared/ui/avatar'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip'

interface TopNavbarProps {
  lastUpdated?: number | null
  monitoringOk?: boolean | null
}

export function TopNavbar({ lastUpdated, monitoringOk }: TopNavbarProps) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { user, logout } = useAuth()
  const { theme, toggleTheme } = useTheme()
  const health = useHealthQuery()
  const alerts = useAlertsQuery('active', DASHBOARD_ACTIVE_ALERTS_LIMIT)
  const [now, setNow] = useState(() => new Date())
  const [refreshing, setRefreshing] = useState(false)

  const alertCount = alerts.data?.total ?? alerts.data?.count ?? 0
  const apiOk = monitoringOk ?? (health.isError ? false : health.data ? true : null)
  const updatedLabel = lastUpdated
    ? formatRelative(new Date(lastUpdated).toISOString())
    : health.dataUpdatedAt
      ? formatRelative(new Date(health.dataUpdatedAt).toISOString())
      : null

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(id)
  }, [])

  const lastAlertIdRef = useRef<string | null>(null)

  useEffect(() => {
    if (alerts.data?.data && alerts.data.data.length > 0) {
      const latestAlert = alerts.data.data[0]
      if (lastAlertIdRef.current && lastAlertIdRef.current !== latestAlert._id) {
        if (latestAlert.severity === 'CRITICAL' || latestAlert.severity === 'WARNING') {
          toast.error(latestAlert.title || 'New Alert', {
            description: latestAlert.message,
            duration: 10000,
          })
        } else {
          toast.info(latestAlert.title || 'New Alert', {
            description: latestAlert.message,
            duration: 10000,
          })
        }
      }
      lastAlertIdRef.current = latestAlert._id
    }
  }, [alerts.data])

  const onRefresh = async () => {
    setRefreshing(true)
    try {
      await refreshMonitoringQueries(qc)
    } finally {
      setRefreshing(false)
    }
  }

  const initials = (user?.username || 'U').slice(0, 2).toUpperCase()
  const dateLabel = now.toLocaleDateString(undefined, {
    weekday: 'short',
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
  const timeLabel = now.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })

  return (
    <header className="sticky top-0 z-30 flex h-14 min-w-0 items-center gap-2 border-b border-border/80 bg-background/80 px-4 backdrop-blur-md sm:gap-3 md:h-16 md:px-6">
      <div
        className="ml-10 flex min-w-0 items-center gap-2 rounded-lg border border-border/60 bg-card/60 px-2.5 py-1.5 text-xs md:ml-0"
        aria-live="polite"
        aria-label={`Current date and time: ${dateLabel}, ${timeLabel}`}
      >
        <Clock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <span className="hidden truncate text-muted-foreground sm:inline">{dateLabel}</span>
        <span className="hidden text-border sm:inline" aria-hidden>
          ·
        </span>
        <span className="tabular-nums font-medium text-foreground">{timeLabel}</span>
      </div>

      <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
        <div className="mr-1 hidden items-center gap-2 rounded-lg border border-border/60 bg-card/60 px-2.5 py-1.5 text-xs lg:flex">
          <span
            className={`h-2 w-2 rounded-full ${
              apiOk === true
                ? 'animate-pulse bg-success shadow-[0_0_0_3px_rgba(34,197,94,0.2)]'
                : apiOk === false
                  ? 'bg-danger'
                  : 'bg-slate-500'
            }`}
          />
          <span className="text-muted-foreground">
            {apiOk === true ? 'Monitoring active' : apiOk === false ? 'Monitoring down' : 'Checking…'}
          </span>
          {updatedLabel ? (
            <>
              <span className="text-border">·</span>
              <span className="text-muted-foreground">Updated {updatedLabel}</span>
            </>
          ) : null}
        </div>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => void onRefresh()}
              aria-label="Refresh data"
              disabled={refreshing}
            >
              <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Refresh</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button type="button" variant="ghost" size="icon" onClick={toggleTheme} aria-label="Toggle theme">
              {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{theme === 'dark' ? 'Light mode' : 'Dark mode'}</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="relative"
              onClick={() => navigate('/alerts')}
              aria-label={
                alertCount > 0
                  ? `View alerts, ${alertCount} active`
                  : 'View alerts'
              }
            >
              <Bell className="h-4 w-4" />
              {alertCount > 0 ? (
                <Badge
                  variant="danger"
                  className="absolute -right-0.5 -top-0.5 h-4 min-w-4 justify-center rounded-full px-1 text-[10px]"
                >
                  {alertCount > 99 ? '99+' : alertCount}
                </Badge>
              ) : null}
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            {alertCount > 0
              ? `${alertCount} active alert${alertCount === 1 ? '' : 's'}`
              : 'No active alerts'}
          </TooltipContent>
        </Tooltip>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button type="button" variant="ghost" className="relative h-9 gap-2 rounded-full px-1.5">
              <Avatar className="h-8 w-8">
                <AvatarFallback>{initials}</AvatarFallback>
              </Avatar>
              <span className="hidden max-w-[100px] truncate text-sm font-medium sm:inline">
                {user?.username}
              </span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-52">
            <DropdownMenuLabel>
              <div className="flex flex-col">
                <span>{user?.username}</span>
                <span className="text-xs font-normal capitalize text-muted-foreground">{user?.role}</span>
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => navigate('/account')}>
              <User className="h-4 w-4" />
              Account
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={logout}>
              <LogOut className="h-4 w-4" />
              Log out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  )
}
