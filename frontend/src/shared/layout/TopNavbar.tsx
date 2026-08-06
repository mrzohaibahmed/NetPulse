import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import {
  Bell,
  LogOut,
  Moon,
  RefreshCw,
  Search,
  Sun,
  User,
} from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { useAuth } from '@/shared/auth/AuthContext'
import { useAlertsQuery, useHealthQuery } from '@/hooks/queries'
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
import { Input } from '@/shared/ui/input'
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
  const alerts = useAlertsQuery('active', 10)
  const [query, setQuery] = useState('')
  const [refreshing, setRefreshing] = useState(false)

  const alertCount = alerts.data?.count ?? alerts.data?.data?.length ?? 0
  const apiOk = monitoringOk ?? (health.isError ? false : health.data ? true : null)
  const updatedLabel = lastUpdated
    ? formatRelative(new Date(lastUpdated).toISOString())
    : health.dataUpdatedAt
      ? formatRelative(new Date(health.dataUpdatedAt).toISOString())
      : null

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const q = params.get('q')
    if (q) setQuery(q)
  }, [])

  const onSearch = (event: FormEvent) => {
    event.preventDefault()
    const q = query.trim()
    navigate(q ? `/devices?q=${encodeURIComponent(q)}` : '/devices')
  }

  const onRefresh = async () => {
    setRefreshing(true)
    try {
      await qc.invalidateQueries()
    } finally {
      setRefreshing(false)
    }
  }

  const initials = (user?.username || 'U').slice(0, 2).toUpperCase()

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border/80 bg-background/80 px-4 backdrop-blur-md md:h-16 md:px-6">
      <form onSubmit={onSearch} className="relative ml-10 flex-1 md:ml-0 md:max-w-md">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search devices…"
          className="h-9 pl-9"
          aria-label="Search devices"
        />
      </form>

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
              aria-label="View alerts"
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
          <TooltipContent>Alerts</TooltipContent>
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
