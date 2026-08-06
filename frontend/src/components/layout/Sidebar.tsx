import { useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Activity,
  Bell,
  CloudLightning,
  FileBarChart,
  History,
  LayoutDashboard,
  Menu,
  Network,
  Pin,
  PinOff,
  Radar,
  Server,
  Settings,
  Shield,
  UserCircle,
  X,
} from 'lucide-react'
import { useAuth } from '@/auth/AuthContext'
import { useHealthQuery } from '@/hooks/queries'
import { cn } from '@/lib/utils'
import { useNavMode, type NavMode } from '@/lib/navMode'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

interface SidebarProps {
  pinned: boolean
  onPinnedChange: (value: boolean) => void
  mobileOpen: boolean
  onMobileOpenChange: (value: boolean) => void
}

type NavItem = {
  to: string
  label: string
  icon: typeof LayoutDashboard
  end?: boolean
  adminOnly?: boolean
}

/** Ping Monitoring mode — device reachability workflow. */
const PING_MONITOR_ITEMS: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/devices', label: 'Devices', icon: Server },
  { to: '/discovery', label: 'Discovery', icon: Radar, adminOnly: true },
  { to: '/alerts', label: 'Alerts', icon: Bell },
]

const PING_ANALYZE_ITEMS: NavItem[] = [
  { to: '/history', label: 'History', icon: History },
  { to: '/reports', label: 'Reports', icon: FileBarChart },
]

/** Storm Detection mode — switch storm-protection workflow. */
const STORM_ITEMS: NavItem[] = [
  { to: '/storm/dashboard', label: 'Storm Dashboard', icon: LayoutDashboard, end: true },
  { to: '/interfaces', label: 'Interfaces', icon: Network },
  { to: '/storm', label: 'Storm Protection', icon: Shield, end: true },
  { to: '/alerts', label: 'Alerts', icon: Bell },
]

/** Shared across both modes. */
const ADMIN_ITEMS: NavItem[] = [
  { to: '/account', label: 'Account', icon: UserCircle },
  { to: '/settings', label: 'Settings', icon: Settings, adminOnly: true },
]

function ModeSwitcher({ collapsed }: { collapsed: boolean }) {
  const { mode, setMode } = useNavMode()
  const navigate = useNavigate()

  const switchTo = (next: NavMode) => {
    if (next !== mode) {
      setMode(next)
      navigate(next === 'storm' ? '/storm/dashboard' : '/')
    }
  }

  if (collapsed) {
    return (
      <div className="flex flex-col items-center gap-1.5 px-2 pb-3">
        <Tooltip delayDuration={0}>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={() => switchTo('ping')}
              aria-label="Switch to Ping Monitoring mode"
              aria-pressed={mode === 'ping'}
              className={cn(
                'flex h-9 w-9 items-center justify-center rounded-lg transition-colors duration-200',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                mode === 'ping'
                  ? 'bg-primary text-primary-foreground'
                  : 'text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-foreground',
              )}
            >
              <Activity className="h-4 w-4" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="right">Ping Monitoring</TooltipContent>
        </Tooltip>
        <Tooltip delayDuration={0}>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={() => switchTo('storm')}
              aria-label="Switch to Storm Detection mode"
              aria-pressed={mode === 'storm'}
              className={cn(
                'flex h-9 w-9 items-center justify-center rounded-lg transition-colors duration-200',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                mode === 'storm'
                  ? 'bg-primary text-primary-foreground'
                  : 'text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-foreground',
              )}
            >
              <CloudLightning className="h-4 w-4" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="right">Storm Detection</TooltipContent>
        </Tooltip>
      </div>
    )
  }

  return (
    <div className="px-3 pb-3">
      <div className="flex rounded-lg bg-sidebar-accent/50 p-1" role="tablist" aria-label="Navigation mode">
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'ping'}
          onClick={() => switchTo('ping')}
          className={cn(
            'flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-semibold transition-colors duration-200',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            mode === 'ping'
              ? 'bg-primary text-primary-foreground shadow-sm'
              : 'text-sidebar-foreground/60 hover:text-sidebar-foreground',
          )}
        >
          <Activity className="h-3.5 w-3.5" />
          Ping Monitoring
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'storm'}
          onClick={() => switchTo('storm')}
          className={cn(
            'flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-semibold transition-colors duration-200',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            mode === 'storm'
              ? 'bg-primary text-primary-foreground shadow-sm'
              : 'text-sidebar-foreground/60 hover:text-sidebar-foreground',
          )}
        >
          <CloudLightning className="h-3.5 w-3.5" />
          Storm Detection
        </button>
      </div>
    </div>
  )
}

