import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Activity,
  Bell,
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
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

interface SidebarProps {
  pinned: boolean
  onPinnedChange: (value: boolean) => void
  mobileOpen: boolean
  onMobileOpenChange: (value: boolean) => void
}

export function Sidebar({ pinned, onPinnedChange, mobileOpen, onMobileOpenChange }: SidebarProps) {
  const [hovered, setHovered] = useState(false)
  const collapsed = !pinned && !hovered
  const { isAdmin, user, logout } = useAuth()
  const health = useHealthQuery()
  const apiOk = health.isError ? false : health.data ? true : null
  const dbOk = health.data ? health.data.database === 'Connected' : health.isError ? false : null

  type NavItem = {
    to: string
    label: string
    icon: typeof LayoutDashboard
    end?: boolean
    adminOnly?: boolean
  }

  const monitorItems: NavItem[] = [
    { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
    { to: '/devices', label: 'Devices', icon: Server },
    { to: '/interfaces', label: 'Interfaces', icon: Network },
    { to: '/storm', label: 'Storm Protection', icon: Shield },
    { to: '/alerts', label: 'Alerts', icon: Bell },
    { to: '/discovery', label: 'Discovery', icon: Radar, adminOnly: true },
  ]

  const analyzeItems: NavItem[] = [
    { to: '/history', label: 'History', icon: History },
    { to: '/reports', label: 'Reports', icon: FileBarChart },
  ]

  const adminItems: NavItem[] = [
    { to: '/account', label: 'Account', icon: UserCircle },
    { to: '/settings', label: 'Settings', icon: Settings, adminOnly: true },
  ]

  const filterItems = (items: NavItem[]) => items.filter((item) => !item.adminOnly || isAdmin)

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
        const link = (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={() => onMobileOpenChange(false)}
            className={({ isActive }) =>
              cn(
                'group relative flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-sidebar-foreground/70 transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground',
                isActive && 'bg-primary/15 text-white',
                collapsed && 'justify-center px-2',
              )
            }
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

      <nav className="flex-1 space-y-5 overflow-y-auto px-2 pb-4" aria-label="Primary">
        <NavSection title="Monitor" items={filterItems(monitorItems)} />
        <NavSection title="Analyze" items={filterItems(analyzeItems)} />
        <NavSection title="Admin" items={filterItems(adminItems)} />
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