export function Sidebar({ pinned, onPinnedChange, mobileOpen, onMobileOpenChange }: SidebarProps) {
  const [hovered, setHovered] = useState(false)
  const collapsed = !pinned && !hovered
  const { isAdmin, user, logout } = useAuth()
  const { mode } = useNavMode()
  const health = useHealthQuery()
  const apiOk = health.isError ? false : health.data ? true : null
  const dbOk = health.data ? health.data.database === 'Connected' : health.isError ? false : null

  const filterItems = (items: NavItem[]) => items.filter((item) => !item.adminOnly || isAdmin)

  const location = useLocation()
  const isItemActive = (item: NavItem) =>
    item.end ? location.pathname === item.to : location.pathname.startsWith(item.to)

  const NavSection = ({
    title,
    items,
  }: {
    title: string
    items: NavItem[]
  }) => (
    <div className="space-y-1">
      {!collapsed ? (
        <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
          {title}
        </p>
      ) : null}
      {items.map((item) => {
        const Icon = item.icon
        // className is resolved to a plain string here (via our own
        // location-based isActive check) rather than passed as NavLink's
        // function-form className={({isActive}) => ...}. Radix's
        // TooltipTrigger asChild (used below for the collapsed sidebar)
        // clones this element and merges its className by reading
        // child.props.className directly, before NavLink ever gets a
        // chance to invoke a function form — so a function there gets
        // silently stringified into the class attribute instead of
        // resolved, breaking every class on the link. The children
        // render-prop form (below) is unaffected since Slot doesn't need
        // to merge children, so it's left as-is.
        const active = isItemActive(item)
        const link = (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={() => onMobileOpenChange(false)}
            className={cn(
              'group relative flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-sidebar-foreground/70 transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              active && 'bg-primary/15 text-white',
              collapsed && 'justify-center px-2',
            )}
          >
            {({ isActive }) => (
              <>
                {isActive ? (
                  <motion.span
                    layoutId="nav-indicator"
                    className="absolute left-0 top-1/2 h-6 w-1 -translate-y-1/2 rounded-r-full bg-primary"
                  />
                ) : null}
                <Icon className="h-4.5 w-4.5 shrink-0" />
                {!collapsed ? <span>{item.label}</span> : null}
              </>
            )}
          </NavLink>
        )

        if (collapsed) {
          return (
            <Tooltip key={item.to} delayDuration={0}>
              <TooltipTrigger asChild>{link}</TooltipTrigger>
              <TooltipContent side="right">{item.label}</TooltipContent>
            </Tooltip>
          )
        }
        return link
      })}
    </div>
  )

  const content = (
    <aside
      className={cn(
        'flex h-full flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-200 ease-in-out',
        collapsed ? 'w-[72px]' : 'w-[248px]',
      )}
    >
      <div className={cn('flex items-center gap-3 px-4 py-5', collapsed && 'justify-center px-2')}>
        <div className="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-primary to-cyan-500 shadow-lg shadow-primary/30">
          <Activity className="h-5 w-5 text-white" />
          <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 animate-pulse rounded-full bg-success ring-2 ring-sidebar" />
        </div>
        <AnimatePresence initial={false}>
          {!collapsed ? (
            <motion.div
              initial={{ opacity: 0, width: 0 }}
              animate={{ opacity: 1, width: 'auto' }}
              exit={{ opacity: 0, width: 0 }}
              className="flex flex-1 items-center justify-between overflow-hidden"
            >
              <div>
                <p className="text-lg font-bold tracking-tight text-white">NetPulse</p>
                <p className="text-xs text-muted-foreground">Network Monitor</p>
              </div>
              <Tooltip delayDuration={0}>
                <TooltipTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 shrink-0 text-muted-foreground hover:text-white"
                    onClick={() => onPinnedChange(!pinned)}
                    aria-label={pinned ? 'Unpin sidebar' : 'Pin sidebar'}
                  >
                    {pinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="right">{pinned ? 'Unpin' : 'Pin'}</TooltipContent>
              </Tooltip>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>

      <ModeSwitcher collapsed={collapsed} />

      <nav className="flex-1 space-y-5 overflow-y-auto px-2 pb-4" aria-label="Primary">
        {mode === 'ping' ? (
          <>
            <NavSection title="Monitor" items={filterItems(PING_MONITOR_ITEMS)} />
            <NavSection title="Analyze" items={filterItems(PING_ANALYZE_ITEMS)} />
          </>
        ) : (
          <NavSection title="Storm Detection" items={filterItems(STORM_ITEMS)} />
        )}
        <NavSection title="Admin" items={filterItems(ADMIN_ITEMS)} />
      </nav>

      <div className="space-y-3 border-t border-sidebar-border p-3">
        <div className={cn('space-y-2 rounded-lg bg-sidebar-accent/50 p-3 text-xs', collapsed && 'px-2')}>
          <HealthRow label="API" ok={apiOk} collapsed={collapsed} />
          <HealthRow label="DB" ok={dbOk} collapsed={collapsed} />
        </div>

        {!collapsed ? (
          <div className="flex items-center justify-between gap-2 rounded-lg bg-sidebar-accent/40 px-3 py-2">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-white">{user?.username}</p>
              <p className="text-xs capitalize text-muted-foreground">{user?.role}</p>
            </div>
            <Button type="button" variant="ghost" size="sm" onClick={logout} className="text-xs">
              Log out
            </Button>
          </div>
        ) : null}

      </div>
    </aside>
  )

  return (
    <>
      {/* Desktop */}
      <div
        className="sticky top-0 hidden h-screen shrink-0 md:block"
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        {content}
      </div>

      {/* Mobile toggle */}
      <Button
        type="button"
        variant="secondary"
        size="icon"
        className="fixed left-3 top-3 z-40 md:hidden"
        onClick={() => onMobileOpenChange(true)}
        aria-label="Open navigation"
      >
        <Menu className="h-4 w-4" />
      </Button>

      {/* Mobile drawer */}
      <AnimatePresence>
        {mobileOpen ? (
          <>
            <motion.div
              className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm md:hidden"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => onMobileOpenChange(false)}
            />
            <motion.div
              className="fixed inset-y-0 left-0 z-50 md:hidden"
              initial={{ x: -280 }}
              animate={{ x: 0 }}
              exit={{ x: -280 }}
              transition={{ type: 'spring', stiffness: 320, damping: 32 }}
            >
              <div className="relative h-full w-[248px]">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-2 top-2 z-10"
                  onClick={() => onMobileOpenChange(false)}
                  aria-label="Close navigation"
                >
                  <X className="h-4 w-4" />
                </Button>
                <div className="h-full [&_aside]:w-full">{content}</div>
              </div>
            </motion.div>
          </>
        ) : null}
      </AnimatePresence>
    </>
  )
}

function HealthRow({
  label,
  ok,
  collapsed,
}: {
  label: string
  ok: boolean | null
  collapsed: boolean
}) {
  return (
    <div className={cn('flex items-center gap-2', collapsed && 'justify-center')}>
      <span
        className={cn(
          'h-2 w-2 rounded-full',
          ok === true && 'bg-success shadow-[0_0_0_3px_rgba(34,197,94,0.2)]',
          ok === false && 'bg-danger',
          ok === null && 'bg-slate-500',
        )}
      />
      {!collapsed ? (
        <span className="text-sidebar-foreground/80">
          {label} {ok === null ? '…' : ok ? (label === 'API' ? 'online' : 'connected') : label === 'API' ? 'down' : 'disconnected'}
        </span>
      ) : null}
    </div>
  )
}
